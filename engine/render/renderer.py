"""Renderer: the pygame-ce window, tile viewport, camera follow, and the
HUD-rect handoff to UI runtime.

See ``docs/components/07-input-renderer-audio.md`` §2.2 for the full spec.
This module draws whatever state the ECS/tilemap currently hold and never
decides game rules -- panel/widget content is `08-ui-runtime.md`'s job
(this module only hands it a rect to draw into); particle/VFX shapes are
`09-animation-vfx.md`'s job.

Genuine spec/coordination gaps resolved here (flagged per CONTRACTS.md §8
and the component doc's Open Questions §10, matching the precedent set by
``engine.systems.ai``/``engine.systems.vision`` of documenting rather than
silently guessing):

1. **Tilemap access.** `06-worldgen-campaign.md` owns ``TileMap``
   (``tiles: dict[(x, y), Tile]``, ``Tile.walkable``/``sprite_ref``) but
   isn't merged and can't be imported (CONTRACTS.md §8). This module works
   against that documented shape via a minimal duck-typed
   ``Protocol`` (see ``engine.render.autotile``) rather than defining a
   second, competing ``TileMap`` class. ``Renderer`` exposes
   :meth:`set_tilemap` as the integration point whoever wires floor
   transitions calls; :meth:`draw_frame` degrades to drawing nothing but
   background in the viewport if none has been set (absence = zero cost).
2. **``floor_changed`` doesn't carry a tilemap.** CONTRACTS.md §3.2 gives
   ``floor_changed``'s payload as ``from_depth, to_depth`` only -- no way
   for a bare event subscriber to obtain the new floor's tilemap. The
   constructor accepts an optional ``get_tilemap: Callable[[], TileMapLike]``
   callable; on ``floor_changed`` this is invoked (if provided) to refresh
   ``self._tilemap`` before rebuilding the bitmask cache, otherwise the
   handler only clears caches and relies on a fresh :meth:`set_tilemap`
   call from whatever owns the transition (either ordering works: both
   paths always end with the wall-bitmask cache fully invalidated for the
   new tilemap, per §2.2's "rebuild the autotile bitmask" requirement).
3. **HUD content / UI runtime.** `08-ui-runtime.md` isn't merged. The
   constructor accepts an optional ``ui_runtime`` object; if given,
   ``draw_frame`` calls ``ui_runtime.draw(surface, hud_rect)`` after
   filling the HUD panel's background, otherwise the HUD panel is just a
   flat background-colored rect (absence = zero cost) -- this component
   never parses widget-tree JSON either way (§1 out-of-scope).
4. **Theme JSON (``wall_variants``).** Owned as an authoring surface by
   `13-editor-core-authoring.md`'s Sprite Manager and doesn't exist yet.
   Read (if present) from ``registry.get("configs", "ui_skin")`` -- the one
   `config file name CONTRACTS.md §1 lists that plausibly names a visual
   theme/skin. Every ``wall_N`` key is looked up independently (§2.3); a
   missing file, a missing ``wall_variants`` block, or a missing individual
   ``wall_N`` entry all degrade to the same per-variant colored-rect (or
   diagonal-triangle) fallback, never an aliased single generic sprite.
5. **Palette config (Open Questions §10).** No component doc claims
   ``data/config/palette.json``. Per this doc's own stated default ("if no
   such file is specified elsewhere, define a minimal schema here"), this
   component introduces ``data/config/palette.json`` (flat
   ``{"colors": {content_type: [r, g, b]}}``, see the file itself) as a new
   ``configs``-namespace file -- flagged in the PR for anyone assigning it
   a more permanent owner later.
6. **Entity sprites.** No merged component defines a "this entity's visual"
   component (`09-animation-vfx.md` most plausibly would, but isn't
   merged). Every entity with a `PositionComponent` currently renders as a
   flat colored rect (player color vs. a generic entity color from the
   palette) -- real per-entity sprite lookup is deferred to whichever
   component lands that data, at which point this module's per-tile sprite
   lookup path (already fully general) is the template to extend, not a
   rewrite.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import pygame

from engine.core.ecs import World
from engine.core.events import EventBus

# See module docstring, gap 6 / engine.input.input_handler's gap 2 --
# reusing ai.py's provisional PositionComponent/PlayerTagComponent rather
# than redefining a third, incompatible pair.
from engine.systems.ai import PlayerTagComponent, PositionComponent

from engine.render.autotile import (
    compute_wall_bitmask,
    draw_diagonal_corner_fallback,
    is_diagonal_corner,
    wall_sprite_key,
)

logger = logging.getLogger(__name__)

Position = tuple[int, int]
Color = tuple[int, int, int]

TILE_GRID_WIDTH = 80
TILE_GRID_HEIGHT = 50
HUD_WIDTH = 240

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ASSETS_ROOT = PROJECT_ROOT / "assets"

_FALLBACK_COLORS: dict[str, Color] = {
    "wall": (90, 90, 100),
    "floor": (40, 40, 46),
    "entity_default": (200, 60, 60),
    "player": (60, 160, 220),
    "hud_background": (24, 24, 28),
    "missing": (255, 0, 255),
}

# Module-level "log once" sets, per CONTRACTS.md §9 -- shared across every
# Renderer instance in a process, matching the missing-sound cache
# convention used in engine.audio.audio_system.
_warned_missing_sprites: set[str] = set()
_warned_missing_palette: set[str] = set()
_sprite_cache: dict[str, pygame.Surface | None] = {}


def _load_sprite(path: str) -> pygame.Surface | None:
    """Load and cache a sprite from ``ASSETS_ROOT / path``. A missing/
    unreadable file is cached as ``None`` (logged exactly once for that
    path) so repeated draw calls referencing it never re-stat the
    filesystem, mirroring `AudioSystem`'s missing-file cache."""
    if path in _sprite_cache:
        return _sprite_cache[path]

    full_path = ASSETS_ROOT / path
    surface: pygame.Surface | None
    try:
        surface = pygame.image.load(str(full_path)).convert_alpha()
    except (FileNotFoundError, OSError, pygame.error):
        surface = None
        if path not in _warned_missing_sprites:
            logger.warning("Missing sprite %r (looked for %s); using palette fallback color.", path, full_path)
            _warned_missing_sprites.add(path)

    _sprite_cache[path] = surface
    return surface


