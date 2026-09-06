"""Integration test for 07-input-renderer-audio.md (component doc §9 /
CONTRACTS.md §7.3): drives the real InputHandler/Renderer/AudioSystem
entry points end-to-end against a fixture floor + fixture configs, not
mocks of them.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from engine.audio.audio_system import AudioSystem
from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.spatial_hash import SpatialHash
from engine.input.input_handler import InputHandler
from engine.render.autotile import compute_wall_bitmask, is_diagonal_corner
from engine.render.renderer import Renderer
from engine.systems.ai import PlayerTagComponent, PositionComponent

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture(scope="module", autouse=True)
def _pygame_headless():
    pygame.init()
    pygame.mixer.init()
    yield
    pygame.quit()


@dataclass
class FixtureTile:
    walkable: bool
    room_id: str | None = None
    sprite_ref: str | None = None
    is_lit_room: bool = False


@dataclass
class FixtureTileMap:
    width: int
    height: int
    tiles: dict[tuple[int, int], FixtureTile] = field(default_factory=dict)


def build_fixture_floor() -> FixtureTileMap:
    """A small floor: an open room, plus a diagonal corridor bend built
    from wall tiles whose bitmask lands on one of the four inner-corner
    variants (3, 6, 9, 12)."""
    size = 12
    tiles = {(x, y): FixtureTile(walkable=True) for x in range(size) for y in range(size)}

    # Enclose the whole floor in walls (off-map already counts as wall,
    # this just makes the walls explicit/visible in the fixture).
    for x in range(size):
        for y in range(size):
            if x in (0, size - 1) or y in (0, size - 1):
                tiles[(x, y)] = FixtureTile(walkable=False)

    # A diagonal corridor bend at (6, 6): wall neighbors to N and E only
    # -> bitmask 3, one of the four diagonal-corner variants.
    tiles[(6, 6)] = FixtureTile(walkable=False)
    tiles[(6, 5)] = FixtureTile(walkable=False)  # north neighbor
    tiles[(7, 6)] = FixtureTile(walkable=False)  # east neighbor

    return FixtureTileMap(width=size, height=size, tiles=tiles)


class FakeRegistry:
    def __init__(self, configs: dict[str, dict]) -> None:
        self._configs = configs

    def get(self, namespace: str, id: str):
        assert namespace == "configs"
        return self._configs.get(id)


def load_fixture(name: str) -> dict:
    with (FIXTURES / name).open() as fh:
        return json.load(fh)


def key_event(key_code: int) -> pygame.event.Event:
    return pygame.event.Event(pygame.KEYDOWN, key=key_code)


def test_input_render_audio_pipeline():
    world = World()
    bus = EventBus()
    spatial_hash = SpatialHash()

    # -- player entity, wired through both PositionComponent and SpatialHash
    player_id = world.create_entity()
    world.add_component(player_id, PositionComponent(x=3, y=3))
    world.add_component(player_id, PlayerTagComponent())
    spatial_hash.insert(player_id, (3, 3))

    registry = FakeRegistry(
        {
            "audio_config": load_fixture("audio_config_minimal.json"),
        }
    )

    input_handler = InputHandler(
        bus,
        load_fixture("controls_minimal.json"),
        world=world,
        spatial_hash=spatial_hash,
        is_passable=lambda pos: True,
    )
    renderer = Renderer(world, registry, bus)
    audio = AudioSystem(bus, registry)

    tilemap = build_fixture_floor()
    renderer.set_tilemap(tilemap)

    # -- drive a real key-down for move_east through the real InputHandler
    input_handler.handle_pygame_event(key_event(pygame.key.key_code("Right")))

    position = world.get_component(player_id, PositionComponent)
    assert (position.x, position.y) == (4, 3)
    assert spatial_hash.position_of(player_id) == (4, 3)

    # -- a real damage_dealt event should make AudioSystem attempt to play
    # the config-mapped sound; mock only the pygame boundary, not dispatch.
    with patch("pathlib.Path.exists", return_value=True):
        with patch("pygame.mixer.Sound") as mock_sound_cls:
            bus.emit(
                "damage_dealt",
                {"target_id": player_id, "amount": 4, "damage_type": "fire", "source_id": 99},
            )
    mock_sound_cls.assert_called_once()
    assert mock_sound_cls.call_args[0][0].endswith("sfx/hit_fire.ogg")
    mock_sound_cls.return_value.play.assert_called_once()

    # -- draw_frame against the fixture floor selects the diagonal-corner
    # polygon fallback for the corridor-bend tile (bitmask 3).
    bitmask = compute_wall_bitmask(tilemap, 6, 6)
    assert bitmask == 3
    assert is_diagonal_corner(bitmask)

    renderer.draw_frame()

    x0, y0 = renderer.world_to_screen((6, 6))
    tile_size = renderer._tile_size
    ne_pixel = tuple(renderer._window.get_at((x0 + tile_size - 1, y0))[:3])
    sw_pixel = tuple(renderer._window.get_at((x0, y0 + tile_size - 1))[:3])
    wall_color = tuple(renderer._resolve_color("wall"))
    floor_color = tuple(renderer._resolve_color("floor"))
    assert ne_pixel == wall_color
    assert sw_pixel == floor_color
