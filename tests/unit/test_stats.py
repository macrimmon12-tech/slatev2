"""Unit tests for engine/systems/stats.py — see
docs/components/01-stats-combat.md §7/§8."""

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.systems.stats import StatsComponent, StatsSystem


def _make_entity(world: World, base: dict, modifiers: dict | None = None) -> int:
    entity_id = world.create_entity()
    world.add_component(entity_id, StatsComponent(base=dict(base), modifiers=dict(modifiers or {})))
    return entity_id


def test_get_stat_with_no_modifiers_returns_base_value():
    world = World()
    bus = EventBus()
    system = StatsSystem(world, bus)
    entity_id = _make_entity(world, {"strength": 10})

    assert system.get_stat(entity_id, "strength", world) == 10


def test_get_stat_missing_stat_defaults_to_zero():
    world = World()
    bus = EventBus()
    system = StatsSystem(world, bus)
    entity_id = _make_entity(world, {"strength": 10})

    assert system.get_stat(entity_id, "dexterity", world) == 0.0


def test_get_stat_no_stats_component_defaults_to_zero():
    world = World()
    bus = EventBus()
    system = StatsSystem(world, bus)
    entity_id = world.create_entity()

    assert system.get_stat(entity_id, "strength", world) == 0.0


def test_option_c_math_adds_only():
    world = World()
    bus = EventBus()
    system = StatsSystem(world, bus)
    entity_id = _make_entity(world, {"strength": 10})

    system.add_modifier(entity_id, "item_1_0", "strength", "add", 3)
    system.add_modifier(entity_id, "item_2_0", "strength", "add", 5)

    assert system.get_stat(entity_id, "strength", world) == 18


def test_option_c_math_multiplies_only():
    world = World()
    bus = EventBus()
    system = StatsSystem(world, bus)
    entity_id = _make_entity(world, {"strength": 10})

    system.add_modifier(entity_id, "buff_1", "strength", "multiply", 1.5)
    system.add_modifier(entity_id, "buff_2", "strength", "multiply", 2.0)

    assert system.get_stat(entity_id, "strength", world) == 30


def test_option_c_math_mixed_adds_and_multiplies():
    world = World()
    bus = EventBus()
    system = StatsSystem(world, bus)
    entity_id = _make_entity(world, {"strength": 10})

    system.add_modifier(entity_id, "item_1_0", "strength", "add", 5)
    system.add_modifier(entity_id, "buff_1", "strength", "multiply", 2.0)

    # (base + sum(adds)) * product(multiplies) = (10 + 5) * 2 = 30
    assert system.get_stat(entity_id, "strength", world) == 30


def test_option_c_math_order_of_insertion_does_not_matter():
    world = World()
    bus = EventBus()
    system_a = StatsSystem(world, bus)
    entity_a = _make_entity(world, {"strength": 10})
    system_a.add_modifier(entity_a, "m1", "strength", "add", 5)
    system_a.add_modifier(entity_a, "m2", "strength", "multiply", 1.5)
    system_a.add_modifier(entity_a, "m3", "strength", "add", 2)

    entity_b = _make_entity(world, {"strength": 10})
    system_a.add_modifier(entity_b, "m3", "strength", "add", 2)
    system_a.add_modifier(entity_b, "m1", "strength", "add", 5)
    system_a.add_modifier(entity_b, "m2", "strength", "multiply", 1.5)

    assert system_a.get_stat(entity_a, "strength", world) == system_a.get_stat(
        entity_b, "strength", world
    )


def test_add_modifier_does_not_mutate_base():
    world = World()
    bus = EventBus()
    system = StatsSystem(world, bus)
    entity_id = _make_entity(world, {"strength": 10})

    system.add_modifier(entity_id, "item_1_0", "strength", "add", 5)
    system.add_modifier(entity_id, "item_1_1", "strength", "multiply", 2.0)
    system.add_modifier(entity_id, "item_1_2", "strength", "add", 1)

    stats = world.get_component(entity_id, StatsComponent)
    assert stats.base == {"strength": 10}
    assert system.get_stat(entity_id, "strength", world) == (10 + 5 + 1) * 2.0


def test_add_modifier_emits_stat_modifier_applied():
    world = World()
    bus = EventBus()
    system = StatsSystem(world, bus)
    entity_id = _make_entity(world, {"strength": 10})
    received = []
    bus.subscribe("stat_modifier_applied", lambda payload: received.append(payload))

    system.add_modifier(entity_id, "item_1_0", "strength", "add", 5)

    assert received == [
        {"entity_id": entity_id, "tag": "item_1_0", "stat": "strength", "op": "add", "value": 5}
    ]


