"""Unit tests for engine.render.renderer -- see
docs/components/07-input-renderer-audio.md §8/§9 for the Definition of
Done bullets these correspond to.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import pytest

import engine.render.renderer as renderer_module
from engine.core.ecs import World
from engine.core.events import EventBus
from engine.render.renderer import HUD_WIDTH, TILE_GRID_HEIGHT, TILE_GRID_WIDTH, Renderer
from engine.systems.ai import PlayerTagComponent, PositionComponent


@dataclass
class FakeTile:
    walkable: bool
    room_id: str | None = None
    sprite_ref: str | None = None
    is_lit_room: bool = False


@dataclass
class FakeTileMap:
    width: int
    height: int
    tiles: dict[tuple[int, int], FakeTile] = field(default_factory=dict)


class FakeRegistry:
    def __init__(self, configs: dict[str, dict] | None = None) -> None:
        self._configs = configs or {}

    def get(self, namespace: str, id: str):
        assert namespace == "configs"
        return self._configs.get(id)


@pytest.fixture(scope="module", autouse=True)
def _pygame_headless():
    pygame.init()
    yield
    pygame.quit()


@pytest.fixture(autouse=True)
def _reset_sprite_cache():
    renderer_module._sprite_cache.clear()
    renderer_module._warned_missing_sprites.clear()
    renderer_module._warned_missing_palette.clear()
    yield


def make_renderer(window_size=(1280, 800), **kwargs) -> Renderer:
    world = World()
    bus = EventBus()
    registry = kwargs.pop("registry", FakeRegistry())
    return Renderer(world, registry, bus, window_size=window_size, **kwargs)


def make_all_floor_tilemap(size: int = 100) -> FakeTileMap:
    tiles = {(x, y): FakeTile(walkable=True) for x in range(size) for y in range(size)}
    return FakeTileMap(width=size, height=size, tiles=tiles)


# -- resize / viewport math (§2.2, §8) -----------------------------------------


@pytest.mark.parametrize(
    "window_size",
    [(1280, 800), (1920, 1080), (800, 600), (1000, 500), (2560, 1440)],
)
def test_resize_recomputes_tile_size_and_hud_stays_240px(window_size):
    renderer = make_renderer(window_size)
    hud_rect = renderer.get_hud_rect()
    viewport_rect = renderer.get_viewport_rect()

    window_w, window_h = window_size
    assert hud_rect.width == HUD_WIDTH
    assert hud_rect.x == window_w - HUD_WIDTH
    assert hud_rect.height == window_h

    expected_viewport_w = window_w - HUD_WIDTH
    expected_tile_size = max(
        min(expected_viewport_w // TILE_GRID_WIDTH, window_h // TILE_GRID_HEIGHT), 1
    )
    assert renderer._tile_size == expected_tile_size
    assert viewport_rect.width == expected_tile_size * TILE_GRID_WIDTH
    assert viewport_rect.height == expected_tile_size * TILE_GRID_HEIGHT

    # Letterboxed/centered within the remaining (non-HUD) area.
    leftover_w = expected_viewport_w - viewport_rect.width
    leftover_h = window_h - viewport_rect.height
    assert viewport_rect.x == leftover_w // 2
    assert viewport_rect.y == leftover_h // 2


def test_world_to_screen_respects_viewport_offset_and_tile_size():
    renderer = make_renderer((1280, 800))
    renderer._camera_origin = (0, 0)
    viewport_rect = renderer.get_viewport_rect()

    screen_pos = renderer.world_to_screen((0, 0))
    assert screen_pos == (viewport_rect.x, viewport_rect.y)

    screen_pos_1 = renderer.world_to_screen((1, 1))
    assert screen_pos_1 == (viewport_rect.x + renderer._tile_size, viewport_rect.y + renderer._tile_size)


# -- missing sprite fallback (§8) -----------------------------------------------


def test_missing_sprite_logs_exactly_once_across_repeated_frames(caplog):
    renderer = make_renderer()
    renderer.set_tilemap(make_all_floor_tilemap(size=1))
    tile = renderer._tilemap.tiles[(0, 0)]
    tile.walkable = False
    tile.sprite_ref = "tiles/does_not_exist.png"

    import logging

    with caplog.at_level(logging.WARNING, logger="engine.render.renderer"):
        for _ in range(5):
            renderer.draw_frame()

    missing_warnings = [r for r in caplog.records if "does_not_exist" in r.getMessage()]
    assert len(missing_warnings) == 1


def test_missing_sprite_falls_back_to_palette_color():
    renderer = make_renderer(registry=FakeRegistry({"palette": {"colors": {"wall": [1, 2, 3], "floor": [4, 5, 6]}}}))
    tilemap = make_all_floor_tilemap(size=1)
    tilemap.tiles[(0, 0)] = FakeTile(walkable=False, sprite_ref="tiles/missing.png")
    renderer.set_tilemap(tilemap)

    renderer.draw_frame()

    screen_pos = renderer.world_to_screen((0, 0))
    pixel = renderer._window.get_at(screen_pos)[:3]
    assert tuple(pixel) == (1, 2, 3)


# -- per-tile sprite_ref override bypasses autotile (§2.3, §8) -----------------


def test_per_tile_sprite_ref_bypasses_autotile_lookup():
    theme = {"wall_variants": {f"wall_{i}": "tiles/should_not_be_used.png" for i in range(16)}}
    registry = FakeRegistry({"ui_skin": theme, "palette": {"colors": {"wall": [9, 9, 9], "floor": [1, 1, 1]}}})
    renderer = make_renderer(registry=registry)

    # A real 1x1 surface written to disk so the per-tile override actually
    # resolves to something distinguishable from both the palette fallback
    # and the theme's (deliberately wrong) wall_variants sprite.
    with tempfile.TemporaryDirectory() as tmp_assets:
        sprite_path = os.path.join(tmp_assets, "override.png")
        surface = pygame.Surface((4, 4))
        surface.fill((255, 255, 0))
        pygame.image.save(surface, sprite_path)

        original_root = renderer_module.ASSETS_ROOT
        renderer_module.ASSETS_ROOT = Path(tmp_assets)
        try:
            tilemap = make_all_floor_tilemap(size=1)
            tilemap.tiles[(0, 0)] = FakeTile(walkable=False, sprite_ref="override.png")
            renderer.set_tilemap(tilemap)
            renderer.draw_frame()
        finally:
            renderer_module.ASSETS_ROOT = original_root

    screen_pos = renderer.world_to_screen((0, 0))
    pixel = renderer._window.get_at(screen_pos)[:3]
    assert tuple(pixel) == (255, 255, 0)


# -- camera follow (§2.2, §8) ---------------------------------------------------


def test_camera_recenters_on_player_moved():
    renderer = make_renderer()
    renderer.event_bus.emit("player_moved", {"entity_id": 1, "from": (5, 5), "to": (50, 40)})

    assert renderer._camera_origin == (50 - TILE_GRID_WIDTH // 2, 40 - TILE_GRID_HEIGHT // 2)


def test_camera_recenters_on_entity_moved_only_for_player_tagged_entity():
    world = World()
    bus = EventBus()
    renderer = Renderer(world, FakeRegistry(), bus)

    non_player = world.create_entity()
    world.add_component(non_player, PositionComponent(x=1, y=1))
    bus.emit("entity_moved", {"entity_id": non_player, "from": (0, 0), "to": (30, 30)})
    assert renderer._camera_origin == (0, 0)

    player = world.create_entity()
    world.add_component(player, PositionComponent(x=1, y=1))
    world.add_component(player, PlayerTagComponent())
    bus.emit("entity_moved", {"entity_id": player, "from": (1, 1), "to": (20, 15)})
    assert renderer._camera_origin == (20 - TILE_GRID_WIDTH // 2, 15 - TILE_GRID_HEIGHT // 2)


# -- floor_changed clears caches (§2.2, §8) --------------------------------------


def test_floor_changed_clears_bitmask_cache_and_rebuilds_for_new_tilemap():
    renderer = make_renderer()
    tilemap_1 = make_all_floor_tilemap(size=3)
    tilemap_1.tiles[(1, 1)] = FakeTile(walkable=False)
    renderer.set_tilemap(tilemap_1)
    assert (1, 1) in renderer._bitmask_cache

    tilemap_2 = make_all_floor_tilemap(size=3)
    tilemap_2.tiles[(2, 2)] = FakeTile(walkable=False)

    renderer._get_tilemap = lambda: tilemap_2
    renderer.event_bus.emit("floor_changed", {"from_depth": 1, "to_depth": 2})

    assert (1, 1) not in renderer._bitmask_cache
    assert (2, 2) in renderer._bitmask_cache
    assert renderer._tilemap is tilemap_2


# -- diagonal-corner autotile rendering (§2.3, §9 integration) -------------------


def test_diagonal_corner_tile_renders_via_polygon_fallback_with_zero_assets():
    renderer = make_renderer(
        registry=FakeRegistry({"palette": {"colors": {"wall": [200, 0, 0], "floor": [0, 200, 0]}}})
    )
    tilemap = make_all_floor_tilemap(size=5)
    # (2,2) has wall neighbors to N and E only -> bitmask 3, a diagonal corner.
    tilemap.tiles[(2, 2)] = FakeTile(walkable=False)
    tilemap.tiles[(2, 1)] = FakeTile(walkable=False)
    tilemap.tiles[(3, 2)] = FakeTile(walkable=False)
    renderer.set_tilemap(tilemap)

    renderer.draw_frame()

    x0, y0 = renderer.world_to_screen((2, 2))
    tile_size = renderer._tile_size
    # NE corner of the tile should be wall-colored (the painted triangle);
    # SW corner should remain floor-colored (the untouched half).
    ne_pixel = renderer._window.get_at((x0 + tile_size - 1, y0))[:3]
    sw_pixel = renderer._window.get_at((x0, y0 + tile_size - 1))[:3]
    assert tuple(ne_pixel) == (200, 0, 0)
    assert tuple(sw_pixel) == (0, 200, 0)
