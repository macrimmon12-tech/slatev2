"""Unit tests for `engine.systems.inventory.InventorySystem` (component doc
`04-inventory-items-loot.md` §2.1, Definition of Done)."""

from pathlib import Path

import pytest

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.registry import DataRegistry
from engine.systems.inventory import (
    InventoryComponent,
    InventorySystem,
    ItemInstanceComponent,
    ItemPositionComponent,
    assert_no_rarity_field,
    reset_identified_base_ids,
)

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "fixtures"


class FakeStatsSystem:
    """Duck-typed double for `01`'s not-yet-merged `StatsSystem` — per
    CONTRACTS.md §8, "mock the function boundary instead of importing the
    real implementation.\""""

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


@pytest.fixture(autouse=True)
def _reset_identified_state():
    reset_identified_base_ids()
    yield
    reset_identified_base_ids()


@pytest.fixture
def registry() -> DataRegistry:
    reg = DataRegistry()
    reg.load(FIXTURES_ROOT)
    return reg


def _make_actor(world: World) -> int:
    eid = world.create_entity()
    world.add_component(eid, InventoryComponent())
    return eid


def _spawn_item(world: World, base_id: str, rarity="common", affixes=None, cursed=False, position=None) -> str:
    eid = world.create_entity()
    instance_id = f"instance-{eid}"
    world.add_component(
        eid,
        ItemInstanceComponent(
            base_id=base_id,
            instance_id=instance_id,
            rarity=rarity,
            affixes=affixes or [],
            cursed=cursed,
        ),
    )
    if position is not None:
        world.add_component(eid, ItemPositionComponent(x=position[0], y=position[1]))
    return instance_id


def test_pickup_moves_item_into_inventory_and_emits_event(registry):
    world = World()
    bus = EventBus()
    inv_sys = InventorySystem(world, bus, registry)
    actor = _make_actor(world)
    instance_id = _spawn_item(world, "dagger", position=(2, 2))

    events = []
    bus.subscribe("item_pickup", lambda p: events.append(p))

    assert inv_sys.pickup(actor, instance_id) is True
    inv = world.get_component(actor, InventoryComponent)
    assert instance_id in inv.item_instance_ids
    assert events == [{"entity_id": actor, "item_instance_id": instance_id}]

    item_eid, _item = inv_sys._find_item(instance_id)
    assert world.get_component(item_eid, ItemPositionComponent) is None


def test_pickup_returns_false_for_unknown_instance(registry):
    world = World()
    bus = EventBus()
    inv_sys = InventorySystem(world, bus, registry)
    actor = _make_actor(world)
    assert inv_sys.pickup(actor, "nonexistent") is False


def test_drop_places_item_and_emits_event(registry):
    world = World()
    bus = EventBus()
    inv_sys = InventorySystem(world, bus, registry)
    actor = _make_actor(world)
    instance_id = _spawn_item(world, "dagger", position=(0, 0))
    inv_sys.pickup(actor, instance_id)

    events = []
    bus.subscribe("item_dropped", lambda p: events.append(p))

    assert inv_sys.drop(actor, instance_id, (5, 5)) is True
    inv = world.get_component(actor, InventoryComponent)
    assert instance_id not in inv.item_instance_ids
    item_eid, _item = inv_sys._find_item(instance_id)
    assert world.get_component(item_eid, ItemPositionComponent) == ItemPositionComponent(5, 5)
    assert events == [{"entity_id": actor, "item_instance_id": instance_id, "position": (5, 5)}]


def test_drop_refuses_currently_equipped_item(registry):
    world = World()
    bus = EventBus()
    stats = FakeStatsSystem()
    inv_sys = InventorySystem(world, bus, registry, stats_system=stats)
    actor = _make_actor(world)
    instance_id = _spawn_item(world, "dagger")
    inv = world.get_component(actor, InventoryComponent)
    inv.item_instance_ids.append(instance_id)
    assert inv_sys.equip(actor, instance_id, "main_hand") is True

    assert inv_sys.drop(actor, instance_id, (1, 1)) is False


