"""Unit tests for `engine.systems.sets.SetTrackerSystem` (component doc
`04-inventory-items-loot.md` §2.3, Definition of Done)."""

from pathlib import Path

import pytest

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.registry import DataRegistry
from engine.systems.inventory import InventoryComponent, ItemInstanceComponent
from engine.systems.sets import SetTrackerSystem

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "fixtures"


class FakeStatsSystem:
    def __init__(self):
        self.calls: list[tuple] = []
        self._modifiers: dict[tuple[int, str], tuple] = {}

    def add_modifier(self, entity_id, tag, stat, op, value):
        self.calls.append(("add", entity_id, tag, stat, op, value))
        self._modifiers[(entity_id, tag)] = (stat, op, value)

    def remove_modifiers_by_tag_prefix(self, entity_id, tag_prefix):
        self.calls.append(("remove_prefix", entity_id, tag_prefix))
        removed = 0
        for key in list(self._modifiers):
            eid, tag = key
            if eid == entity_id and tag.startswith(tag_prefix):
                del self._modifiers[key]
                removed += 1
        return removed

    def current_modifiers_for(self, entity_id):
        return {k[1]: v for k, v in self._modifiers.items() if k[0] == entity_id}


@pytest.fixture
def registry() -> DataRegistry:
    reg = DataRegistry()
    reg.load(FIXTURES_ROOT)
    return reg


def _make_actor(world: World) -> int:
    eid = world.create_entity()
    world.add_component(eid, InventoryComponent())
    return eid


def _spawn_item(world: World, base_id: str) -> str:
    eid = world.create_entity()
    instance_id = f"instance-{eid}"
    world.add_component(eid, ItemInstanceComponent(base_id=base_id, instance_id=instance_id, rarity="common"))
    return instance_id


def _equip(world: World, bus: EventBus, actor: int, base_id: str, slot: str) -> str:
    instance_id = _spawn_item(world, base_id)
    inv = world.get_component(actor, InventoryComponent)
    inv.equipped[slot] = instance_id
    bus.emit("equip_changed", {"entity_id": actor, "slot": slot, "item_instance_id": instance_id})
    return instance_id


def test_no_tier_active_below_first_threshold(registry):
    world = World()
    bus = EventBus()
    stats = FakeStatsSystem()
    SetTrackerSystem(world, bus, registry, stats_system=stats)
    actor = _make_actor(world)

    events = []
    bus.subscribe("set_bonus_changed", lambda p: events.append(p))

    _equip(world, bus, actor, "piece_head", "head")

    assert events[-1] == {"entity_id": actor, "set_id": "testset", "active_tier": None}
    assert stats.current_modifiers_for(actor) == {}


def test_two_piece_tier_applies_its_modifiers(registry):
    world = World()
    bus = EventBus()
    stats = FakeStatsSystem()
    SetTrackerSystem(world, bus, registry, stats_system=stats)
    actor = _make_actor(world)

    events = []
    bus.subscribe("set_bonus_changed", lambda p: events.append(p))

    _equip(world, bus, actor, "piece_head", "head")
    _equip(world, bus, actor, "piece_hands", "hands")

    assert events[-1] == {"entity_id": actor, "set_id": "testset", "active_tier": 2}
    mods = stats.current_modifiers_for(actor)
    assert len(mods) == 1
    assert list(mods.values())[0] == ("armor", "add", 100)


def test_four_piece_tier_is_full_replacement_not_additive(registry):
    """The DoD's explicit regression guard: equipping up to the 4-piece
    tier must leave ONLY the 4-piece tier's modifiers present — not the
    2-piece tier's plus the 4-piece tier's."""
    world = World()
    bus = EventBus()
    stats = FakeStatsSystem()
    SetTrackerSystem(world, bus, registry, stats_system=stats)
    actor = _make_actor(world)

    _equip(world, bus, actor, "piece_head", "head")
    _equip(world, bus, actor, "piece_hands", "hands")
    _equip(world, bus, actor, "piece_legs", "legs")
    _equip(world, bus, actor, "piece_feet", "feet")

    mods = stats.current_modifiers_for(actor)
    assert len(mods) == 1
    assert list(mods.values())[0] == ("armor", "add", 999)  # 4-piece tier only


def test_unequipping_last_piece_clears_all_modifiers(registry):
    world = World()
    bus = EventBus()
    stats = FakeStatsSystem()
    SetTrackerSystem(world, bus, registry, stats_system=stats)
    actor = _make_actor(world)

    _equip(world, bus, actor, "piece_head", "head")
    _equip(world, bus, actor, "piece_hands", "hands")
    assert stats.current_modifiers_for(actor)  # 2pc tier active

    inv = world.get_component(actor, InventoryComponent)
    inv.equipped["head"] = None
    inv.equipped["hands"] = None
    bus.emit("equip_changed", {"entity_id": actor, "slot": "head", "item_instance_id": None})

    assert stats.current_modifiers_for(actor) == {}


def test_no_inventory_component_is_a_harmless_no_op(registry):
    world = World()
    bus = EventBus()
    stats = FakeStatsSystem()
    SetTrackerSystem(world, bus, registry, stats_system=stats)
    entity_without_inventory = world.create_entity()

    # Must not raise.
    bus.emit("equip_changed", {"entity_id": entity_without_inventory, "slot": "head", "item_instance_id": None})
    assert stats.calls == []
