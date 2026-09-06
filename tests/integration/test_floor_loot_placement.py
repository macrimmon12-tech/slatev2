"""Integration test #2 required by component doc `04-inventory-items-loot.md`
§1.2/§8 — THE test guarding against v1's "built but not wired" failure:
uses this component's own minimal fixture floor (`tests/fixtures/floors/`,
standing in for `06`'s real BSP generator per CONTRACTS.md §8) and calls
`place_floor_loot(world, floor_id, depth)` directly, asserting the
returned entries are real `ItemInstanceComponent`-bearing entities that
actually exist in `world` at the claimed positions, on real floor tiles.

**Follow-up flagged for when `06-worldgen-campaign.md` merges** (per this
component's own doc, verbatim): add a second version of this test (or
extend it) that calls `06`'s real floor-generation entry point
(`engine.systems.worldgen.generate_floor`) and re-runs this same assertion
against real BSP output. **Done** —
`tests/integration/test_floor_generation_pipeline.py::
test_real_floor_generation_produces_room_id_coverage_monsters_and_loot`
now drives exactly that (real `generate_floor` -> real `place_floor_loot`
-> real `ItemInstanceComponent`/`GoldComponent` entities at walkable
positions), added as part of `15-integration-verification.md`'s pass
rather than duplicated here since this fixture-floor test and that
real-BSP test cover the same assertion shape against two different tile
sources. That pass also found and fixed a real wiring gap this fixture
floor's own `load_fixture_floor` masked: `place_floor_loot` read tile
eligibility only via `FloorTileComponent` entities, which `06`'s real
`generate_floor` never creates (see `engine/systems/loot.py`'s
`_floor_tiles_from_worldgen` bridge).
"""

from pathlib import Path

from engine.core.ecs import World
from engine.core.registry import DataRegistry
from engine.systems.inventory import ItemInstanceComponent, ItemPositionComponent
from engine.systems.loot import (
    FloorTileComponent,
    GoldComponent,
    load_fixture_floor,
    place_floor_loot,
    set_active_registry,
    set_active_spawned_uniques,
)

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "fixtures"
SIMPLE_FLOOR = FIXTURES_ROOT / "floors" / "simple_floor.json"
DEGENERATE_FLOOR = FIXTURES_ROOT / "floors" / "degenerate_floor.json"


def _walkable_non_stair_positions(world: World, floor_id: str) -> set[tuple[int, int]]:
    return {
        (tile.x, tile.y)
        for _eid, tile in world.query(FloorTileComponent)
        if tile.floor_id == floor_id and tile.walkable and not tile.is_stairs and tile.room_id is not None
    }


def test_place_floor_loot_populates_real_tile_map_positions():
    registry = DataRegistry()
    registry.load(FIXTURES_ROOT)
    set_active_registry(registry)
    set_active_spawned_uniques(set())

    world = World()
    floor_id = load_fixture_floor(world, SIMPLE_FLOOR)

    # Force at least one item per room so the placement is deterministic
    # enough to assert on, by loading a registry whose eligible item pool
    # at depth 1 is exactly the fixture items.
    placed = place_floor_loot(world, floor_id, depth=1)

    assert placed, "place_floor_loot must actually place something on a floor with eligible tiles"

    eligible_positions = _walkable_non_stair_positions(world, floor_id)

    for entry in placed:
        position = tuple(entry["position"])
        assert position in eligible_positions, f"{position} is not a valid non-wall, non-stairs floor tile"

        if entry["item_base_id"] == "gold":
            matches = [
                (eid, gold)
                for eid, gold in world.query(GoldComponent)
            ]
            assert any(
                world.get_component(eid, ItemPositionComponent) == ItemPositionComponent(*position)
                for eid, _gold in matches
            )
        else:
            matches = [
                (eid, item)
                for eid, item in world.query(ItemInstanceComponent)
                if item.instance_id == entry["instance_id"]
            ]
            assert len(matches) == 1
            eid, item = matches[0]
            assert item.base_id == entry["item_base_id"]
            assert world.get_component(eid, ItemPositionComponent) == ItemPositionComponent(*position)


def test_place_floor_loot_never_places_on_stairs_or_walls():
    registry = DataRegistry()
    registry.load(FIXTURES_ROOT)
    set_active_registry(registry)
    set_active_spawned_uniques(set())

    world = World()
    floor_id = load_fixture_floor(world, SIMPLE_FLOOR)
    placed = place_floor_loot(world, floor_id, depth=1)

    stairs_and_walls = {
        (tile.x, tile.y)
        for _eid, tile in world.query(FloorTileComponent)
        if tile.floor_id == floor_id and (tile.is_stairs or not tile.walkable)
    }
    for entry in placed:
        assert tuple(entry["position"]) not in stairs_and_walls


def test_place_floor_loot_is_harmless_noop_on_degenerate_floor():
    registry = DataRegistry()
    registry.load(FIXTURES_ROOT)
    set_active_registry(registry)
    set_active_spawned_uniques(set())

    world = World()
    floor_id = load_fixture_floor(world, DEGENERATE_FLOOR)

    placed = place_floor_loot(world, floor_id, depth=1)  # must not raise
    assert placed == []


def test_place_floor_loot_is_harmless_noop_for_unknown_floor_id():
    registry = DataRegistry()
    registry.load(FIXTURES_ROOT)
    set_active_registry(registry)
    set_active_spawned_uniques(set())

    world = World()
    load_fixture_floor(world, SIMPLE_FLOOR)  # populates "fixture_floor_1" only

    placed = place_floor_loot(world, "totally_unknown_floor", depth=1)
    assert placed == []


def test_place_floor_loot_is_harmless_noop_without_active_registry():
    set_active_registry(None)
    world = World()
    floor_id = load_fixture_floor(world, SIMPLE_FLOOR)
    assert place_floor_loot(world, floor_id, depth=1) == []