def test_use_consumable_resolves_effects_and_removes_item(registry):
    world = World()
    bus = EventBus()
    resolved_calls = []

    def fake_resolver(effects, source_id, target_id, world_, event_bus_):
        resolved_calls.append((effects, source_id, target_id))

    inv_sys = InventorySystem(world, bus, registry, effect_resolver=fake_resolver)
    actor = _make_actor(world)
    instance_id = _spawn_item(world, "potion")
    inv = world.get_component(actor, InventoryComponent)
    inv.item_instance_ids.append(instance_id)

    used_events = []
    bus.subscribe("item_used", lambda p: used_events.append(p))

    assert inv_sys.use(actor, instance_id) is True
    assert resolved_calls[0][1] == actor
    assert resolved_calls[0][2] == actor
    assert used_events == [{"entity_id": actor, "item_instance_id": instance_id}]
    assert instance_id not in inv.item_instance_ids  # consumed


def test_equip_calls_add_modifier_for_each_stat_modifier_and_emits_event(registry):
    world = World()
    bus = EventBus()
    stats = FakeStatsSystem()
    inv_sys = InventorySystem(world, bus, registry, stats_system=stats)
    actor = _make_actor(world)
    instance_id = _spawn_item(world, "dagger")
    inv = world.get_component(actor, InventoryComponent)
    inv.item_instance_ids.append(instance_id)

    events = []
    bus.subscribe("equip_changed", lambda p: events.append(p))

    assert inv_sys.equip(actor, instance_id, "main_hand") is True
    assert len(stats.calls) == 2  # dagger.json has 2 stat_modifiers
    for call in stats.calls:
        assert call[0] == "add"
        assert call[1] == actor
        assert call[2].startswith(f"item_{instance_id}_")
    assert inv.equipped["main_hand"] == instance_id
    assert events == [{"entity_id": actor, "slot": "main_hand", "item_instance_id": instance_id}]


def test_equip_swaps_out_previously_equipped_item_in_same_slot(registry):
    world = World()
    bus = EventBus()
    stats = FakeStatsSystem()
    inv_sys = InventorySystem(world, bus, registry, stats_system=stats)
    actor = _make_actor(world)
    inv = world.get_component(actor, InventoryComponent)

    first = _spawn_item(world, "dagger")
    inv.item_instance_ids.append(first)
    assert inv_sys.equip(actor, first, "main_hand") is True

    second = _spawn_item(world, "dagger")
    inv.item_instance_ids.append(second)
    assert inv_sys.equip(actor, second, "main_hand") is True

    assert inv.equipped["main_hand"] == second
    # The first item's modifiers must actually be cleared (not stacked with
    # the second's) — the swap must have gone through unequip() first.
    assert ("remove_prefix", actor, f"item_{first}_") in stats.calls
    remaining_tags = set(stats._modifiers.keys())
    assert not any(eid == actor and tag.startswith(f"item_{first}_") for eid, tag in remaining_tags)


def test_equip_refuses_swap_when_currently_equipped_item_is_locked(registry):
    world = World()
    bus = EventBus()
    stats = FakeStatsSystem()
    inv_sys = InventorySystem(world, bus, registry, stats_system=stats)
    actor = _make_actor(world)
    inv = world.get_component(actor, InventoryComponent)

    cursed_instance = _spawn_item(world, "ring_of_woe", cursed=True)
    inv.item_instance_ids.append(cursed_instance)
    assert inv_sys.equip(actor, cursed_instance, "ring") is True

    other_instance = _spawn_item(world, "ring_of_woe", cursed=False)
    inv.item_instance_ids.append(other_instance)

    assert inv_sys.equip(actor, other_instance, "ring") is False
    assert inv.equipped["ring"] == cursed_instance


def test_equip_includes_affix_modifiers(registry):
    world = World()
    bus = EventBus()
    stats = FakeStatsSystem()
    inv_sys = InventorySystem(world, bus, registry, stats_system=stats)
    actor = _make_actor(world)
    affix = {"id": "affix_of_the_bear", "stat_modifiers": [{"stat": "strength", "operation": "add", "value": 4}]}
    instance_id = _spawn_item(world, "dagger", rarity="rare", affixes=[affix])
    inv = world.get_component(actor, InventoryComponent)
    inv.item_instance_ids.append(instance_id)

    inv_sys.equip(actor, instance_id, "main_hand")
    # 2 base stat_modifiers + 1 affix stat_modifier
    assert len(stats.calls) == 3


