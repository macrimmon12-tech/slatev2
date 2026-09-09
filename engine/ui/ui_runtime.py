"""``UIRuntime`` — the data-driven widget-tree runtime (``docs/components/
08-ui-runtime.md``). Parses the widget-tree JSON format (§5.1) and
renders/updates the live panel stack every frame.

The single most important invariant this module upholds (doc §1): the
exact same tree format works identically whether it comes from
``data/config/ui_skin.json``, a Python ``create_panel()`` call, or (once
``10-lua-scripting-layer.md`` lands) Lua's ``engine.create_panel()`` —
there is no second, lesser path for scripted content. Every method here
operates purely on plain ``dict``s; nothing about the parser or panel
stack is Python-object-specific.

Coordination notes (genuine spec gaps this module had to resolve on its
own — flagged here and in this component's PR rather than silently
guessed, per CONTRACTS.md §8/§5 precedent set by ``02-ai-system.md`` and
``05-progression-vision.md``):

1. **"Pre-registered tree" for ``show_panel`` (doc §2.2).** The doc says
   ``show_panel`` routes to a tree "registered via ``create_panel`` ahead
   of time," which is unambiguous once a panel has actually been created
   at least once — that case is a direct ``_panels`` lookup, no invention
   needed. What's *not* fully spelled out is how ``main_menu``'s "Load
   Game" button (whose ``on_click`` in this doc's own §5.1/§2.3 example is
   literally ``show_panel(panel_id="save_select")``) is supposed to work
   the very first time, before anything has explicitly called
   ``create_panel("save_select", ...)``. Requiring a caller to always
   ``create_panel`` a screen before it can ever be ``show_panel``'d would
   force every screen to be materialized (and, per "panels stack in
   creation order," visible) at boot, which can't be right for on-demand
   screens. Resolution taken: at construction, if a ``registry`` is given,
   this class reads ``data/config/ui_skin.json`` (the ``configs`` /
   ``ui_skin`` entry) once and caches its ``screens`` dict as a template
   table. ``show_panel`` first checks the live ``_panels`` registry
   (exact doc wording), and only if nothing live matches, falls back to
   this template table and lazily ``create_panel``'s it. This is a direct,
   literal reading of doc §5.2's own framing of ``ui_skin.json``'s
   ``screens`` dict as "pre-registered trees" — not a new mechanism.
   Panels never previously created and absent from both sources still
   no-op silently exactly as doc §2.2 point 2 requires (both cases are
   unit-tested independently of this fallback).
2. **``screen_rect_provider`` call convention.** Not specified beyond "the
   HUD rect or full screen depending on ``full_screen: true`` on the root
   Panel node." Taken as ``screen_rect_provider(full_screen: bool) ->
   (x, y, w, h)`` — one callable, one bool argument selecting which rect.
3. **``ui``-context input action names.** ``07-input-renderer-audio.md``
   (not merged yet) owns raw key -> action translation and hasn't defined
   its exact ``ui``-context action vocabulary. This module assumes the
   minimal, conventional set ``{"confirm", "cancel", "up", "down", "left",
   "right"}`` (confirm activates the focused interactive widget; up/down/
   left/right move focus by one; cancel is intentionally left
   unhandled/returns ``False`` — no documented default exists for it, so
   this runtime doesn't invent panel-dismissal behavior). If ``07`` lands
   with different names, that's a follow-up alignment noted for
   ``15-integration-verification.md``, not a blocker here.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from engine.core.events import EventBus
from engine.core.registry import DataRegistry
from engine.core.schemas.ui_skin_schema import validate_widget_tree

logger = logging.getLogger(__name__)

#: ``{{item.field}}`` (or plain ``{{key}}``) interpolation used by Label/
#: Button ``text`` and ``on_click.payload`` fields (doc §5.1).
_TEMPLATE_FULL_RE = re.compile(r"^\{\{\s*([A-Za-z0-9_.]+)\s*\}\}$")
_TEMPLATE_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")

#: ``ui``-context actions this runtime recognizes — see module docstring
#: note 3.
_FOCUS_NEXT_ACTIONS = frozenset({"down", "right"})
_FOCUS_PREV_ACTIONS = frozenset({"up", "left"})

_VALID_ANCHORS_RIGHT = frozenset({"top_right", "bottom_right"})
_VALID_ANCHORS_BOTTOM = frozenset({"bottom_left", "bottom_right"})

ScreenRectProvider = Callable[[bool], tuple[int, int, int, int]]

# Lazily imported pygame module. ``False`` = not yet attempted, ``None`` =
# attempted and unavailable. pygame-ce isn't a project dependency yet
# (that lands with 07-input-renderer-audio.md); until it is, drawing is a
# documented no-op (absence = zero cost, CONTRACTS.md §2 rule 7) rather
# than a hard import-time failure for every other component's tests.
_pygame_module: Any = False


def get_pygame_module() -> Any:
    """Return the imported ``pygame`` module, or ``None`` if it isn't
    installed. Cached after the first call; logs once on the fallback
    path. Shared by :mod:`engine.ui.ui_runtime` and
    :mod:`engine.ui.targeting_overlay` — both draw onto the same kind of
    surface and both need the identical "no pygame yet" fallback."""
    global _pygame_module
    if _pygame_module is False:
        try:
            import pygame  # type: ignore

            _pygame_module = pygame
        except ImportError:
            _pygame_module = None
            logger.info(
                "pygame not installed — UI drawing is a no-op until it is "
                "(absence = zero cost, CONTRACTS.md §2 rule 7)."
            )
    return _pygame_module


def _lighten(color: tuple[int, int, int], amount: int) -> tuple[int, int, int]:
    """Auto-generated hover fill when a Button spec doesn't set its own
    ``hover_color`` — clamped channel-wise so this never overflows/wraps."""
    return tuple(min(255, channel + amount) for channel in color)  # type: ignore[return-value]


def _default_screen_rect_provider(full_screen: bool) -> tuple[int, int, int, int]:
    # No renderer is wired yet (07-input-renderer-audio.md) so there is no
    # real screen size to report; a fixed placeholder keeps callers that
    # don't supply their own provider (tests, early boot) working.
    return (0, 0, 1280, 720) if full_screen else (0, 0, 800, 600)


def _lookup(context: dict[str, Any], dotted_path: str) -> Any:
    parts = dotted_path.split(".")
    current: Any = context
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _interpolate(value: Any, context: dict[str, Any]) -> Any:
    """Resolve ``{{dotted.path}}`` templates in ``value`` against
    ``context``. A value that is *entirely* one template token resolves to
    the looked-up value verbatim (preserving type — e.g. ``payload:
    {"slot": "{{item.slot_id}}"}`` yields the real ``slot_id``, not a
    string). A template embedded in a larger string is substituted as
    text. Non-string values, and strings with no template markers, pass
    through unchanged. A dotted path that resolves to nothing (missing
    key at any level) becomes ``None`` for a full-token match, or an empty
    string when embedded in text — never raises (absence = zero cost)."""
    if not isinstance(value, str):
        return value

    full_match = _TEMPLATE_FULL_RE.match(value)
    if full_match:
        return _lookup(context, full_match.group(1))

    def _replace(match: re.Match[str]) -> str:
        looked_up = _lookup(context, match.group(1))
        return "" if looked_up is None else str(looked_up)

    return _TEMPLATE_RE.sub(_replace, value)


def _interpolate_deep(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _interpolate_deep(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_interpolate_deep(item, context) for item in value]
    return _interpolate(value, context)


def _is_visible(spec: dict[str, Any], data: dict[str, Any]) -> bool:
    condition = spec.get("visible_if")
    if condition is None:
        return True
    return data.get(condition["data_key"]) == condition["equals"]


def _rect_spec_of(spec: dict[str, Any]) -> dict[str, Any]:
    rect = spec.get("rect")
    if rect is not None:
        return rect
    return {"x": spec.get("x", 0), "y": spec.get("y", 0), "w": spec.get("w"), "h": spec.get("h")}


def resolve_rect(
    spec: dict[str, Any], parent_rect: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    """Resolve a node's ``rect`` (or flat ``x``/``y``/``w``/``h``) against
    its parent container's already-resolved rect (doc §5.1). ``x``/``y``
    of the literal string ``"center"`` centers the node within the parent
    on that axis. ``anchor`` (default ``top_left``) picks which corner of
    the parent numeric ``x``/``y`` are measured from — ``top_right``/
    ``bottom_right`` measure ``x`` from the parent's right edge,
    ``bottom_left``/``bottom_right`` measure ``y`` from the parent's
    bottom edge. Missing ``w``/``h`` default to filling the parent on that
    axis."""
    parent_x, parent_y, parent_w, parent_h = parent_rect
    rect_spec = _rect_spec_of(spec)

    width = rect_spec.get("w")
    width = parent_w if width is None else width
    height = rect_spec.get("h")
    height = parent_h if height is None else height

    anchor = spec.get("anchor", "top_left")

    raw_x = rect_spec.get("x", 0)
    if raw_x == "center":
        x = parent_x + (parent_w - width) // 2
    else:
        numeric_x = float(raw_x)
        if anchor in _VALID_ANCHORS_RIGHT:
            x = parent_x + parent_w - numeric_x - width
        else:
            x = parent_x + numeric_x

    raw_y = rect_spec.get("y", 0)
    if raw_y == "center":
        y = parent_y + (parent_h - height) // 2
    else:
        numeric_y = float(raw_y)
        if anchor in _VALID_ANCHORS_BOTTOM:
            y = parent_y + parent_h - numeric_y - height
        else:
            y = parent_y + numeric_y

    return (int(x), int(y), int(width), int(height))


@dataclass
class _WidgetNode:
    """A parsed mirror of one widget-tree node. Built once by
    :func:`_build_widget` when a panel is created (or when a ``List``/
    ``Grid`` item is expanded); never rebuilt by :meth:`UIRuntime.update_panel`,
    which only ever replaces the panel's bound ``data`` — this is what
    keeps a top-level widget's Python identity stable across an update
    (this component's Definition of Done requires a test asserting
    exactly that)."""

    spec: dict[str, Any]
    children: list["_WidgetNode"] = field(default_factory=list)


def _build_widget(spec: dict[str, Any]) -> _WidgetNode:
    child_specs = spec.get("children") or []
    return _WidgetNode(spec=spec, children=[_build_widget(child) for child in child_specs])


@dataclass
class _PanelState:
    panel_id: str
    root: _WidgetNode
    data: dict[str, Any]
    focus_index: int = 0


class UIRuntime:
    """Widget-tree parser, panel stack, and per-frame render/update
    driver. See module docstring and ``docs/components/08-ui-runtime.md``
    §2.1 for the full contract; this exact ``create_panel``/
    ``update_panel``/``destroy_panel`` triple is what
    ``10-lua-scripting-layer.md``'s ``engine.*`` API binds to 1:1 — keep
    the signatures stable.
    """

    def __init__(
        self,
        event_bus: EventBus,
        registry: DataRegistry | None,
        screen_rect_provider: ScreenRectProvider | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._registry = registry
        self._screen_rect_provider = screen_rect_provider or _default_screen_rect_provider

        self._panels: dict[str, _PanelState] = {}
        self._stack: list[str] = []  # panel_id, bottom -> top
        self._screen_templates: dict[str, dict[str, Any]] = {}

        # Instance copy (not a class-attribute mutation) so multiple
        # UIRuntime instances in the same process (e.g. tests) never leak
        # config from one into another. Live-play visual polish fix: this
        # used to be a hardcoded class dict that `data/config/ui_config.json`
        # had zero effect on despite the file existing and every screen
        # referencing its color names ("panel_bg", "title", "text_default",
        # "hp_red") -- a "built but not wired" gap exactly like the ones
        # CONTRACTS.md §7 exists to catch, just in the one place nothing
        # was ever automatically re-verified against a running window.
        # ui_config.json's colors now genuinely drive rendering; the
        # hardcoded pairs below remain only as the fallback when no
        # registry/config is available (tests, early boot).
        self._STYLE_COLORS: dict[str, tuple[int, int, int]] = dict(self._STYLE_COLORS)

        if registry is not None:
            ui_skin_config = registry.get("configs", "ui_skin")
            if ui_skin_config:
                self._screen_templates = dict(ui_skin_config.get("screens") or {})

            ui_config = registry.get("configs", "ui_config")
            if ui_config:
                for key, value in (ui_config.get("colors") or {}).items():
                    if isinstance(value, (list, tuple)) and len(value) == 3:
                        self._STYLE_COLORS[key] = (int(value[0]), int(value[1]), int(value[2]))

        self._font: Any = None  # lazily created pygame.font.Font, or False if init failed
        self._hover_spec: Any = None  # the exact spec dict of the currently-hovered interactive widget

        self._show_panel_token = event_bus.subscribe("show_panel", self._on_show_panel)
        # 15-integration-verification.md fix: 10-lua-scripting-layer.md's
        # own module docstring (engine/lua/api/panel.py) already flagged
        # both gaps below "for 08 to reconcile" -- CONTRACTS.md §3.2 lists
        # `panel_closed` as consumed by UIRuntime, but nothing here ever
        # subscribed to it, so a Lua `engine.destroy_panel` call never
        # actually removed the panel from this runtime's stack.
        event_bus.subscribe("panel_closed", self._on_panel_closed)

    # -- create/update/destroy -------------------------------------------

    def create_panel(self, panel_id: str, tree: dict[str, Any], data: dict[str, Any] | None = None) -> None:
        """Parse ``tree`` (validated against the widget-tree schema) and
        push it onto the panel stack, on top. Re-creating an existing
        ``panel_id`` replaces its tree/data outright and moves it to the
        top of the stack (a fresh widget-object tree is built in this
        case — identity stability is only guaranteed *between*
        ``update_panel`` calls on an unchanged panel, not across a second
        ``create_panel`` for the same id)."""
        validate_widget_tree(tree)
        root = _build_widget(tree)
        self._panels[panel_id] = _PanelState(panel_id=panel_id, root=root, data=dict(data or {}))
        if panel_id in self._stack:
            self._stack.remove(panel_id)
        self._stack.append(panel_id)

    def update_panel(self, panel_id: str, data: dict[str, Any]) -> None:
        """Merge ``data`` into an already-created panel's live binding
        without rebuilding any widget object. A no-op if ``panel_id``
        doesn't exist (absence = zero cost) — a Lua script racing a
        ``destroy_panel`` shouldn't need to guard every ``update_panel``
        call."""
        state = self._panels.get(panel_id)
        if state is None:
            return
        state.data.update(data)

    def destroy_panel(self, panel_id: str) -> None:
        """Remove a panel from the stack immediately. A no-op if
        ``panel_id`` doesn't exist (absence = zero cost)."""
        self._panels.pop(panel_id, None)
        if panel_id in self._stack:
            self._stack.remove(panel_id)

    # -- show_panel ---------------------------------------------------------

    def _on_show_panel(self, payload: dict[str, Any] | None) -> None:
        payload = payload or {}
        panel_id = payload.get("panel_id")
        if not panel_id:
            return
        data = payload.get("data") or {}

        state = self._panels.get(panel_id)
        if state is not None:
            # Already live — re-bind data and bring to front (doc §2.2
            # point 1's "pre-registered tree" case; see module docstring
            # note 1 for the rest of the resolution).
            state.data.update(data)
            self._stack.remove(panel_id)
            self._stack.append(panel_id)
            return

        # 15-integration-verification.md fix: an ad-hoc `tree` on the
        # payload (10-lua-scripting-layer.md's `engine.create_panel`,
        # `tree` non-None per its own docstring) builds a real panel from
        # it directly -- this used to fall through to the
        # ui_skin.json-only lookup below and silently no-op for any
        # panel_id that wasn't already a pre-registered screen (10's own
        # engine/lua/api/panel.py module docstring flagged this exact gap
        # "for 08 to reconcile"; reconciled here).
        tree = payload.get("tree")
        if tree is not None:
            self.create_panel(panel_id, tree, data)
            return

        template = self._screen_templates.get(panel_id)
        if template is not None:
            self.create_panel(panel_id, template, data)
            return

        # No tree registered anywhere for this panel_id — a missed frame,
        # not an error (doc §2.2 point 2 / CONTRACTS.md §2 rule 7).
        logger.debug("show_panel for unregistered panel_id %r ignored", panel_id)

    def _on_panel_closed(self, payload: dict[str, Any] | None) -> None:
        payload = payload or {}
        panel_id = payload.get("panel_id")
        if not panel_id:
            return
        self.destroy_panel(panel_id)

    # -- draw -------------------------------------------------------------

    def draw(self, surface: Any, mouse_pos: tuple[int, int] | None = None) -> None:
        """Draw every visible panel, bottom to top, onto ``surface``. A
        pure no-op (per widget, logged once) if pygame isn't installed —
        see :func:`get_pygame_module`.

        ``mouse_pos``, when given, updates which interactive widget (if
        any) is hovered — purely visual (a lighter fill via
        ``hover_color``/an auto-lightened ``color``); it never activates
        anything by itself (that's :meth:`handle_mouse_click`'s job)."""
        if mouse_pos is not None:
            self._update_hover(mouse_pos)
        for panel_id in list(self._stack):
            state = self._panels.get(panel_id)
            if state is None:
                continue
            if not _is_visible(state.root.spec, state.data):
                continue
            parent_rect = self._screen_rect_provider(bool(state.root.spec.get("full_screen")))
            self._draw_widget(surface, state.root, state.data, parent_rect)

    def _draw_widget(
        self,
        surface: Any,
        widget: _WidgetNode,
        data: dict[str, Any],
        parent_rect: tuple[int, int, int, int],
    ) -> None:
        spec = widget.spec
        if not _is_visible(spec, data):
            return

        rect = resolve_rect(spec, parent_rect)
        node_type = spec.get("type")

        pygame_module = get_pygame_module()
        if pygame_module is not None:
            try:
                self._draw_with_pygame(pygame_module, surface, spec, node_type, rect, data)
            except Exception:
                logger.warning(
                    "UI draw failed for node type=%s id=%s", node_type, spec.get("id"), exc_info=True
                )

        for child in widget.children:
            self._draw_widget(surface, child, data, rect)

        if node_type in ("List", "Grid"):
            self._draw_list_items(surface, spec, rect, data)

    def _draw_list_items(
        self,
        surface: Any,
        spec: dict[str, Any],
        rect: tuple[int, int, int, int],
        data: dict[str, Any],
    ) -> None:
        x, y, w, _h = rect
        template = spec.get("item_template")
        if not template:
            return
        template_rect_spec = _rect_spec_of(template)
        row_h = template_rect_spec.get("h") or 32
        for index, (item_widget, item_context) in enumerate(self._expand_items(spec, data)):
            cell_rect = (x, y + index * row_h, w, row_h)
            self._draw_widget(surface, item_widget, item_context, cell_rect)

    def _expand_items(
        self, spec: dict[str, Any], data: dict[str, Any]
    ) -> list[tuple[_WidgetNode, dict[str, Any]]]:
        """Instantiate ``item_template`` once per entry in
        ``data[items_key]``. Rebuilt fresh every call by design — unlike
        the panel's static tree, list contents are inherently derived from
        live data and are expected to change shape as that data changes."""
        items_key = spec.get("items_key")
        items = data.get(items_key)
        template = spec.get("item_template")
        if not template or not isinstance(items, list):
            return []
        expanded: list[tuple[_WidgetNode, dict[str, Any]]] = []
        for item in items:
            item_context = dict(data)
            item_context["item"] = item
            expanded.append((_build_widget(template), item_context))
        return expanded

    _STYLE_COLORS = {
        "panel_bg": (30, 30, 40),
        "hp_red": (200, 40, 40),
    }

    def _color_for(self, style_key: Any, default: tuple[int, int, int]) -> tuple[int, int, int]:
        return self._STYLE_COLORS.get(style_key, default)

    def _draw_with_pygame(
        self,
        pygame_module: Any,
        surface: Any,
        spec: dict[str, Any],
        node_type: str,
        rect: tuple[int, int, int, int],
        data: dict[str, Any],
    ) -> None:
        x, y, w, h = rect
        if node_type == "Panel":
            color = self._color_for(spec.get("background"), (20, 20, 25))
            pygame_module.draw.rect(surface, color, pygame_module.Rect(x, y, w, h))
            # Optional subtle border (visual-polish addition, additive
            # field per the schema's own stated permissiveness about
            # unknown keys — absent 'border' draws exactly as before).
            border_key = spec.get("border")
            if border_key:
                border_color = self._color_for(border_key, (70, 70, 80))
                pygame_module.draw.rect(surface, border_color, pygame_module.Rect(x, y, w, h), width=1)
        elif node_type == "Label":
            text_color = self._color_for(spec.get("style"), self._color_for("text_default", (230, 230, 230)))
            self._blit_text(
                pygame_module, surface, str(_interpolate(spec.get("text", ""), data)), (x, y), text_color
            )
        elif node_type == "Bar":
            value = float(data.get(spec.get("value_key"), 0) or 0)
            max_value = float(data.get(spec.get("max_key"), 1) or 1)
            ratio = 0.0 if max_value <= 0 else max(0.0, min(1.0, value / max_value))
            track_color = self._color_for(spec.get("bg_color"), (60, 60, 60))
            pygame_module.draw.rect(surface, track_color, pygame_module.Rect(x, y, w, h))
            fill_color = self._color_for(spec.get("color"), (200, 40, 40))
            pygame_module.draw.rect(surface, fill_color, pygame_module.Rect(x, y, int(w * ratio), h))
            label = spec.get("label")
            if label:
                label_color = self._color_for("text_default", (230, 230, 230))
                self._blit_text(pygame_module, surface, str(label), (x + 4, y), label_color)
        elif node_type == "Button":
            hovered = self._hover_spec is not None and spec is self._hover_spec
            base_color = self._color_for(spec.get("color"), (50, 50, 60))
            if hovered:
                hover_key = spec.get("hover_color")
                fill_color = (
                    self._color_for(hover_key, base_color) if hover_key else _lighten(base_color, 24)
                )
            else:
                fill_color = base_color
            pygame_module.draw.rect(surface, fill_color, pygame_module.Rect(x, y, w, h))
            text = str(_interpolate(spec.get("text", ""), data))
            text_color = self._color_for(spec.get("text_style"), self._color_for("text_default", (230, 230, 230)))
            self._blit_text(pygame_module, surface, text, (x + 4, y + 4), text_color)
        # List/Grid are pure containers here — their items are drawn by
        # _draw_list_items, called separately by _draw_widget.

    def _blit_text(
        self,
        pygame_module: Any,
        surface: Any,
        text: str,
        pos: tuple[int, int],
        color: tuple[int, int, int] = (230, 230, 230),
    ) -> None:
        if not text:
            return
        font = self._get_font(pygame_module)
        if font is None:
            return
        surface.blit(font.render(text, True, color), pos)

    def _get_font(self, pygame_module: Any) -> Any:
        if self._font is None:
            try:
                if not pygame_module.font.get_init():
                    pygame_module.font.init()
                self._font = pygame_module.font.Font(None, 18)
            except Exception:
                logger.warning("Could not initialize a font for UI text rendering", exc_info=True)
                self._font = False  # tried and failed — don't retry every frame
        return self._font or None

    # -- input --------------------------------------------------------------

    def handle_ui_input(self, action: str) -> bool:
        """Route a ``ui``-context input action to the topmost panel that
        has any interactive widgets, per doc §2.1 ("topmost panel receives
        ``ui``-context input first ... returns ``True`` to signal
        consumed, stop propagating"). A panel with zero interactive
        widgets is transparent to input (falls through to the next panel
        down) — see module docstring note 3 for the assumed action
        vocabulary."""
        for panel_id in reversed(self._stack):
            state = self._panels.get(panel_id)
            if state is None or not _is_visible(state.root.spec, state.data):
                continue
            interactive = list(self._iter_interactive(state.root, state.data))
            if not interactive:
                continue
            return self._handle_panel_input(state, interactive, action)
        return False

    def _handle_panel_input(
        self,
        state: _PanelState,
        interactive: list[tuple[_WidgetNode, dict[str, Any]]],
        action: str,
    ) -> bool:
        if action == "confirm":
            state.focus_index %= len(interactive)
            self._activate(interactive[state.focus_index])
            return True
        if action in _FOCUS_NEXT_ACTIONS:
            state.focus_index = (state.focus_index + 1) % len(interactive)
            return True
        if action in _FOCUS_PREV_ACTIONS:
            state.focus_index = (state.focus_index - 1) % len(interactive)
            return True
        # "cancel" and anything else: no documented default behavior for
        # this runtime to invent — not consumed.
        return False

    def _iter_interactive(
        self, widget: _WidgetNode, data: dict[str, Any]
    ):
        spec = widget.spec
        if not _is_visible(spec, data):
            return
        node_type = spec.get("type")
        if node_type == "Button" and spec.get("on_click"):
            yield (widget, data)
        for child in widget.children:
            yield from self._iter_interactive(child, data)
        if node_type in ("List", "Grid"):
            for item_widget, item_context in self._expand_items(spec, data):
                yield from self._iter_interactive(item_widget, item_context)

    def _activate(self, entry: tuple[_WidgetNode, dict[str, Any]]) -> None:
        widget, context = entry
        on_click = widget.spec.get("on_click")
        if not on_click:
            return
        event = on_click.get("event")
        if not event:
            return
        payload = _interpolate_deep(on_click.get("payload") or {}, context)
        self._event_bus.emit(event, payload)

    # -- mouse --------------------------------------------------------------
    #
    # No component doc specifies mouse handling (07/08 both only define a
    # keyboard/gamepad `ui`-context action vocabulary — module docstring
    # note 3). A real desktop window needs click-to-activate too, so this
    # follows the same "topmost panel with any interactive widgets claims
    # input" rule `handle_ui_input` already established, generalized to
    # pixel coordinates instead of a focus index.

    def _iter_interactive_with_rect(
        self,
        widget: _WidgetNode,
        data: dict[str, Any],
        parent_rect: tuple[int, int, int, int],
    ):
        spec = widget.spec
        if not _is_visible(spec, data):
            return
        rect = resolve_rect(spec, parent_rect)
        node_type = spec.get("type")
        if node_type == "Button" and spec.get("on_click"):
            yield (widget, data, rect)
        for child in widget.children:
            yield from self._iter_interactive_with_rect(child, data, rect)
        if node_type in ("List", "Grid"):
            x, y, w, _h = rect
            template = spec.get("item_template")
            if template:
                row_h = _rect_spec_of(template).get("h") or 32
                for index, (item_widget, item_context) in enumerate(self._expand_items(spec, data)):
                    cell_rect = (x, y + index * row_h, w, row_h)
                    yield from self._iter_interactive_with_rect(item_widget, item_context, cell_rect)

    def _hit_test(
        self, pos: tuple[int, int]
    ) -> tuple[bool, tuple[_WidgetNode, dict[str, Any], tuple[int, int, int, int]] | None]:
        """Returns ``(panel_claimed, hit)``. ``panel_claimed`` is True as
        soon as the topmost panel with any interactive widgets is found,
        even if ``pos`` misses every widget in it — mirrors
        ``handle_ui_input``'s "topmost interactive panel owns input,
        whether or not it does anything with this particular press"."""
        px, py = pos
        for panel_id in reversed(self._stack):
            state = self._panels.get(panel_id)
            if state is None or not _is_visible(state.root.spec, state.data):
                continue
            parent_rect = self._screen_rect_provider(bool(state.root.spec.get("full_screen")))
            hits = list(self._iter_interactive_with_rect(state.root, state.data, parent_rect))
            if not hits:
                continue
            for hit in hits:
                _widget, _context, rect = hit
                rx, ry, rw, rh = rect
                if rx <= px < rx + rw and ry <= py < ry + rh:
                    return True, hit
            return True, None
        return False, None

    def _update_hover(self, pos: tuple[int, int]) -> None:
        _claimed, hit = self._hit_test(pos)
        self._hover_spec = hit[0].spec if hit is not None else None

    def handle_mouse_click(self, pos: tuple[int, int]) -> bool:
        """Click analogue of :meth:`handle_ui_input`. Returns True if a
        panel with interactive widgets claimed the click (so a caller —
        the game loop — knows not to also treat it as a game-world
        click), regardless of whether the click landed on an actual
        widget."""
        claimed, hit = self._hit_test(pos)
        if hit is not None:
            self._activate((hit[0], hit[1]))
        return claimed