class Renderer:
    """Owns the pygame-ce window: tile viewport + HUD panel, camera
    follow, and sprite/autotile lookup with fallback."""

    def __init__(
        self,
        world: World,
        registry: Any,
        event_bus: EventBus,
        window_size: tuple[int, int] = (1280, 800),
        *,
        get_tilemap: Callable[[], Any] | None = None,
        ui_runtime: Any | None = None,
    ) -> None:
        self.world = world
        self.registry = registry
        self.event_bus = event_bus
        self._get_tilemap = get_tilemap
        self._ui_runtime = ui_runtime

        self._tilemap: Any | None = None
        self._bitmask_cache: dict[Position, int] = {}
        self._camera_origin: Position = (0, 0)

        pygame.init()
        self._window: pygame.Surface = pygame.display.set_mode(window_size, pygame.RESIZABLE)
        self._viewport_rect = pygame.Rect(0, 0, 0, 0)
        self._hud_rect = pygame.Rect(0, 0, 0, 0)
        self._tile_size = 1
        self.resize(window_size)

        self._palette = registry.get("configs", "palette") if registry is not None else None
        self._theme = registry.get("configs", "ui_skin") if registry is not None else None

        event_bus.subscribe("entity_moved", self._on_entity_moved)
        event_bus.subscribe("player_moved", self._on_player_moved)
        event_bus.subscribe("floor_changed", self._on_floor_changed)

    # -- public API (component doc §2.2) -------------------------------------

    def resize(self, new_size: tuple[int, int]) -> None:
        window_w, window_h = new_size
        self._window = pygame.display.set_mode(new_size, pygame.RESIZABLE)
        self._window_size = (window_w, window_h)

        viewport_w = max(window_w - HUD_WIDTH, 0)
        viewport_h = max(window_h, 0)
        tile_size = max(
            min(viewport_w // TILE_GRID_WIDTH, viewport_h // TILE_GRID_HEIGHT), 1
        )
        self._tile_size = tile_size

        drawn_w = tile_size * TILE_GRID_WIDTH
        drawn_h = tile_size * TILE_GRID_HEIGHT
        offset_x = (viewport_w - drawn_w) // 2
        offset_y = (viewport_h - drawn_h) // 2
        self._viewport_rect = pygame.Rect(offset_x, offset_y, drawn_w, drawn_h)
        self._hud_rect = pygame.Rect(window_w - HUD_WIDTH, 0, HUD_WIDTH, window_h)

    def get_hud_rect(self) -> pygame.Rect:
        return self._hud_rect.copy()

    def get_viewport_rect(self) -> pygame.Rect:
        return self._viewport_rect.copy()

    def world_to_screen(self, tile_pos: Position) -> tuple[int, int]:
        tx, ty = tile_pos
        ox, oy = self._camera_origin
        return (
            self._viewport_rect.x + (tx - ox) * self._tile_size,
            self._viewport_rect.y + (ty - oy) * self._tile_size,
        )

    def set_tilemap(self, tilemap: Any) -> None:
        """Integration point for whoever owns floor transitions (see
        module docstring gap 2). Replaces the active tilemap and rebuilds
        the wall-bitmask cache for it."""
        self._tilemap = tilemap
        self._rebuild_bitmask_cache()

    def draw_frame(self) -> None:
        """Draw the tile viewport, then hand the HUD rect off (see module
        docstring gap 3). Does not flip the display -- callers compose
        this with whatever else they draw per frame before flipping."""
        self._window.fill(self._resolve_color("floor"))
        self._draw_viewport()
        self._draw_entities()
        self._draw_hud()

    # -- drawing --------------------------------------------------------------

    def _draw_viewport(self) -> None:
        if self._tilemap is None:
            return
        ox, oy = self._camera_origin
        for row in range(TILE_GRID_HEIGHT):
            for col in range(TILE_GRID_WIDTH):
                tile_x, tile_y = ox + col, oy + row
                tile = self._tilemap.tiles.get((tile_x, tile_y))
                if tile is None:
                    continue
                screen_x, screen_y = self.world_to_screen((tile_x, tile_y))
                rect = pygame.Rect(screen_x, screen_y, self._tile_size, self._tile_size)
                self._draw_tile(rect, tile, tile_x, tile_y)

    def _draw_tile(self, rect: pygame.Rect, tile: Any, x: int, y: int) -> None:
        sprite_ref = getattr(tile, "sprite_ref", None)
        if sprite_ref:
            # Per-tile override always wins over autotile lookup (§2.3).
            surface = _load_sprite(sprite_ref)
            if surface is not None:
                self._window.blit(pygame.transform.scale(surface, rect.size), rect)
                return

        if tile.walkable:
            pygame.draw.rect(self._window, self._resolve_color("floor"), rect)
            return

        bitmask = self._bitmask_cache.get((x, y))
        if bitmask is None:
            bitmask = compute_wall_bitmask(self._tilemap, x, y)
            self._bitmask_cache[(x, y)] = bitmask

        surface = self._wall_sprite_surface(bitmask)
        if surface is not None:
            self._window.blit(pygame.transform.scale(surface, rect.size), rect)
            return

        wall_color = self._resolve_color("wall")
        if is_diagonal_corner(bitmask):
            # Zero-asset diagonal corridor fallback (§2.3): floor-colored
            # base, wall-colored triangle covering the wall-mass corner.
            pygame.draw.rect(self._window, self._resolve_color("floor"), rect)
            draw_diagonal_corner_fallback(self._window, rect, bitmask, wall_color)
        else:
            pygame.draw.rect(self._window, wall_color, rect)

    def _wall_sprite_surface(self, bitmask: int) -> pygame.Surface | None:
        variants = (self._theme or {}).get("wall_variants", {})
        path = variants.get(wall_sprite_key(bitmask))
        if not path:
            return None
        return _load_sprite(path)

    def _draw_entities(self) -> None:
        for entity_id, position in self.world.query(PositionComponent):
            screen_x, screen_y = self.world_to_screen((position.x, position.y))
            rect = pygame.Rect(screen_x, screen_y, self._tile_size, self._tile_size)
            if not self._viewport_rect.colliderect(rect):
                continue
            is_player = self.world.get_component(entity_id, PlayerTagComponent) is not None
            color = self._resolve_color("player" if is_player else "entity_default")
            pygame.draw.rect(self._window, color, rect)

    def _draw_hud(self) -> None:
        pygame.draw.rect(self._window, self._resolve_color("hud_background"), self._hud_rect)
        if self._ui_runtime is not None:
            self._ui_runtime.draw(self._window, self._hud_rect)

    # -- palette --------------------------------------------------------------

    def _resolve_color(self, key: str) -> Color:
        colors = (self._palette or {}).get("colors") if self._palette else None
        if colors:
            value = colors.get(key) or colors.get("missing")
            if value is not None:
                return tuple(value)  # type: ignore[return-value]
        if "palette_missing" not in _warned_missing_palette:
            logger.warning(
                "data/config/palette.json missing or malformed; using built-in fallback colors."
            )
            _warned_missing_palette.add("palette_missing")
        return _FALLBACK_COLORS.get(key, _FALLBACK_COLORS["missing"])

    # -- bitmask cache ----------------------------------------------------------

    def _rebuild_bitmask_cache(self) -> None:
        self._bitmask_cache = {}
        if self._tilemap is None:
            return
        for (x, y), tile in self._tilemap.tiles.items():
            if not tile.walkable:
                self._bitmask_cache[(x, y)] = compute_wall_bitmask(self._tilemap, x, y)

    # -- event subscriptions (§2.2, §3) ----------------------------------------

    def _on_entity_moved(self, payload: dict | None) -> None:
        if not payload:
            return
        entity_id = payload.get("entity_id")
        if entity_id is not None and self.world.get_component(entity_id, PlayerTagComponent) is not None:
            self._recenter_camera(payload.get("to"))

    def _on_player_moved(self, payload: dict | None) -> None:
        if not payload:
            return
        self._recenter_camera(payload.get("to"))

    def _recenter_camera(self, to_pos: Any) -> None:
        if not to_pos:
            return
        tx, ty = to_pos
        self._camera_origin = (tx - TILE_GRID_WIDTH // 2, ty - TILE_GRID_HEIGHT // 2)

    def _on_floor_changed(self, _payload: dict | None) -> None:
        # Drop cached tile surfaces for the previous floor and rebuild the
        # bitmask cache for the new one (§2.2). Sprite *image* data stays
        # cached globally by path (engine.render.renderer._sprite_cache) --
        # only the per-position bitmask cache is floor-scoped.
        if self._get_tilemap is not None:
            self._tilemap = self._get_tilemap()
        self._rebuild_bitmask_cache()


__all__ = ["Renderer", "TILE_GRID_WIDTH", "TILE_GRID_HEIGHT", "HUD_WIDTH"]
