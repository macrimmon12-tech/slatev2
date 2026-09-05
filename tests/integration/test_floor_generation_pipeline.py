"""Integration test required by CONTRACTS.md §7.3 and
docs/components/06-worldgen-campaign.md §8: drives ``generate_floor``'s
real entry point end-to-end (BSP layout + vault injection + real monster
spawning + the §1.1 ``place_floor_loot`` wiring call), and a second case
driving ``FloorManager``/``CampaignSystem`` through real ``stair_use``
events for a floor A -> B -> A round trip.

``04-inventory-items-loot.md`` (``place_floor_loot``) is not merged in this
checkout. Per CONTRACTS.md §8, this test exercises the module's own
documented fallback (a real, harmless no-op — see
``engine/systems/worldgen.py``'s module docstring) for the "wiring call
actually happens" half of the assertion, and separately monkeypatches
``worldgen.place_floor_loot`` to a fixture stand-in that creates real
entities at the returned positions (rather than mocking `generate_floor`
itself) for the "real items land on the map" half — proving the pipeline
threads `place_floor_loot`'s return value through to real world state.
**When 04 merges, replace the monkeypatched stand-in with the real
`place_floor_loot`/`ItemInstanceComponent` and re-run this same shape of
assertion against it** (flagged here and in this component's PR so it
isn't lost, matching 04-inventory-items-loot.md §8's own anticipation of
this follow-up).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.registry import DataRegistry
from engine.systems import campaign, worldgen
from engine.systems.ai import AIComponent, PlayerTagComponent, PositionComponent
from engine.systems.stats import StatsComponent

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "worldgen"


@dataclass
class _FixtureItemMarker:
    """Local stand-in for 04's `ItemInstanceComponent` (not merged in this
    checkout) -- see module docstring. Deliberately NOT decorated with
    `@component`/registered in `engine.core.save._COMPONENT_REGISTRY`: it's
    a test-only marker (`World.query`/`add_component` don't require the
    marker), and this test never saves/loads it."""

    item_base_id: str


def _fixture_registry() -> DataRegistry:
    registry = DataRegistry()
    registry.load(FIXTURES)
    return registry


def _reset():
    worldgen.reset_module_state()
    campaign.set_data_registry(None)


def test_real_floor_generation_produces_room_id_coverage_monsters_and_loot(monkeypatch):
    _reset()
    registry = _fixture_registry()
    worldgen.set_data_registry(registry)

    # Fixture stand-in for 04's place_floor_loot (§1.1/§8) -- creates real
    # entities at the positions it returns, so downstream assertions check
    # genuine world state, not a mock call log.
    def fixture_place_floor_loot(world, floor_id, depth):
        placements = []
        candidates = [pos for pos, tile in worldgen.get_tilemap(floor_id).tiles.items() if tile.walkable]
        for i, pos in enumerate(candidates[:3]):
            entity_id = world.create_entity()
            world.add_component(entity_id, PositionComponent(*pos))
            world.add_component(entity_id, _FixtureItemMarker(item_base_id="fixture_potion"))
            placements.append({"instance_id": f"item_{i}", "item_base_id": "fixture_potion", "position": pos})
        return placements

    monkeypatch.setattr(worldgen, "place_floor_loot", fixture_place_floor_loot)

    world = World()
    bus = EventBus()
    spawned_events = []
    bus.subscribe("entity_spawned", lambda payload: spawned_events.append(payload))

    level_def = {
        "id": "integration_floor",
        "depth": 1,
        "width": 45,
        "height": 32,
        "monster_spawns": [{"entity_id": "fixture_rat", "count": [3, 5]}],
        "vaults": [{"vault_id": "fixture_guaranteed_vault", "guaranteed": True}],
    }

    floor_id = worldgen.generate_floor(level_def, world, random.Random(11), event_bus=bus)
    tilemap = worldgen.get_tilemap(floor_id)

    # (a) room_id populated on every walkable tile, whole-map, not spot-checked.
    walkable = [pos for pos, tile in tilemap.tiles.items() if tile.walkable]
    assert walkable
    assert all(tilemap.tiles[pos].room_id is not None for pos in walkable)

    # (b) the guaranteed vault's tiles actually appear in the generated map.
    vault_rooms = [room for room in tilemap.rooms.values() if room.get("vault_id") == "fixture_guaranteed_vault"]
    assert len(vault_rooms) == 1
    origin_x, origin_y = vault_rooms[0]["vault_origin"]
    # The vault's own template has a '#' at local (2,0) and '.' at (1,1).
    assert tilemap.tiles[(origin_x + 2, origin_y + 0)].walkable is False
    assert tilemap.tiles[(origin_x + 1, origin_y + 1)].walkable is True

    # (c) real monster entities exist, matching monster_spawns' entity id
    # and roughly the right count (3-5 from monster_spawns + 1 from the
    # vault's own entity_spawns).
    ai_rows = world.query(AIComponent)
    assert 4 <= len(ai_rows) <= 6
    for entity_id, ai in ai_rows:
        assert ai.behavior == "chaser"
        assert world.get_component(entity_id, StatsComponent) is not None
    assert all(evt["kind"] == "fixture_rat" for evt in spawned_events)

    # (d) real item entities exist on the tile map afterward, at walkable
    # positions, via the (fixture-standing-in-for-04) place_floor_loot call
    # that generate_floor actually invoked.
    item_rows = world.query(_FixtureItemMarker)
    assert len(item_rows) == 3
    for entity_id, marker in item_rows:
        position = world.get_component(entity_id, PositionComponent)
        assert position is not None
        assert tilemap.tiles[(position.x, position.y)].walkable is True

    _reset()


def test_floor_manager_round_trip_preserves_state_via_real_stair_use_events():
    """A second integration case: floor A -> B -> A, asserting A's state
    (a monster killed before leaving) persisted across the round trip via
    the real snapshot/restore path, driven through real `stair_use` events
    (not direct internal method calls) to also prove CampaignSystem's
    event subscription is wired to FloorManager."""
    _reset()
    registry = _fixture_registry()
    worldgen.set_data_registry(registry)
    campaign.set_data_registry(registry)

    world = World()
    bus = EventBus()
    player_id = world.create_entity()
    world.add_component(player_id, PlayerTagComponent())
    world.add_component(player_id, PositionComponent(0, 0))

    cs = campaign.CampaignSystem(world, bus, rng=random.Random(3))
    cs.load_campaign("sample_campaign")

    cs.enter_level("level_a")
    monster_ids = [row[0] for row in world.query(AIComponent)]
    assert monster_ids
    for monster_id in monster_ids:
        world.destroy_entity(monster_id)

    bus.emit("stair_use", {"entity_id": player_id, "direction": "down"})
    assert cs.current_level_id == "level_deep_but_early"

    bus.emit("stair_use", {"entity_id": player_id, "direction": "up"})
    assert cs.current_level_id == "level_a"

    # Restored, not regenerated -- the killed monster stays gone.
    assert world.query(AIComponent) == []
    assert world.get_component(player_id, PlayerTagComponent) is not None

    _reset()
