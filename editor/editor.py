"""The editor application shell (``docs/components/13-editor-core-authoring.md``
§2.2/§2.5) — a standalone pygame-ce application, entirely independent of
the game runtime. Wires the Project Manager and the mode-registration
registry every mode (this component's own six, and Wave 2's
``14-editor-visual-quest-dialog.md`` two more) plugs into identically.

Mode registration (§2.5): the tab bar and hotkeys are rendered from the
registered-modes list, never hand-enumerated here. This component's own
six tabs register through the exact same :meth:`Editor.register_mode` call
a later component would use — there is no privileged "built-in"
registration path. Registration order is call order (first registered,
first in the tab list) — a later ``register_mode`` call always appends,
never reorders existing tabs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from editor.modes import EditorContext, EditorMode, SpriteCache
from editor.modes.audio_tab import AudioTabMode
from editor.modes.data_editor import DataEditorMode
from editor.modes.map_editor import MapEditorMode
from editor.modes.skin_editor import SkinEditorMode
from editor.modes.sprite_editor import SpriteEditorMode
from editor.modes.sprite_manager import SpriteManagerMode
from editor.project import Project

logger = logging.getLogger(__name__)

_WINDOW_SIZE = (1280, 800)


class Editor:
    """Application shell. See module docstring and doc §2.2."""

    def __init__(self, project: "Project | None" = None) -> None:
        self.project = project

        self.sprite_cache = SpriteCache()
        self._context = EditorContext(
            get_project=lambda: self.project,
            navigate_to=self.navigate_to,
            sprite_cache=self.sprite_cache,
        )

        self._modes: dict[str, EditorMode] = {}
        self._mode_order: list[str] = []
        self._tab_labels: dict[str, str] = {}
        self._hotkeys: dict[str, str] = {}

        self.active_mode_id: str | None = None
        self._screen: Any = None
        self._running = False

        self._register_builtin_modes()
        if self.active_mode_id is None and self._mode_order:
            self.switch_mode(self._mode_order[0])

    # -- mode registration (§2.5) ---------------------------------------------

    def _register_builtin_modes(self) -> None:
        """This component's own six tabs, registered through the exact
        same call `14-editor-visual-quest-dialog.md` will use — the
        concrete proof the seam works (doc §2.5)."""
        self.register_mode("map_editor", MapEditorMode(self._context), "Map", "F1")
        self.register_mode("sprite_editor", SpriteEditorMode(self._context), "Sprite", "F2")
        self.register_mode("data_editor", DataEditorMode(self._context), "Data", "F3")
        self.register_mode("skin_editor", SkinEditorMode(self._context), "Skin", "F4")
        self.register_mode("sprite_manager", SpriteManagerMode(self._context), "Sprite Manager", None)
        self.register_mode("audio_tab", AudioTabMode(self._context), "Audio", None)

    def register_mode(
        self,
        mode_id: str,
        mode_instance: "EditorMode",
        tab_label: str,
        hotkey: str | None = None,
    ) -> None:
        """Register (or replace) a mode under ``mode_id``. Registration
        order is tab order — a mode registered later is appended, never
        inserted ahead of an already-registered one, unless it's replacing
        an existing ``mode_id`` in place (its tab position is preserved)."""
        self._modes[mode_id] = mode_instance
        if mode_id not in self._mode_order:
            self._mode_order.append(mode_id)
        self._tab_labels[mode_id] = tab_label
        if hotkey:
            self._hotkeys[hotkey] = mode_id

    @property
    def tab_list(self) -> list[tuple[str, str]]:
        """``[(mode_id, tab_label), ...]`` in registration order — what a
        real tab bar would render from directly."""
        return [(mode_id, self._tab_labels[mode_id]) for mode_id in self._mode_order]

    def mode(self, mode_id: str) -> "EditorMode | None":
        return self._modes.get(mode_id)

    # -- mode switching -------------------------------------------------------

    def switch_mode(self, mode_id: str, context: dict[str, Any] | None = None) -> None:
        """Switch the active mode by ``mode_id``, resolved through the
        registry (doc §2.3/§2.5) — never a direct reference to another
        mode. ``context`` is the optional §2.7 context-link payload; if the
        target mode implements ``receive_context``, it's delivered right
        after ``on_activate``."""
        if mode_id not in self._modes:
            logger.warning("switch_mode: unknown mode_id %r ignored", mode_id)
            return

        if self.active_mode_id == mode_id and context is None:
            return

        if self.active_mode_id is not None and self.active_mode_id in self._modes:
            self._modes[self.active_mode_id].on_deactivate()

        self.active_mode_id = mode_id
        new_mode = self._modes[mode_id]
        new_mode.on_activate()
        if context is not None:
            receive_context = getattr(new_mode, "receive_context", None)
            if callable(receive_context):
                receive_context(context)

    def navigate_to(self, mode_id: str, context: dict[str, Any] | None = None) -> None:
        """The callback every mode is handed at construction (via
        :class:`editor.modes.EditorContext`) for context links (doc §2.3/
        §2.7) — modes call this instead of ever holding a reference to
        another mode instance."""
        self.switch_mode(mode_id, context)

    def handle_hotkey(self, key_name: str) -> bool:
        mode_id = self._hotkeys.get(key_name)
        if mode_id is None:
            return False
        self.switch_mode(mode_id)
        return True

    def _current_mode(self) -> "EditorMode | None":
        if self.active_mode_id is None:
            return None
        return self._modes.get(self.active_mode_id)

    # -- Project Manager (doc §1.1) ---------------------------------------------

    def new_project(self, root: Path) -> Project:
        """New Project: creates the project, then switches straight to Map
        Editor with the blank map loaded — no intermediate blocking
        dialog, within the same call (doc §1.1/§8)."""
        self.project = Project.new(root)
        map_mode = self._modes.get("map_editor")
        if isinstance(map_mode, MapEditorMode):
            map_mode.load_map("untitled")
        self.switch_mode("map_editor")
        return self.project

    def open_project(self, root_or_archive: Path) -> Project:
        """Open Project: a loose folder opens directly; a ``.pak``/``.pkd``
        unpacks first via ``12-modding-archive-system.md``'s reader if
        merged, or raises :class:`editor.project.ArchiveSupportUnavailable`
        with a documented stub message otherwise (doc §1.1/§2.1). A real
        UI is expected to keep "Open Archive..." disabled with an
        explanatory tooltip while that component is unmerged; see
        :meth:`archive_support_available`."""
        self.project = Project.open(root_or_archive)
        self.switch_mode("map_editor")
        return self.project

    def pack_project(self, dist_dir: Path) -> tuple[Path, Path]:
        if self.project is None:
            raise RuntimeError("No active project to pack")
        return self.project.pack(dist_dir)

    def archive_support_available(self) -> bool:
        """Whether ``12-modding-archive-system.md``'s real archive reader
        is importable yet — the editor UI's "Open Archive..." menu item
        should be enabled iff this is True (doc §2.1)."""
        try:
            import engine.modding.archive  # noqa: F401

            return True
        except ImportError:
            return False

    # -- main loop --------------------------------------------------------------

    def run(self) -> None:
        """Boot pygame and run the main loop:
        handle_event/update/draw across the active mode, once per frame."""
        pygame = _get_pygame()
        if pygame is None:
            logger.info("pygame not installed — Editor.run() is a no-op")
            return

        pygame.init()
        self._screen = pygame.display.set_mode(_WINDOW_SIZE, pygame.RESIZABLE)
        clock = pygame.time.Clock()
        self._running = True

        while self._running:
            dt = clock.tick(60) / 1000.0
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self._running = False
                    continue
                self.handle_event(event)
            self.update(dt)
            self.draw()
            pygame.display.flip()

    def quit(self) -> None:
        self._running = False

    def handle_event(self, event: Any) -> None:
        mode = self._current_mode()
        if mode is not None:
            mode.handle_event(event)

    def update(self, dt: float) -> None:
        mode = self._current_mode()
        if mode is not None:
            mode.update(dt)

    def draw(self) -> None:
        if self._screen is None:
            return
        self._screen.fill((10, 10, 14))
        mode = self._current_mode()
        if mode is not None:
            mode.draw(self._screen)


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
