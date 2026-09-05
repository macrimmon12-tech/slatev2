"""Unit tests for engine/systems/effects.py — see
docs/components/01-stats-combat.md §7/§8. Covers all effect types in §2.2's
table plus the unknown-type-is-skipped guarantee and the lifesteal shared-
context mechanism."""

import json
from pathlib import Path

import pytest

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.systems import effects
from engine.systems.stats import StatsComponent

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "effects"


@pytest.fixture(autouse=True)
def _reset_effects_module_state():
    """Each test gets a fresh RNG/position-lookup/warned-once state so tests
    don't leak into each other via this module's globals."""
    import random

    effects.set_rng(random.Random(1234))
    effects.set_position_lookup(None)
    effects._warned_missing_dependency.clear()
    effects._warned_unknown_types.clear()
    effects._warned_no_position_lookup = False
    yield
    effects.set_rng(random.Random())
    effects.set_position_lookup(None)


def _make_entity(world: World, base: dict, modifiers: dict | None = None) -> int:
    entity_id = world.create_entity()
    world.add_component(entity_id, StatsComponent(base=dict(base), modifiers=dict(modifiers or {})))
    return entity_id


def test_damage_reduces_hp_and_emits_damage_dealt():
    world = World()
    bus = EventBus()
    source = _make_entity(world, {"strength": 10})
    target = _make_entity(world, {"hp": 20, "max_hp": 20})
    received = []
    bus.subscribe("damage_dealt", lambda payload: received.append(payload))

    effects.apply_effect({"type": "damage", "amount": 5, "damage_type": "fire"}, source, target, world, bus)

    stats = world.get_component(target, StatsComponent)
    assert stats.base["hp"] == 15
    assert received == [
        {"target_id": target, "amount": 5.0, "damage_type": "fire", "source_id": source, "position": None}
    ]


def test_damage_with_dice_amount():
    world = World()
    bus = EventBus()
    source = _make_entity(world, {})
    target = _make_entity(world, {"hp": 20, "max_hp": 20})
    effects.set_rng(__import__("random").Random(0))  # deterministic roll

    effects.apply_effect({"type": "damage", "amount": "1d1", "damage_type": "physical"}, source, target, world, bus)

    stats = world.get_component(target, StatsComponent)
    assert stats.base["hp"] == 19  # "1d1" always rolls 1


def test_damage_with_formula_amount_uses_source_stats():
    world = World()
    bus = EventBus()
    source = _make_entity(world, {"strength": 10})
    target = _make_entity(world, {"hp": 50, "max_hp": 50})

    effects.apply_effect(
        {"type": "damage", "amount": "STRENGTH * 2", "damage_type": "physical"}, source, target, world, bus
    )

    stats = world.get_component(target, StatsComponent)
    assert stats.base["hp"] == 30


def test_damage_dropping_hp_to_zero_calls_handle_potential_death(monkeypatch):
    world = World()
    bus = EventBus()
    source = _make_entity(world, {})
    target = _make_entity(world, {"hp": 5, "max_hp": 5})
    calls = []

    from engine.systems import combat

    monkeypatch.setattr(
        combat, "handle_potential_death",
        lambda target_id, killer_id, w, eb: calls.append((target_id, killer_id)),
    )

    effects.apply_effect({"type": "damage", "amount": 10, "damage_type": "physical"}, source, target, world, bus)

    assert calls == [(target, source)]


def test_damage_target_without_stats_component_is_a_noop():
    world = World()
    bus = EventBus()
    source = world.create_entity()
    target = world.create_entity()

    effects.apply_effect({"type": "damage", "amount": 5, "damage_type": "physical"}, source, target, world, bus)
    # must not raise


def test_restore_hp_clamps_to_max_hp():
    world = World()
    bus = EventBus()
    source = world.create_entity()
    target = _make_entity(world, {"hp": 18, "max_hp": 20})

    effects.apply_effect({"type": "restore_hp", "amount": 10}, source, target, world, bus)

    stats = world.get_component(target, StatsComponent)
    assert stats.base["hp"] == 20


def test_restore_hp_emits_optional_message():
    world = World()
    bus = EventBus()
    source = world.create_entity()
    target = _make_entity(world, {"hp": 5, "max_hp": 20})
    received = []
    bus.subscribe("message", lambda payload: received.append(payload))

    effects.apply_effect({"type": "restore_hp", "amount": 3}, source, target, world, bus)

    assert len(received) == 1
    assert received[0]["category"] == "heal"


def test_restore_mp_clamps_to_max_mp():
    world = World()
    bus = EventBus()
    source = world.create_entity()
    target = _make_entity(world, {"mp": 8, "max_mp": 10})

    effects.apply_effect({"type": "restore_mp", "amount": 100}, source, target, world, bus)

    stats = world.get_component(target, StatsComponent)
    assert stats.base["mp"] == 10


