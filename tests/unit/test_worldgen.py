"""Unit tests for engine/systems/worldgen.py — see
docs/components/06-worldgen-campaign.md §7/§8."""

from __future__ import annotations

import random
from collections import deque
from pathlib import Path

import pytest

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.registry import DataRegistry
from engine.systems import worldgen
from engine.systems.ai import AIComponent, PositionComponent
from engine.systems.stats import StatsComponent

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "worldgen"


@pytest.fixture(autouse=True)
def _clean_module_state():
    worldgen.reset_module_state()
    yield
    worldgen.reset_module_state()


def _walkable_positions(tilemap: worldgen.TileMap) -> list[tuple[int, int]]:
    return [pos for pos, tile in tilemap.tiles.items() if tile.walkable]


def _is_fully_connected(tilemap: worldgen.TileMap) -> bool:
    walkable = set(_walkable_positions(tilemap))
    if not walkable:
        return True
    start = next(iter(walkable))
    seen = {start}
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                neighbor = (x + dx, y + dy)
                if neighbor in walkable and neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
    return seen == walkable


# ---------------------------------------------------------------------------
# BSP layout — §7 room_id hard requirement + connectivity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(10))
def test_bsp_layout_every_walkable_tile_has_room_id(seed):
    tilemap = worldgen._generate_bsp_layout(50, 35, random.Random(seed))
    walkable = _walkable_positions(tilemap)
    assert walkable, "BSP layout produced no walkable tiles at all"
    assert all(tilemap.tiles[pos].room_id is not None for pos in walkable)


@pytest.mark.parametrize("seed", range(10))
def test_bsp_layout_is_fully_connected(seed):
    tilemap = worldgen._generate_bsp_layout(50, 35, random.Random(seed))
    assert _is_fully_connected(tilemap)


def test_bsp_layout_corridor_tiles_get_non_room_sentinel_room_id():
    tilemap = worldgen._generate_bsp_layout(50, 35, random.Random(7))
    corridor_room_ids = {
        tile.room_id for tile in tilemap.tiles.values() if tile.walkable and tile.room_id not in tilemap.rooms
    }
    assert corridor_room_ids, "expected at least one corridor tile with a non-room room_id"
    assert all(rid.startswith("corridor") for rid in corridor_room_ids)


def test_walls_have_no_room_id():
    tilemap = worldgen._generate_bsp_layout(50, 35, random.Random(3))
    non_walkable = [pos for pos, tile in tilemap.tiles.items() if not tile.walkable]
    assert non_walkable
    assert all(tilemap.tiles[pos].room_id is None for pos in non_walkable)


# ---------------------------------------------------------------------------
# TileMap serialization round trip
# ---------------------------------------------------------------------------


def test_tilemap_to_dict_from_dict_round_trip():
    tilemap = worldgen._generate_bsp_layout(20, 15, random.Random(1))
    tilemap.stairs_down = next(iter(_walkable_positions(tilemap)))
    restored = worldgen.tilemap_from_dict(worldgen.tilemap_to_dict(tilemap))
    assert restored.width == tilemap.width
    assert restored.height == tilemap.height
    assert restored.stairs_down == tilemap.stairs_down
    assert set(restored.tiles) == set(tilemap.tiles)
    for pos, tile in tilemap.tiles.items():
        assert restored.tiles[pos] == tile
    assert set(restored.rooms) == set(tilemap.rooms)


# ---------------------------------------------------------------------------
# generate_floor pipeline wiring (§1.1 hard requirement)
# ---------------------------------------------------------------------------


def _load_fixture_registry() -> DataRegistry:
    registry = DataRegistry()
    registry.load(FIXTURES)
    return registry


def test_generate_floor_calls_place_floor_loot(monkeypatch):
    """§7: verified by a spy counting invocations, independent of whether
    04-inventory-items-loot.md has merged (this repo checkout doesn't have
    it yet -- see the module docstring's §1.1 fallback)."""
    registry = _load_fixture_registry()
    worldgen.set_data_registry(registry)

    calls = []

    def spy(world, floor_id, depth):
        calls.append((floor_id, depth))
        return []

    monkeypatch.setattr(worldgen, "place_floor_loot", spy)

    world = World()
    level_def = {"id": "spy_floor", "depth": 3, "width": 30, "height": 20, "monster_spawns": [], "vaults": []}
    floor_id = worldgen.generate_floor(level_def, world, random.Random(1))

    assert calls == [(floor_id, 3)]


