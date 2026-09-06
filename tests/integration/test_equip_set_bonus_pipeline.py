"""Integration test #3 required by component doc `04-inventory-items-loot.md`
§8: a real entity with `InventoryComponent` + `01`'s real `StatsSystem`/
`StatsComponent` resolving modifiers via the documented Option C formula
(`(base + sum(adds)) * product(multiplies)`), equip 2 then 4 pieces of a
fixture 4-piece set via real `equip()` calls, and assert the final derived
stat value reflects ONLY the 4-piece tier's bonus — full replacement
verified against real modifier resolution, not a mock of
`SetTrackerSystem`'s internals.

**15-integration-verification.md update**: `01-stats-combat.md` has now
merged, so this re-points `stats_system` at the real
`engine.systems.stats.StatsSystem` instead of the `RealMathStatsSystem`
hand-rolled stand-in this test used to carry (CONTRACTS.md §8: "delete
your fixture/mock only if the integration test still passes against the
real thing" — it does, unchanged assertions, once the actor carries a real
`StatsComponent` for `get_stat`/`add_modifier` to read/write).
"""

from pathlib import Path

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.registry import DataRegistry
from engine.systems.inventory import InventoryComponent, InventorySystem
from engine.systems.loot import spawn_item_instance
from engine.systems.sets import SetTrackerSystem
from engine.systems.stats import StatsComponent, StatsSystem

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "fixtures"


def test_equip_two_then_four_set_pieces_yields_only_four_piece_bonus_in_derived_stat():
    registry = DataRegistry()
    registry.load(FIXTURES_ROOT)

    world = World()
    bus = EventBus()
    stats = StatsSystem(world, bus)
    inv_sys = InventorySystem(world, bus, registry, stats_system=stats)
    SetTrackerSystem(world, bus, registry, stats_system=stats)

    base_armor = 10.0
    actor = world.create_entity()
    world.add_component(actor, InventoryComponent())
    world.add_component(actor, StatsComponent(base={"armor": base_armor}, modifiers={}))
    inv = world.get_component(actor, InventoryComponent)

    def equip_piece(base_id: str, slot: str) -> None:
        instance_id = spawn_item_instance(base_id, depth=10, position=(0, 0), world=world, registry=registry)
        inv.item_instance_ids.append(instance_id)
        assert inv_sys.equip(actor, instance_id, slot) is True

    def armor() -> float:
        return stats.get_stat(actor, "armor", world)

    # Below the 2-piece threshold: no set bonus yet.
    equip_piece("piece_head", "head")
    assert armor() == base_armor

    # 2-piece tier active.
    equip_piece("piece_hands", "hands")
    assert armor() == base_armor + 100

    # 3rd piece — still only the 2-piece tier (pieces_required=4 not met).
    equip_piece("piece_legs", "legs")
    assert armor() == base_armor + 100

    # 4-piece tier active — full replacement: ONLY +999, not +100 AND +999.
    equip_piece("piece_feet", "feet")
    assert armor() == base_armor + 999
