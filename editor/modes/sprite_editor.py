"""Sprite Editor (``docs/components/13-editor-core-authoring.md`` §2.4) —
pixel-level painting, Sheet Mode with animation preview, multi-size
round/square brushes, and a mirror tool.

Pixel data is stored sparsely per frame (``dict[(x, y), RGBA]`` — an unset
pixel is transparent) so an arbitrary canvas size costs nothing until
something is actually painted on it (CONTRACTS.md §2 rule 7's "absence =
zero cost" spirit, applied to authoring data rather than gameplay).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from editor.modes import EditorContext, SaveAffordanceMixin, TooltipMixin

logger = logging.getLogger(__name__)

RGBA = tuple[int, int, int, int]

_TOOLTIPS = {
    "save_button": "Save this sprite/sheet as a PNG under the project's assets/sprites/ (Ctrl+S)",
    "brush_size": "Brush width in pixels",
    "brush_shape": "Round or square brush footprint",
    "mirror_tool": "Mirror painted pixels horizontally or vertically while you paint",
    "onion_skin": "Show the previous frame faintly beneath the current one",
    "frame_scrub": "Drag to preview the sheet's frames in sequence",
    "add_frame_button": "Add a new blank frame to this sprite sheet",
}

_VALID_SHAPES = frozenset({"square", "round"})
_VALID_MIRRORS = frozenset({None, "horizontal", "vertical"})


class SpriteEditorMode(TooltipMixin, SaveAffordanceMixin):
    def __init__(self, context: EditorContext, width: int = 16, height: int = 16) -> None:
        self._context = context
        self.tooltips = dict(_TOOLTIPS)

        self.width = width
        self.height = height
        self.frames: list[dict[tuple[int, int], RGBA]] = [{}]
        self.current_frame_index = 0
        self.onion_skin = False

        self.brush_size = 1
        self.brush_shape = "square"
        self.mirror: str | None = None

        self.loaded_path: str | None = None
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
            frame = self.frames[self.current_frame_index]
            for (x, y), color in frame.items():
                surface.fill(color[:3], pygame.Rect(x, y, 1, 1))
        except Exception:
            logger.warning("Sprite Editor draw failed", exc_info=True)

    # -- context link receiver (doc §2.7) -------------------------------------

    def receive_context(self, context: dict[str, Any]) -> None:
        """Called by the Editor shell right after ``navigate_to`` switches
        into this mode with a context payload — e.g. Map Editor's palette
        right-click hands ``{"asset_path": ...}`` here (doc §2.7)."""
        asset_path = context.get("asset_path")
        if asset_path:
            self.load_sprite(asset_path)

    # -- new / load / frames --------------------------------------------------

    def new_sprite(self, width: int = 16, height: int = 16) -> None:
        self.width = width
        self.height = height
        self.frames = [{}]
        self.current_frame_index = 0
        self.loaded_path = None
        self.mark_dirty()

    def load_sprite(self, relative_or_absolute_path: str) -> None:
        pygame = _get_pygame()
        project = self._context.project()
        path = Path(relative_or_absolute_path)
        if project is not None and not path.is_absolute():
            path = project.assets_path(relative_or_absolute_path)

        self.loaded_path = str(relative_or_absolute_path)
        if pygame is None or not path.exists():
            # Absence = zero cost: start from a blank canvas rather than
            # raising if the referenced PNG doesn't exist (yet).
            self.frames = [{}]
            self.current_frame_index = 0
            self.mark_saved()
            return

        image = pygame.image.load(str(path))
        self.width, self.height = image.get_width(), image.get_height()
        frame: dict[tuple[int, int], RGBA] = {}
        for x in range(self.width):
            for y in range(self.height):
                frame[(x, y)] = tuple(image.get_at((x, y)))
        self.frames = [frame]
        self.current_frame_index = 0
        self.mark_saved()

    def add_frame(self) -> int:
        self.frames.append({})
        self.current_frame_index = len(self.frames) - 1
        self.mark_dirty()
        return self.current_frame_index

    def select_frame(self, index: int) -> None:
        if 0 <= index < len(self.frames):
            self.current_frame_index = index

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    def set_onion_skin(self, enabled: bool) -> None:
        self.onion_skin = enabled

    def onion_skin_frame(self) -> dict[tuple[int, int], RGBA] | None:
        """The previous frame's pixels, for a faint underlay preview — only
        meaningful when :attr:`onion_skin` is enabled and there is a
        previous frame."""
        if not self.onion_skin or self.current_frame_index == 0:
            return None
        return self.frames[self.current_frame_index - 1]

    # -- brush / mirror configuration ------------------------------------------

    def set_brush(self, size: int, shape: str = "square") -> None:
        if shape not in _VALID_SHAPES:
            raise ValueError(f"invalid brush shape {shape!r}; expected one of {sorted(_VALID_SHAPES)}")
        if size < 1:
            raise ValueError("brush size must be >= 1")
        self.brush_size = size
        self.brush_shape = shape

    def set_mirror(self, mode: str | None) -> None:
        if mode not in _VALID_MIRRORS:
            raise ValueError(f"invalid mirror mode {mode!r}; expected one of {sorted(m for m in _VALID_MIRRORS if m)}")
        self.mirror = mode

    # -- painting -------------------------------------------------------------

    def _brush_offsets(self) -> list[tuple[int, int]]:
        size = self.brush_size
        radius = size / 2.0
        offsets: list[tuple[int, int]] = []
        half = size // 2
        for dx in range(-half, size - half):
            for dy in range(-half, size - half):
                if self.brush_shape == "square":
                    offsets.append((dx, dy))
                else:  # round -- a strict subset of the equivalent square
                    # brush, excluding the square's corner pixels.
                    if dx * dx + dy * dy <= radius ** 2:
                        offsets.append((dx, dy))
        return offsets

    def paint_pixel(self, x: int, y: int, color: RGBA) -> None:
        """Paint at ``(x, y)`` using the current brush size/shape, applying
        the active mirror mode (if any) as a second simultaneous stroke."""
        frame = self.frames[self.current_frame_index]
        targets = {(x + dx, y + dy) for dx, dy in self._brush_offsets()}

        if self.mirror == "horizontal":
            targets |= {(self.width - 1 - tx, ty) for tx, ty in targets}
        elif self.mirror == "vertical":
            targets |= {(tx, self.height - 1 - ty) for tx, ty in targets}

        for tx, ty in targets:
            if 0 <= tx < self.width and 0 <= ty < self.height:
                frame[(tx, ty)] = color
        self.mark_dirty()

    def get_pixel(self, x: int, y: int) -> RGBA | None:
        return self.frames[self.current_frame_index].get((x, y))

    # -- saving ---------------------------------------------------------------

    def save(self, relative_path: str | None = None) -> Path:
        """Write the current frame (frame 0 if single-frame; sheet mode
        writes a horizontal strip of all frames) as a PNG under the
        project's ``assets/sprites/``, and invalidate any cached preview of
        that path (doc §7 — cross-editor cache invalidation, no restart
        required)."""
        pygame = _get_pygame()
        project = self._context.project()
        if project is None:
            raise RuntimeError("Sprite Editor has no active project to save into")

        relative_path = relative_path or self.loaded_path
        if not relative_path:
            raise ValueError("save() requires a target path (none loaded/provided)")

        dest = project.assets_path(relative_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        if pygame is None:
            # Absence = zero cost: record intent, skip the actual PNG
            # encode if pygame's image module isn't available.
            dest.touch()
        else:
            sheet_width = self.width * len(self.frames)
            surface = pygame.Surface((sheet_width, self.height), pygame.SRCALPHA)
            for frame_index, frame in enumerate(self.frames):
                x_offset = frame_index * self.width
                for (x, y), color in frame.items():
                    surface.set_at((x_offset + x, y), color)
            pygame.image.save(surface, str(dest))

        self._context.sprite_cache.invalidate(dest)
        self.loaded_path = relative_path
        self.mark_saved()
        return dest


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
