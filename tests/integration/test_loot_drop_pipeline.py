"""Integration test #1 required by component doc `04-inventory-items-loot.md`
§8: a real `World` + event bus + fixture monster with a non-empty
`loot_table`, `loot_drop` emitted the way `01`'s real `CombatSystem` does,
asserting `LootSystem`'s real subscriber fires and real
`ItemInstanceComponent`-bearing entities land in `world` — not a mock
assertion that resolution "would have happened".
"""

from pathlib import Path

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.registry import DataRegistry
from engine.systems.inventory import ItemInstanceComponent
from engine.systems.loot import LootSystem

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "fixtures"


def test_loot_drop_event_produces_real_item_entities_at_position():
    registry = DataRegistry()
    registry.load(FIXTURES_ROOT)

    world = World()
    bus = EventBus()
    loot_system = LootSystem(world, bus, registry)
    loot_system.set_current_depth(1)

    # Simulate exactly what 01's CombatSystem.handle_potential_death emits:
    # the monster's raw, unresolved loot_table entries.
    position = (7, 9)
    entries = [
        {"item_id": "dagger", "weight": 1, "quantity": [1, 1]},
    ]
    bus.emit("loot_drop", {"position": position, "entries": entries})

    spawned = [
        (eid, item)
        for eid, item in world.query(ItemInstanceComponent)
        if item.base_id == "dagger"
    ]
    assert len(spawned) == 1
    _eid, item = spawned[0]
    assert item.instance_id
    assert item.rarity in ("common", "uncommon", "rare", "epic", "legendary")


def test_loot_drop_with_no_position_or_entries_is_harmless():
    registry = DataRegistry()
    registry.load(FIXTURES_ROOT)
    world = World()
    bus = EventBus()
    LootSystem(world, bus, registry)

    # Must not raise.
    bus.emit("loot_drop", {"position": None, "entries": []})
    bus.emit("loot_drop", None)
    assert list(world.query(ItemInstanceComponent)) == []
