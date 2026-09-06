"""Skin Editor (``docs/components/13-editor-core-authoring.md`` §2.4) —
visual authoring for ``ui_skin.json``'s widget-tree format, defined in full
by ``08-ui-runtime.md`` §5.1. This mode edits exactly that schema; it never
invents a parallel widget-tree shape.

This is the **one deliberate exception** to "editor never imports engine's
game loop" (doc §2.4/§10): the live preview pane renders the tree being
edited using the real ``engine.ui.ui_runtime.UIRuntime`` rendering path
(read-only usage — ``create_panel``/``draw``, never the event-bus-wired
input-handling side) so what's shown here is what actually renders
in-game, not a reimplementation that could silently drift. Nothing else in
this editor imports ``engine.ui`` or any other ``engine.*`` gameplay
module.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from editor.modes import EditorContext, SaveAffordanceMixin, TooltipMixin

logger = logging.getLogger(__name__)

_TOOLTIPS = {
    "save_button": "Save this screen into the project's ui_skin.json (Ctrl+S)",
    "add_node_button": "Add a new widget node as a child of the selected node",
    "delete_node_button": "Delete the selected widget node and its children",
    "preview_pane": "Live preview, rendered via the real UIRuntime draw path",
    "color_picker": "Edit a named style color used by 'style'/'color' fields in the tree",
}

_DEFAULT_UI_CONFIG_COLORS = {
    "panel_bg": (30, 30, 40),
    "title": (230, 230, 230),
    "hp_red": (200, 40, 40),
    "text_default": (230, 230, 230),
}

NodePath = tuple[int, ...]


def _node_at(tree: dict[str, Any], path: NodePath) -> dict[str, Any]:
    node = tree
    for index in path:
        node = node["children"][index]
    return node


class SkinEditorMode(TooltipMixin, SaveAffordanceMixin):
    def __init__(self, context: EditorContext) -> None:
        self._context = context
        self.tooltips = dict(_TOOLTIPS)

        self.current_screen_id: str | None = None
        self.current_tree: dict[str, Any] = self._blank_tree("new_screen")
        self._ui_config_cache: dict[str, tuple[int, int, int]] | None = None

        self.dirty = False
        self.save_button_rect: tuple[int, int, int, int] = (0, 0, 80, 24)

    # -- EditorMode protocol --------------------------------------------------

    def on_activate(self) -> None:
        pass

    def on_deactivate(self) -> None:
        pass

    def handle_event(self, event: Any) -> None:
        pass

    def update(self, dt: float) -> None:
        pass

    def draw(self, surface: Any) -> None:
        pygame = _get_pygame()
        if pygame is None:
            return
        try:
            pygame.draw.rect(surface, (60, 120, 60), pygame.Rect(*self.save_button_rect))
            self.render_preview(surface)
        except Exception:
            logger.warning("Skin Editor draw failed", exc_info=True)

    # -- screen construction / loading -----------------------------------------

    def _blank_tree(self, screen_id: str) -> dict[str, Any]:
        return {
            "type": "Panel",
            "id": screen_id,
            "rect": {"x": "center", "y": "center", "w": 400, "h": 300},
            "background": "panel_bg",
            "children": [],
        }

    def new_screen(self, screen_id: str) -> None:
        self.current_screen_id = screen_id
        self.current_tree = self._blank_tree(screen_id)
        self.mark_dirty()

    def load_screen(self, screen_id: str) -> None:
        project = self._require_project()
        ui_skin_path = project.data_path("config", "ui_skin.json")
        config = json.loads(ui_skin_path.read_text(encoding="utf-8")) if ui_skin_path.exists() else {"screens": {}}
        tree = config.get("screens", {}).get(screen_id)
        if tree is None:
            raise KeyError(f"No screen {screen_id!r} in ui_skin.json")
        self.current_screen_id = screen_id
        self.current_tree = tree
        self.mark_saved()

    # -- tree editing -----------------------------------------------------------

    def get_node(self, path: NodePath) -> dict[str, Any]:
        return _node_at(self.current_tree, path)

    def add_node(self, parent_path: NodePath, node: dict[str, Any]) -> None:
        parent = _node_at(self.current_tree, parent_path)
        parent.setdefault("children", []).append(node)
        self.mark_dirty()

    def remove_node(self, path: NodePath) -> None:
        if not path:
            raise ValueError("cannot remove the root node")
        parent = _node_at(self.current_tree, path[:-1])
        parent["children"].pop(path[-1])
        self.mark_dirty()

    def set_field(self, path: NodePath, field: str, value: Any) -> None:
        node = _node_at(self.current_tree, path)
        node[field] = value
        self.mark_dirty()

    def validate(self) -> None:
        """Validate :attr:`current_tree` against the exact widget-tree
        schema ``08-ui-runtime.md`` §5.1 defines. Raises
        ``engine.core.schemas.ui_skin_schema.WidgetTreeError`` on a
        structural problem."""
        from engine.core.schemas.ui_skin_schema import validate_widget_tree

        validate_widget_tree(self.current_tree)

    # -- live preview (the one deliberate engine.ui import) --------------------

    def render_preview(self, surface: Any) -> None:
        """Render :attr:`current_tree` via the real
        ``engine.ui.ui_runtime.UIRuntime`` draw path — read-only usage,
        purely for preview (doc §2.4/§10)."""
        from engine.core.events import EventBus
        from engine.ui.ui_runtime import UIRuntime

        pygame = _get_pygame()
        width = surface.get_width() if pygame is not None else 800
        height = surface.get_height() if pygame is not None else 600

        runtime = UIRuntime(EventBus(), None, screen_rect_provider=lambda full: (0, 0, width, height))
        runtime.create_panel("__skin_editor_preview__", self.current_tree)
        runtime.draw(surface)

    # -- saving -----------------------------------------------------------------

    def save_screen(self) -> Path:
        """Save :attr:`current_tree` into the project's ``ui_skin.json``
        under its own ``screen_id`` key — matching ``08-ui-runtime.md``
        §5.2's file shape byte-for-byte (this mode only ever adds/edits
        entries in ``screens``, never restructures the file)."""
        self.validate()
        project = self._require_project()
        if not self.current_screen_id:
            raise ValueError("Skin Editor has no screen id to save under")

        ui_skin_path = project.data_path("config", "ui_skin.json")
        ui_skin_path.parent.mkdir(parents=True, exist_ok=True)
        if ui_skin_path.exists():
            config = json.loads(ui_skin_path.read_text(encoding="utf-8"))
        else:
            config = {"schema_version": 1, "screens": {}}
        config.setdefault("screens", {})[self.current_screen_id] = self.current_tree
        ui_skin_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

        self.mark_saved()
        return ui_skin_path

    # -- palette color-picker for ui_config.json --------------------------------

    def _load_ui_config_colors(self) -> dict[str, tuple[int, int, int]]:
        if self._ui_config_cache is not None:
            return self._ui_config_cache
        project = self._context.project()
        colors = dict(_DEFAULT_UI_CONFIG_COLORS)
        if project is not None:
            path = project.data_path("config", "ui_config.json")
            if path.exists():
                config = json.loads(path.read_text(encoding="utf-8"))
                for name, rgb in config.get("colors", {}).items():
                    colors[name] = tuple(rgb)
        self._ui_config_cache = colors
        return colors

    def get_color(self, name: str) -> tuple[int, int, int] | None:
        return self._load_ui_config_colors().get(name)

    def set_color(self, name: str, rgb: tuple[int, int, int]) -> None:
        colors = self._load_ui_config_colors()
        colors[name] = tuple(rgb)
        self.mark_dirty()

    def save_ui_config(self) -> Path:
        project = self._require_project()
        path = project.data_path("config", "ui_config.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        colors = self._load_ui_config_colors()
        payload = {"schema_version": 1, "colors": {name: list(rgb) for name, rgb in colors.items()}}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.mark_saved()
        return path

    # -- helpers ------------------------------------------------------------

    def _require_project(self):
        project = self._context.project()
        if project is None:
            raise RuntimeError("Skin Editor has no active project")
        return project


_pygame_module: Any = False


def _get_pygame() -> Any:
    global _pygame_module
    if _pygame_module is False:
        try:
            import pygame

            _pygame_module = pygame
        except ImportError:
            _pygame_module = None
    return _pygame_module
