"""Unit tests for StatusEffectsSystem (docs/components/03-spells-status.md
§2.2, §8).

Same 01-not-merged-yet situation as test_spells.py: apply_effect_list /
add_modifier / remove_modifiers_by_tag_prefix are injected stand-ins here.
``_FakeStatsSystem`` mirrors 01's documented ``StatsComponent.modifiers``
shape (tag -> (stat, op, value)) closely enough to prove tag-prefix removal
actually clears entries, matching the doc's Test Plan wording ("modifier
gone from StatsComponent.modifiers after tick() expires the instance").
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.schemas.status_effects import StatusEffectSchemaError, validate_status_effect
from engine.systems.status import StatusEffectsComponent, StatusEffectsSystem, StatusInstance

REPO_ROOT = Path(__file__).resolve().parents[2]
EFFECTS_DIR = REPO_ROOT / "data" / "effects"
INVALID_EFFECTS_DIR = REPO_ROOT / "tests" / "fixtures" / "effects_status"


class FakeRegistry:
    def __init__(self, namespaces: dict[str, dict[str, dict]]):
        self._namespaces = namespaces

    def get(self, namespace: str, id: str) -> dict | None:
        return self._namespaces.get(namespace, {}).get(id)

    def all(self, namespace: str) -> dict[str, dict]:
        return dict(self._namespaces.get(namespace, {}))


class Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, effects, source_id, target_id, world, event_bus, position=None):
        self.calls.append({"effects": effects, "source_id": source_id, "target_id": target_id})


class FakeStatsSystem:
    """Mirrors 01's StatsComponent.modifiers shape: tag -> (stat, op, value),
    per entity_id."""

    def __init__(self) -> None:
        self.modifiers: dict[int, dict[str, tuple[str, str, float]]] = {}

    def add_modifier(self, entity_id, tag, stat, op, value):
        self.modifiers.setdefault(entity_id, {})[tag] = (stat, op, value)

    def remove_modifiers_by_tag_prefix(self, entity_id, tag_prefix):
        mods = self.modifiers.get(entity_id, {})
        to_remove = [tag for tag in mods if tag.startswith(tag_prefix)]
        for tag in to_remove:
            del mods[tag]
        return len(to_remove)


POISONED = {
    "id": "poisoned",
    "display_name": "Poisoned",
    "on_apply": [],
    "on_tick": [{"type": "damage", "amount": "1d4", "damage_type": "poison"}],
    "stat_modifiers": [{"stat": "dexterity", "operation": "add", "value": -2}],
}

BLESSED = {
    "id": "blessed",
    "display_name": "Blessed",
    "on_apply": [{"type": "restore_hp", "amount": "1d4"}],
    "on_tick": [],
    "stat_modifiers": [{"stat": "armor", "operation": "add", "value": 3}],
}


def make_system(effects: dict[str, dict] | None = None):
    world = World()
    event_bus = EventBus()
    registry = FakeRegistry({"effects": effects or {}})
    apply_effect_list = Recorder()
    fake_stats = FakeStatsSystem()

    system = StatusEffectsSystem(
        world,
        event_bus,
        registry=registry,
        apply_effect_list=apply_effect_list,
        add_modifier=fake_stats.add_modifier,
        remove_modifiers_by_tag_prefix=fake_stats.remove_modifiers_by_tag_prefix,
    )
    return system, world, event_bus, apply_effect_list, fake_stats


# -- apply() ----------------------------------------------------------------


def test_apply_creates_instance_and_emits_status_applied():
    system, world, event_bus, recorder, fake_stats = make_system({"poisoned": POISONED})
    entity_id = world.create_entity(1)
    events: list[dict] = []
    event_bus.subscribe("status_applied", events.append)

    instance_id = system.apply(entity_id, "poisoned", 3, None, world, event_bus)

    comp = world.get_component(entity_id, StatusEffectsComponent)
    assert comp is not None
    assert instance_id in comp.instances
    instance = comp.instances[instance_id]
    assert instance.effect_id == "poisoned"
    assert instance.remaining_duration == 3
    assert events == [
        {
            "entity_id": entity_id,
            "effect_id": "poisoned",
            "instance_id": instance_id,
            "duration": 3,
        }
    ]


def test_apply_resolves_on_apply_effects_immediately():
    system, world, event_bus, recorder, fake_stats = make_system({"blessed": BLESSED})
    entity_id = world.create_entity(1)

    system.apply(entity_id, "blessed", 5, None, world, event_bus)

    assert len(recorder.calls) == 1
    assert recorder.calls[0]["effects"] == BLESSED["on_apply"]
    assert recorder.calls[0]["target_id"] == entity_id
    assert recorder.calls[0]["source_id"] == entity_id


def test_apply_registers_stat_modifiers_with_instance_tag():
    system, world, event_bus, recorder, fake_stats = make_system({"poisoned": POISONED})
    entity_id = world.create_entity(1)

    instance_id = system.apply(entity_id, "poisoned", 3, None, world, event_bus)

    assert fake_stats.modifiers[entity_id] == {
        f"{instance_id}:dexterity": ("dexterity", "add", -2)
    }


def test_apply_with_unknown_effect_id_still_creates_instance():
    system, world, event_bus, recorder, fake_stats = make_system({})
    entity_id = world.create_entity(1)

    instance_id = system.apply(entity_id, "does_not_exist", 2, None, world, event_bus)

    comp = world.get_component(entity_id, StatusEffectsComponent)
    assert instance_id in comp.instances
    assert recorder.calls == []
    assert fake_stats.modifiers.get(entity_id, {}) == {}


# -- unconditional per-instance stacking -----------------------------------


def test_repeated_apply_creates_independent_instances():
    system, world, event_bus, recorder, fake_stats = make_system({"poisoned": POISONED})
    entity_id = world.create_entity(1)

    instance_a = system.apply(entity_id, "poisoned", 1, None, world, event_bus)
    instance_b = system.apply(entity_id, "poisoned", 3, None, world, event_bus)

    assert instance_a != instance_b
    comp = world.get_component(entity_id, StatusEffectsComponent)
    assert set(comp.instances) == {instance_a, instance_b}


def test_two_instances_tick_and_expire_independently():
    system, world, event_bus, recorder, fake_stats = make_system({"poisoned": POISONED})
    entity_id = world.create_entity(1)
    instance_a = system.apply(entity_id, "poisoned", 1, None, world, event_bus)  # expires tick 1
    instance_b = system.apply(entity_id, "poisoned", 3, None, world, event_bus)  # expires tick 3
    expired_events: list[dict] = []
    event_bus.subscribe("status_expired", expired_events.append)

    system.tick(world, event_bus)

    comp = world.get_component(entity_id, StatusEffectsComponent)
    assert instance_a not in comp.instances
    assert instance_b in comp.instances
    assert comp.instances[instance_b].remaining_duration == 2
    assert expired_events == [
        {"entity_id": entity_id, "effect_id": "poisoned", "instance_id": instance_a}
    ]
    # instance_a's modifier is gone; instance_b's is untouched.
    assert f"{instance_a}:dexterity" not in fake_stats.modifiers[entity_id]
    assert fake_stats.modifiers[entity_id][f"{instance_b}:dexterity"] == ("dexterity", "add", -2)

    system.tick(world, event_bus)
    system.tick(world, event_bus)

    comp = world.get_component(entity_id, StatusEffectsComponent)
    assert instance_b not in comp.instances
    assert f"{instance_b}:dexterity" not in fake_stats.modifiers[entity_id]
    assert len(expired_events) == 2
    assert expired_events[1] == {
        "entity_id": entity_id,
        "effect_id": "poisoned",
        "instance_id": instance_b,
    }


# -- tick() -------------------------------------------------------------


def test_tick_resolves_on_tick_effects_each_turn():
    system, world, event_bus, recorder, fake_stats = make_system({"poisoned": POISONED})
    entity_id = world.create_entity(1)
    system.apply(entity_id, "poisoned", 2, None, world, event_bus)

    system.tick(world, event_bus)

    assert len(recorder.calls) == 1
    assert recorder.calls[0]["effects"] == POISONED["on_tick"]
    assert recorder.calls[0]["target_id"] == entity_id


def test_tick_with_no_active_instances_is_noop():
    system, world, event_bus, recorder, fake_stats = make_system({"poisoned": POISONED})
    world.create_entity(1)

    system.tick(world, event_bus)  # must not raise

    assert recorder.calls == []


# -- schema round trip ------------------------------------------------------


def test_all_data_effects_are_valid_status_definitions():
    for path in sorted(EFFECTS_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        validate_status_effect(data, source=str(path))  # must not raise


def test_invalid_operation_fixture_is_rejected():
    data = json.loads((INVALID_EFFECTS_DIR / "invalid_operation.json").read_text())
    with pytest.raises(StatusEffectSchemaError):
        validate_status_effect(data)


# -- serialization round trip (StatusEffectsComponent custom from_dict) ----


def test_status_effects_component_round_trips_through_to_dict_from_dict():
    instance = StatusInstance(
        effect_id="poisoned", instance_id="abc123", remaining_duration=2, magnitude=None
    )
    comp = StatusEffectsComponent(instances={"abc123": instance})

    restored = StatusEffectsComponent.from_dict(comp.to_dict())

    assert restored.instances["abc123"] == instance