def test_stat_modifier_calls_add_modifier_and_emits_event():
    world = World()
    bus = EventBus()
    source = world.create_entity()
    target = _make_entity(world, {"armor": 5})
    received = []
    bus.subscribe("stat_modifier_applied", lambda payload: received.append(payload))

    effects.apply_effect(
        {"type": "stat_modifier", "stat": "armor", "op": "add", "value": 2}, source, target, world, bus
    )

    assert len(received) == 1
    assert received[0]["stat"] == "armor"
    assert received[0]["value"] == 2

    from engine.systems.stats import StatsSystem

    assert StatsSystem(world, bus).get_stat(target, "armor", world) == 7


def test_stat_modifier_with_duration_still_applies_as_permanent_and_logs():
    world = World()
    bus = EventBus()
    source = world.create_entity()
    target = _make_entity(world, {"armor": 5})

    effects.apply_effect(
        {"type": "stat_modifier", "stat": "armor", "op": "add", "value": 2, "duration": 5},
        source, target, world, bus,
    )

    from engine.systems.stats import StatsSystem

    assert StatsSystem(world, bus).get_stat(target, "armor", world) == 7


def test_stat_modifier_explicit_tag_override():
    world = World()
    bus = EventBus()
    source = world.create_entity()
    target = _make_entity(world, {"armor": 5})

    effects.apply_effect(
        {"type": "stat_modifier", "stat": "armor", "op": "add", "value": 2, "tag": "affix_of_fire_0"},
        source, target, world, bus,
    )

    stats = world.get_component(target, StatsComponent)
    assert "affix_of_fire_0" in stats.modifiers


def test_noise_emits_noise_emitted_with_radius_and_source():
    world = World()
    bus = EventBus()
    source = world.create_entity()
    target = world.create_entity()
    received = []
    bus.subscribe("noise_emitted", lambda payload: received.append(payload))

    effects.apply_effect({"type": "noise", "radius": 5}, source, target, world, bus)

    assert received == [{"position": None, "radius": 5, "source_id": source}]


def test_noise_uses_passed_position_over_lookup():
    world = World()
    bus = EventBus()
    source = world.create_entity()
    target = world.create_entity()
    received = []
    bus.subscribe("noise_emitted", lambda payload: received.append(payload))

    effects.apply_effect({"type": "noise", "radius": 5}, source, target, world, bus, position=(3, 4))

    assert received[0]["position"] == (3, 4)


def test_vfx_is_a_pure_passthrough():
    world = World()
    bus = EventBus()
    source = world.create_entity()
    target = world.create_entity()
    received = []
    bus.subscribe("vfx_play", lambda payload: received.append(payload))

    effects.apply_effect(
        {"type": "vfx", "vfx_id": "spark", "data": {"color": "red"}}, source, target, world, bus, position=(1, 1)
    )

    assert received == [{"vfx_id": "spark", "position": (1, 1), "data": {"color": "red"}}]


def test_script_effect_is_a_noop_without_lua_host():
    world = World()
    bus = EventBus()
    source = world.create_entity()
    target = world.create_entity()

    effects.apply_effect({"type": "script", "script_ref": "npc_interaction.lua"}, source, target, world, bus)
    # must not raise


def test_identify_item_is_a_noop_without_inventory_system():
    world = World()
    bus = EventBus()
    source = world.create_entity()
    target = world.create_entity()

    effects.apply_effect({"type": "identify_item", "item_instance_id": "abc"}, source, target, world, bus)
    # must not raise


def test_remove_curse_is_a_noop_without_inventory_system():
    world = World()
    bus = EventBus()
    source = world.create_entity()
    target = world.create_entity()

    effects.apply_effect({"type": "remove_curse", "item_instance_id": "abc"}, source, target, world, bus)
    # must not raise


def test_teleport_self_is_a_noop_without_position_wiring():
    world = World()
    bus = EventBus()
    source = world.create_entity()
    target = world.create_entity()
    received = []
    bus.subscribe("entity_moved", lambda payload: received.append(payload))

    effects.apply_effect({"type": "teleport_self", "destination": "random_floor_tile"}, source, target, world, bus)

    assert received == []


def test_apply_status_is_a_noop_without_status_system():
    world = World()
    bus = EventBus()
    source = world.create_entity()
    target = world.create_entity()

    effects.apply_effect(
        {"type": "apply_status", "effect_id": "poisoned", "duration": 5, "magnitude": 2},
        source, target, world, bus,
    )
    # must not raise


