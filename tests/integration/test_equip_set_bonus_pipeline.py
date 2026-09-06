"""Integration test #3 required by component doc `04-inventory-items-loot.md`
§8: a real entity with `InventoryComponent` + a stats double resolving
modifiers the way `01`'s real `StatsSystem` documents (Option C:
`(base + sum(adds)) * product(multiplies)`), equip 2 then 4 pieces of a
fixture 4-piece set via real `equip()` calls, and assert the final derived
stat value reflects ONLY the 4-piece tier's bonus — full replacement
verified against real modifier resolution, not a mock of
`SetTrackerSystem`'s internals.

`01-stats-combat.md` isn't merged yet, so this test provides a small,
real (not mocked-away) modifier-resolution engine implementing exactly the
documented Option C formula, and exercises it through the real `equip()`/
`SetTrackerSystem._recompute()` call paths — CONTRACTS.md §8: mock the
function *boundary*, not the modifier math this test exists to verify.
"""

from pathlib import Path

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.registry import DataRegistry
from engine.systems.inventory import InventoryComponent, InventorySystem
from engine.systems.loot import spawn_item_instance
from engine.systems.sets import SetTrackerSystem

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "fixtures"


class RealMathStatsSystem:
    """Implements `01-stats-combat.md` §2.1's documented Option C exactly:
    `get_stat = (base + sum(add modifiers)) * product(multiply modifiers)`.
    Not a call-count mock — this is real modifier resolution, which is what
    this integration test needs to prove full-replacement actually holds
    for a derived stat value, not just for the internal modifier dict."""

    def __init__(self):
        # entity_id -> {tag: (stat, op, value)}
        self._modifiers: dict[int, dict[str, tuple[str, str, float]]] = {}

    def add_modifier(self, entity_id, tag, stat, op, value):
        self._modifiers.setdefault(entity_id, {})[tag] = (stat, op, value)

    def remove_modifiers_by_tag_prefix(self, entity_id, tag_prefix):
        mods = self._modifiers.get(entity_id, {})
        removed = [tag for tag in mods if tag.startswith(tag_prefix)]
        for tag in removed:
            del mods[tag]
        return len(removed)

    def get_stat(self, entity_id, stat_name, base_value):
        adds = 0.0
        multiplies = 1.0
        for stat, op, value in self._modifiers.get(entity_id, {}).values():
            if stat != stat_name:
                continue
            if op == "add":
                adds += value
            elif op == "multiply":
                multiplies *= value
        return (base_value + adds) * multiplies


def test_equip_two_then_four_set_pieces_yields_only_four_piece_bonus_in_derived_stat():
    registry = DataRegistry()
    registry.load(FIXTURES_ROOT)

    world = World()
    bus = EventBus()
    stats = RealMathStatsSystem()
    inv_sys = InventorySystem(world, bus, registry, stats_system=stats)
    SetTrackerSystem(world, bus, registry, stats_system=stats)

    actor = world.create_entity()
    world.add_component(actor, InventoryComponent())
    inv = world.get_component(actor, InventoryComponent)

    base_armor = 10.0

    def equip_piece(base_id: str, slot: str) -> None:
        instance_id = spawn_item_instance(base_id, depth=10, position=(0, 0), world=world, registry=registry)
        inv.item_instance_ids.append(instance_id)
        assert inv_sys.equip(actor, instance_id, slot) is True

    # Below the 2-piece threshold: no set bonus yet.
    equip_piece("piece_head", "head")
    assert stats.get_stat(actor, "armor", base_armor) == base_armor

    # 2-piece tier active.
    equip_piece("piece_hands", "hands")
    assert stats.get_stat(actor, "armor", base_armor) == base_armor + 100

    # 3rd piece — still only the 2-piece tier (pieces_required=4 not met).
    equip_piece("piece_legs", "legs")
    assert stats.get_stat(actor, "armor", base_armor) == base_armor + 100

    # 4-piece tier active — full replacement: ONLY +999, not +100 AND +999.
    equip_piece("piece_feet", "feet")
    assert stats.get_stat(actor, "armor", base_armor) == base_armor + 999