def test_generate_floor_spawns_real_monster_entities():
    registry = _load_fixture_registry()
    worldgen.set_data_registry(registry)

    world = World()
    bus = EventBus()
    spawned_events = []
    bus.subscribe("entity_spawned", lambda payload: spawned_events.append(payload))

    level_def = {
        "id": "monster_floor",
        "depth": 1,
        "width": 40,
        "height": 30,
        "monster_spawns": [{"entity_id": "fixture_rat", "count": [3, 3]}],
        "vaults": [],
    }
    worldgen.generate_floor(level_def, world, random.Random(2), event_bus=bus)

    ai_rows = world.query(AIComponent)
    assert len(ai_rows) == 3
    for entity_id, ai in ai_rows:
        assert ai.behavior == "chaser"
        stats = world.get_component(entity_id, StatsComponent)
        assert stats is not None
        assert stats.base["hp"] == 6
        position = world.get_component(entity_id, PositionComponent)
        assert position is not None

    assert len(spawned_events) == 3
    assert all(evt["kind"] == "fixture_rat" for evt in spawned_events)


def test_generate_floor_skips_unresolvable_monster_entries_without_raising():
    registry = _load_fixture_registry()
    worldgen.set_data_registry(registry)
    world = World()
    level_def = {
        "id": "bad_monster_floor",
        "depth": 1,
        "width": 30,
        "height": 20,
        "monster_spawns": [{"entity_id": "no_such_monster", "count": [1, 1]}],
        "vaults": [],
    }
    # Absence = zero cost -- must not raise.
    worldgen.generate_floor(level_def, world, random.Random(1))
    assert world.query(AIComponent) == []


def test_generate_floor_with_no_data_registry_is_a_harmless_no_op_for_spawning():
    world = World()
    level_def = {"id": "no_registry_floor", "depth": 1, "width": 30, "height": 20, "monster_spawns": [{"entity_id": "fixture_rat", "count": [2, 2]}], "vaults": []}
    worldgen.generate_floor(level_def, world, random.Random(1))
    assert world.query(AIComponent) == []


def test_generate_floor_stamps_guaranteed_vault_and_realizes_its_spawns():
    registry = _load_fixture_registry()
    worldgen.set_data_registry(registry)
    world = World()

    level_def = {
        "id": "vault_floor",
        "depth": 1,
        "width": 40,
        "height": 30,
        "monster_spawns": [],
        "vaults": [{"vault_id": "fixture_guaranteed_vault", "guaranteed": True}],
    }
    floor_id = worldgen.generate_floor(level_def, world, random.Random(5))

    tilemap = worldgen.get_tilemap(floor_id)
    vault_rooms = [room for room in tilemap.rooms.values() if room.get("vault_id") == "fixture_guaranteed_vault"]
    assert len(vault_rooms) == 1

    # The vault's own entity_spawns entry (fixture_rat) must have been
    # realized as a real entity, not merely recorded.
    ai_rows = world.query(AIComponent)
    assert len(ai_rows) == 1


# ---------------------------------------------------------------------------
# current_depth / set_current_depth (01's combat.py wiring point)
# ---------------------------------------------------------------------------


def test_generate_floor_pushes_room_data_to_registered_vision_system():
    """05-progression-vision.md's VisionSystem exposes
    register_floor_rooms(floor_id, rooms) specifically inviting "06 (or
    whatever wires floors up in the meantime)" to call it -- this closes
    that loop."""
    registry = _load_fixture_registry()
    worldgen.set_data_registry(registry)

    class _FixtureVisionSystem:
        def __init__(self):
            self.calls = []

        def register_floor_rooms(self, floor_id, rooms):
            self.calls.append((floor_id, rooms))

    vision = _FixtureVisionSystem()
    worldgen.set_vision_system(vision)

    world = World()
    level_def = {"id": "vision_floor", "depth": 1, "width": 30, "height": 20, "monster_spawns": [], "vaults": []}
    floor_id = worldgen.generate_floor(level_def, world, random.Random(1))

    assert len(vision.calls) == 1
    called_floor_id, rooms = vision.calls[0]
    assert called_floor_id == floor_id
    assert rooms  # at least one room payload
    for room in rooms:
        assert set(room) == {"room_id", "lit", "tiles"}


def test_current_depth_defaults_to_one_then_reflects_set_current_depth():
    world = World()
    assert worldgen.current_depth(world) == 1
    worldgen.set_current_depth(world, 5)
    assert worldgen.current_depth(world) == 5

    other_world = World()
    assert worldgen.current_depth(other_world) == 1


# ---------------------------------------------------------------------------
# worldgen.json config validation
# ---------------------------------------------------------------------------


def test_validate_worldgen_config_accepts_the_real_shipped_config():
    import json

    data = json.loads((Path(__file__).resolve().parent.parent.parent / "data" / "config" / "worldgen.json").read_text())
    assert worldgen.validate_worldgen_config(data) == []


def test_validate_worldgen_config_rejects_missing_fields():
    errors = worldgen.validate_worldgen_config({"bsp": {}, "vault_fill_probability": "nope"})
    assert errors
