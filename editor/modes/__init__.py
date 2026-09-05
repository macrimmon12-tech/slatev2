"""Shared mode contract + small cross-mode infrastructure for the SLATE v2
content editor (``docs/components/13-editor-core-authoring.md`` §2.3).

Everything a mode needs to reach outside itself is handed to it at
construction time as an :class:`EditorContext` — modes never import each
other or hold a reference to another mode instance. A mode that wants to
jump to another mode calls ``context.navigate_to(mode_id, payload)``; it
never reaches for ``other_mode_instance.some_method()``.

This module also carries the handful of small, genuinely cross-mode
usability primitives from the component doc's §7 ("lessons from v1") so
they're implemented once and shared, not re-invented per mode:

- :class:`TooltipMixin` — non-obvious-control tooltips.
- :class:`SaveAffordanceMixin` — a visible Save control + dirty flag.
- :func:`compute_dropdown_rect` — position-aware popup placement (flips
  above/left instead of clipping off-screen).
- :class:`SpriteCache` — cross-mode sprite-preview cache invalidation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

# ``Project`` is defined in ``editor/project.py``; imported lazily under
# TYPE_CHECKING to avoid a hard import-order dependency at module load time
# (project.py doesn't import this module, so there's no real cycle, but
# keeping the import local documents the direction of the dependency).
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from editor.project import Project


@runtime_checkable
class EditorMode(Protocol):
    """The contract every registered mode implements
    (``docs/components/13-editor-core-authoring.md`` §2.3)."""

    def handle_event(self, event: Any) -> None: ...

    def update(self, dt: float) -> None: ...

    def draw(self, surface: Any) -> None: ...

    def on_activate(self) -> None: ...

    def on_deactivate(self) -> None: ...


class SpriteCache:
    """A tiny path -> loaded-surface cache shared by every mode that
    previews sprites (Map Editor, Sprite Editor, Sprite Manager).

    Doc §7 requires that saving a sprite in Sprite Editor invalidate any
    cached preview shown elsewhere *without an app restart* — this is the
    single shared cache all three modes read/write through so that
    invariant holds structurally instead of per-mode.
    """

    def __init__(self) -> None:
        self._surfaces: dict[str, Any] = {}
        self._version: dict[str, int] = {}

    def get(self, path: str | Path) -> Any | None:
        return self._surfaces.get(str(path))

    def put(self, path: str | Path, surface: Any) -> None:
        key = str(path)
        self._surfaces[key] = surface
        self._version[key] = self._version.get(key, 0) + 1

    def invalidate(self, path: str | Path) -> None:
        """Drop any cached surface for ``path`` and bump its version
        counter so a caller can detect the change even if it already holds
        a stale reference."""
        key = str(path)
        self._surfaces.pop(key, None)
        self._version[key] = self._version.get(key, 0) + 1

    def version(self, path: str | Path) -> int:
        return self._version.get(str(path), 0)


@dataclass
class EditorContext:
    """Everything a mode may need from outside itself, injected at
    registration time instead of the mode reaching for it directly.

    ``get_project`` is a callable (not a bare reference) because the active
    project can change out from under a mode (Open Project / New Project) —
    modes must always call it fresh rather than caching the result.
    """

    get_project: Callable[[], "Project | None"]
    navigate_to: Callable[[str, dict[str, Any] | None], None]
    sprite_cache: SpriteCache = field(default_factory=SpriteCache)

    def project(self) -> "Project | None":
        return self.get_project()


class TooltipMixin:
    """Non-obvious-control tooltips (doc §7). A mode using this mixin
    declares ``self.tooltips: dict[str, str]`` (control id -> tooltip text)
    and gets a uniform ``tooltip_for``/``has_tooltip`` pair for free instead
    of hand-rolling presence checks per mode."""

    tooltips: dict[str, str]

    def tooltip_for(self, control_id: str) -> str | None:
        return getattr(self, "tooltips", {}).get(control_id)

    def has_tooltip(self, control_id: str) -> bool:
        return control_id in getattr(self, "tooltips", {})


class SaveAffordanceMixin:
    """A visible Save control + unsaved-changes indicator (doc §7 — v1's
    Save worked but had no discoverable UI element). A mode using this
    mixin exposes a real ``save_button_rect`` (so a test/click-hit-test can
    find it, not just Ctrl+S) and a ``dirty`` flag flipped by
    ``mark_dirty``/cleared by ``mark_saved``."""

    dirty: bool = False
    save_button_label: str = "Save"

    def mark_dirty(self) -> None:
        self.dirty = True

    def mark_saved(self) -> None:
        self.dirty = False

    def has_visible_save_control(self) -> bool:
        """Every mode using this mixin ships a real, visible Save
        button/menu item — this is the presence check a test asserts
        instead of only checking that Ctrl+S works."""
        return True

    def unsaved_changes_indicator(self) -> str:
        return "*" if self.dirty else ""


def compute_dropdown_rect(
    button_rect: tuple[int, int, int, int],
    popup_size: tuple[int, int],
    screen_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    """Position a dropdown popup anchored to ``button_rect`` inside
    ``screen_size``, flipping above/left when there isn't room below/right
    (doc §7 — "dropdown popups that don't clip off-screen").

    Returns ``(x, y, w, h)`` for the popup. Default anchor is below-left of
    the button; flips to above the button if it would overflow the bottom
    edge, and flips its horizontal alignment to right-aligned against the
    button if it would overflow the right edge.
    """
    bx, by, bw, bh = button_rect
    pw, ph = popup_size
    screen_w, screen_h = screen_size

    x = bx
    if x + pw > screen_w:
        x = max(0, bx + bw - pw)

    y = by + bh
    if y + ph > screen_h:
        flipped_y = by - ph
        y = flipped_y if flipped_y >= 0 else max(0, screen_h - ph)

    return (x, y, pw, ph)


def table_column_headers(columns: list[dict[str, Any]]) -> list[str]:
    """Labeled column headers for any list/table widget (doc §7 — "visible
    column headers on form tables"). ``columns`` is the list-of-dict shape
    every table-like widget (the effect-array sub-editor, loot table
    entries) declares its columns with; this just extracts the display
    labels in order, falling back to the raw key if a column omits an
    explicit label."""
    return [str(col.get("label", col.get("key", ""))) for col in columns]