def test_lifesteal_reads_preceding_damage_in_same_effect_list():
    world = World()
    bus = EventBus()
    source = _make_entity(world, {"hp": 10, "max_hp": 50})
    target = _make_entity(world, {"hp": 50, "max_hp": 50})

    effects.apply_effect_list(
        [
            {"type": "damage", "amount": 20, "damage_type": "physical"},
            {"type": "lifesteal", "percent": 50},
        ],
        source, target, world, bus,
    )

    source_stats = world.get_component(source, StatsComponent)
    target_stats = world.get_component(target, StatsComponent)
    assert target_stats.base["hp"] == 30
    assert source_stats.base["hp"] == 20  # 10 + 50% of 20


def test_lifesteal_without_preceding_damage_is_a_noop():
    world = World()
    bus = EventBus()
    source = _make_entity(world, {"hp": 10, "max_hp": 50})
    target = world.create_entity()

    effects.apply_effect({"type": "lifesteal", "percent": 50}, source, target, world, bus)

    source_stats = world.get_component(source, StatsComponent)
    assert source_stats.base["hp"] == 10


def test_lifesteal_context_does_not_leak_across_separate_apply_effect_calls():
    world = World()
    bus = EventBus()
    source = _make_entity(world, {"hp": 10, "max_hp": 50})
    target = _make_entity(world, {"hp": 50, "max_hp": 50})

    effects.apply_effect({"type": "damage", "amount": 20, "damage_type": "physical"}, source, target, world, bus)
    effects.apply_effect({"type": "lifesteal", "percent": 50}, source, target, world, bus)

    source_stats = world.get_component(source, StatsComponent)
    assert source_stats.base["hp"] == 10  # no shared context between two apply_effect calls


def test_reduce_armor_is_sugar_for_negative_stat_modifier():
    world = World()
    bus = EventBus()
    source = world.create_entity()
    target = _make_entity(world, {"armor": 10})

    effects.apply_effect({"type": "reduce_armor", "amount": 3, "duration": 3}, source, target, world, bus)

    from engine.systems.stats import StatsSystem

    assert StatsSystem(world, bus).get_stat(target, "armor", world) == 7


def test_trigger_level_complete_passthrough():
    world = World()
    bus = EventBus()
    source = world.create_entity()
    target = world.create_entity()
    received = []
    bus.subscribe("trigger_level_complete", lambda payload: received.append(payload))

    effects.apply_effect({"type": "trigger_level_complete"}, source, target, world, bus)

    assert received == [{"entity_id": source}]


def test_learn_spell_emits_spell_learned():
    world = World()
    bus = EventBus()
    source = world.create_entity()
    target = world.create_entity()
    received = []
    bus.subscribe("spell_learned", lambda payload: received.append(payload))

    effects.apply_effect({"type": "learn_spell", "spell_id": "fireball"}, source, target, world, bus)

    assert received == [{"entity_id": target, "spell_id": "fireball"}]


def test_unknown_effect_type_is_silently_skipped():
    world = World()
    bus = EventBus()
    source = world.create_entity()
    target = world.create_entity()
    received = []
    for event_type in (
        "damage_dealt", "message", "noise_emitted", "vfx_play", "stat_modifier_applied",
        "trigger_level_complete", "spell_learned",
    ):
        bus.subscribe(event_type, lambda payload, received=received: received.append(payload))

    effects.apply_effect({"type": "made_up_effect"}, source, target, world, bus)  # must not raise

    assert received == []


def test_position_lookup_hook_used_as_fallback():
    world = World()
    bus = EventBus()
    source = world.create_entity()
    target = world.create_entity()
    effects.set_position_lookup(lambda entity_id, w: (7, 8))
    received = []
    bus.subscribe("noise_emitted", lambda payload: received.append(payload))

    effects.apply_effect({"type": "noise", "radius": 1}, source, target, world, bus)

    assert received[0]["position"] == (7, 8)


# ---------------------------------------------------------------------------
# Schema validation round-trip (CONTRACTS.md §9)
# ---------------------------------------------------------------------------


def test_validate_effect_dict_accepts_every_fixture_sample():
    data = json.loads((FIXTURES / "sample_effects.json").read_text())
    for effect in data["effects"]:
        assert effects.validate_effect_dict(effect) == [], effect


def test_validate_effect_dict_accepts_every_real_data_effects_tick_effect():
    data_dir = Path(__file__).resolve().parent.parent.parent / "data" / "effects"
    for json_path in sorted(data_dir.glob("*.json")):
        content = json.loads(json_path.read_text())
        for tick_effect in content.get("tick_effects", []):
            assert effects.validate_effect_dict(tick_effect) == [], (json_path, tick_effect)


def test_validate_effect_dict_flags_missing_required_field():
    assert effects.validate_effect_dict({"type": "damage"}) != []


def test_validate_effect_dict_does_not_flag_unknown_type():
    assert effects.validate_effect_dict({"type": "made_up_effect"}) == []