def test_add_modifier_rejects_invalid_op():
    world = World()
    bus = EventBus()
    system = StatsSystem(world, bus)
    entity_id = _make_entity(world, {"strength": 10})
    received = []
    bus.subscribe("stat_modifier_applied", lambda payload: received.append(payload))

    system.add_modifier(entity_id, "item_1_0", "strength", "subtract", 5)

    assert received == []
    stats = world.get_component(entity_id, StatsComponent)
    assert stats.modifiers == {}
    assert system.get_stat(entity_id, "strength", world) == 10


def test_add_modifier_on_entity_without_stats_component_is_a_noop():
    world = World()
    bus = EventBus()
    system = StatsSystem(world, bus)
    entity_id = world.create_entity()

    system.add_modifier(entity_id, "item_1_0", "strength", "add", 5)  # must not raise


def test_remove_modifiers_by_tag_prefix_item_instance_convention():
    world = World()
    bus = EventBus()
    system = StatsSystem(world, bus)
    entity_id = _make_entity(world, {"strength": 10, "dexterity": 5})

    system.add_modifier(entity_id, "item_42_0", "strength", "add", 3)
    system.add_modifier(entity_id, "item_42_1", "dexterity", "add", 2)
    system.add_modifier(entity_id, "item_99_0", "strength", "add", 100)

    removed = system.remove_modifiers_by_tag_prefix(entity_id, "item_42_")

    assert removed == 2
    assert system.get_stat(entity_id, "strength", world) == 110
    assert system.get_stat(entity_id, "dexterity", world) == 5


def test_remove_modifiers_by_tag_prefix_affix_convention():
    world = World()
    bus = EventBus()
    system = StatsSystem(world, bus)
    entity_id = _make_entity(world, {"strength": 10})

    system.add_modifier(entity_id, "affix_of_fire_0", "strength", "add", 4)

    removed = system.remove_modifiers_by_tag_prefix(entity_id, "affix_of_fire_")

    assert removed == 1
    assert system.get_stat(entity_id, "strength", world) == 10


def test_remove_modifiers_by_exact_tag_status_instance_convention():
    """`{instance_id}:{stat}` tags are removed by exact tag on that
    instance's expiry (not necessarily prefix) -- but prefix removal must
    also work to clear all of one instance's stats at once."""
    world = World()
    bus = EventBus()
    system = StatsSystem(world, bus)
    entity_id = _make_entity(world, {"strength": 10, "dexterity": 5})

    system.add_modifier(entity_id, "inst-1:strength", "strength", "add", 3)
    system.add_modifier(entity_id, "inst-1:dexterity", "dexterity", "add", 2)
    system.add_modifier(entity_id, "inst-2:strength", "strength", "add", 100)

    removed = system.remove_modifiers_by_tag_prefix(entity_id, "inst-1:")

    assert removed == 2
    assert system.get_stat(entity_id, "strength", world) == 110
    assert system.get_stat(entity_id, "dexterity", world) == 5


def test_remove_modifiers_by_tag_prefix_set_bonus_convention():
    world = World()
    bus = EventBus()
    system = StatsSystem(world, bus)
    entity_id = _make_entity(world, {"strength": 10})

    system.add_modifier(entity_id, "set_juggernaut_2_0", "strength", "add", 6)
    system.add_modifier(entity_id, "set_juggernaut_3_0", "strength", "add", 10)

    removed = system.remove_modifiers_by_tag_prefix(entity_id, "set_juggernaut_")

    assert removed == 2
    assert system.get_stat(entity_id, "strength", world) == 10


def test_remove_modifiers_by_tag_prefix_no_match_returns_zero():
    world = World()
    bus = EventBus()
    system = StatsSystem(world, bus)
    entity_id = _make_entity(world, {"strength": 10})

    assert system.remove_modifiers_by_tag_prefix(entity_id, "item_1_") == 0


def test_remove_modifiers_on_entity_without_stats_component_is_a_noop():
    world = World()
    bus = EventBus()
    system = StatsSystem(world, bus)
    entity_id = world.create_entity()

    assert system.remove_modifiers_by_tag_prefix(entity_id, "item_1_") == 0