def test_unequip_removes_modifiers_by_prefix_and_emits_event(registry):
    world = World()
    bus = EventBus()
    stats = FakeStatsSystem()
    inv_sys = InventorySystem(world, bus, registry, stats_system=stats)
    actor = _make_actor(world)
    instance_id = _spawn_item(world, "dagger")
    inv = world.get_component(actor, InventoryComponent)
    inv.item_instance_ids.append(instance_id)
    inv_sys.equip(actor, instance_id, "main_hand")

    events = []
    bus.subscribe("equip_changed", lambda p: events.append(p))

    assert inv_sys.unequip(actor, "main_hand") is True
    assert inv.equipped["main_hand"] is None
    assert ("remove_prefix", actor, f"item_{instance_id}_") in stats.calls
    assert events == [{"entity_id": actor, "slot": "main_hand", "item_instance_id": None}]


def test_cursed_item_auto_identifies_and_locks_on_equip_then_refuses_unequip(registry):
    world = World()
    bus = EventBus()
    stats = FakeStatsSystem()
    inv_sys = InventorySystem(world, bus, registry, stats_system=stats)
    actor = _make_actor(world)
    instance_id = _spawn_item(world, "ring_of_woe", cursed=True)
    inv = world.get_component(actor, InventoryComponent)
    inv.item_instance_ids.append(instance_id)

    assert inv_sys.equip(actor, instance_id, "ring") is True
    _eid, item = inv_sys._find_item(instance_id)
    assert item.curse_identified is True
    assert item.locked is True

    # Locked — unequip must refuse, no state change.
    assert inv_sys.unequip(actor, "ring") is False
    assert inv.equipped["ring"] == instance_id


def test_identify_type_marks_every_instance_of_base_id_globally(registry):
    world = World()
    bus = EventBus()
    inv_sys = InventorySystem(world, bus, registry)
    instance_a = _spawn_item(world, "dagger")
    instance_b = _spawn_item(world, "dagger")
    instance_other = _spawn_item(world, "potion")

    inv_sys.identify_type("dagger")

    _eid_a, item_a = inv_sys._find_item(instance_a)
    _eid_b, item_b = inv_sys._find_item(instance_b)
    _eid_other, item_other = inv_sys._find_item(instance_other)
    assert item_a.identified_type is True
    assert item_b.identified_type is True
    assert item_other.identified_type is False
    assert inv_sys.is_type_identified("dagger") is True


def test_identify_type_request_event_triggers_identify_type(registry):
    world = World()
    bus = EventBus()
    inv_sys = InventorySystem(world, bus, registry)
    instance_id = _spawn_item(world, "dagger")

    bus.emit("identify_type_request", {"item_base_id": "dagger"})

    _eid, item = inv_sys._find_item(instance_id)
    assert item.identified_type is True


def test_identify_instance_reveals_curse_without_leaking_to_other_instance(registry):
    world = World()
    bus = EventBus()
    inv_sys = InventorySystem(world, bus, registry)
    cursed_instance = _spawn_item(world, "ring_of_woe", cursed=True)
    clean_instance = _spawn_item(world, "ring_of_woe", cursed=False)

    inv_sys.identify_instance(cursed_instance)

    _eid_a, cursed_item = inv_sys._find_item(cursed_instance)
    _eid_b, clean_item = inv_sys._find_item(clean_instance)
    assert cursed_item.curse_identified is True
    assert clean_item.curse_identified is False
    # identify_instance never affects base-type identification.
    assert cursed_item.identified_type is False
    assert clean_item.identified_type is False


def test_assert_no_rarity_field_raises_on_bad_content():
    with pytest.raises(ValueError):
        assert_no_rarity_field({"id": "bad_item", "rarity": "epic"}, "bad_item", "items")


def test_loading_real_content_with_rarity_field_is_rejected():
    reg = DataRegistry()
    reg.load(FIXTURES_ROOT / "invalid_items")
    bad_item = reg.get("items", "bad_item")
    assert bad_item is not None
    with pytest.raises(ValueError):
        assert_no_rarity_field(bad_item, "bad_item", "items")
