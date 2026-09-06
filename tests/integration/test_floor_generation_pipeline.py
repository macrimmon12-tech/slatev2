"""Integration test required by CONTRACTS.md §7.3 and
docs/components/06-worldgen-campaign.md §8: drives ``generate_floor``'s
real entry point end-to-end (BSP layout + vault injection + real monster
spawning + the §1.1 ``place_floor_loot`` wiring call), and a second case
driving ``FloorManager``/``CampaignSystem`` through real ``stair_use``
events for a floor A -> B -> A round trip.

**15-integration-verification.md update**: `04-inventory-items-loot.md` has
now merged, so this re-points the "real items land on the map" half of the
assertion at the real `place_floor_loot`/`ItemInstanceComponent`/
`GoldComponent` instead of the fixture stand-in this test used to
monkeypatch in — per this component doc's own anticipation of exactly this
follow-up. Doing so surfaced a genuine cross-component wiring gap (the
"damage vs damage_dealt" class of bug CONTRACTS.md §7.1 generalizes):
`place_floor_loot` read tile eligibility exclusively via
`FloorTileComponent` ECS entities (04's documented convention), but `06`'s
real `generate_floor` never creates those — it keeps its own `TileMap` in
`engine.systems.worldgen`'s module-level registry, exactly the "equivalent
bridge" `04`'s own `loot.py` docstring flagged as still needed. Fixed here
as a small, in-scope wiring fix (`engine/systems/loot.py`'s
`_floor_tiles_from_worldgen` bridge) rather than filed as a follow-up,
since both docs already agreed on the *behavior* (scatter loot on eligible
floor tiles) and only the tile-access *shape* had drifted.
"""

from __future__ import annotations

import random
from pathlib import Path

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.registry import DataRegistry
from engine.systems import campaign, loot, worldgen
from engine.systems.ai import AIComponent, PlayerTagComponent, PositionComponent
from engine.systems.inventory import ItemInstanceComponent, ItemPositionComponent
from engine.systems.loot import GoldComponent
from engine.systems.stats import StatsComponent

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "worldgen"


def _fixture_registry() -> DataRegistry:
    registry = DataRegistry()
    registry.load(FIXTURES)
    return registry


def _reset():
    worldgen.reset_module_state()
    campaign.set_data_registry(None)
    loot.set_active_registry(None)
    loot.set_active_spawned_uniques(set())


def test_real_floor_generation_produces_room_id_coverage_monsters_and_loot():
    _reset()
    registry = _fixture_registry()
    worldgen.set_data_registry(registry)
    # §1.1's wiring call is only real end-to-end once 04's LootSystem has a
    # registry to read the floor-loot config/item pool from (see
    # loot.py's module docstring's "Registry access" wiring note) -- this
    # is the real place_floor_loot now, not a monkeypatched stand-in.
    loot.set_active_registry(registry)

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

    # (d) real ambient floor loot (04's real place_floor_loot, real
    # ItemInstanceComponent/GoldComponent entities -- not a fixture stand-in)
    # actually lands on the generated tile map, at walkable positions, via
    # the §1.1 wiring call generate_floor made for real. This is the exact
    # "damage vs damage_dealt" class of cross-component drift 15's own doc
    # calls out: 04's place_floor_loot only read tile eligibility via
    # FloorTileComponent entities, which 06's real generate_floor never
    # creates -- see engine/systems/loot.py's bridge fix.
    item_rows = world.query(ItemInstanceComponent)
    gold_rows = world.query(GoldComponent)
    assert item_rows or gold_rows
    for entity_id, _item in item_rows:
        position = world.get_component(entity_id, ItemPositionComponent)
        assert position is not None
        assert tilemap.tiles[(position.x, position.y)].walkable is True
    for entity_id, _gold in gold_rows:
        position = world.get_component(entity_id, ItemPositionComponent)
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
