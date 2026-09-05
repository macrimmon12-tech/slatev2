"""Unit tests for engine.systems.progression — see
docs/components/05-progression-vision.md §7/§8 for the Definition of Done
and Test Plan bullets these correspond to.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from engine.core import save
from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.registry import DataRegistry
from engine.systems.progression import ProgressionSystem, XpComponent, validate_progression_config

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "progression" / "data"
REAL_PROGRESSION_CONFIG = (
    Path(__file__).resolve().parents[2] / "data" / "config" / "progression.json"
)


class FakeStatsSystem:
    """Stands in for 01-stats-combat.md's StatsSystem (not merged yet,
    CONTRACTS.md §8 stubbing) — records add_modifier calls for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, str, str, str, float]] = []

    def add_modifier(self, entity_id: int, tag: str, stat: str, op: str, value: float) -> None:
        self.calls.append((entity_id, tag, stat, op, value))


class FakeRegistry:
    """Stands in for the foundation DataRegistry, serving fixed in-memory
    namespaces instead of reading disk."""

    def __init__(self, configs: dict[str, dict], spells: dict[str, dict] | None = None) -> None:
        self._configs = configs
        self._spells = spells or {}

    def get(self, namespace: str, id: str):
        if namespace == "configs":
            return self._configs.get(id)
        return None

    def all(self, namespace: str) -> dict[str, dict]:
        if namespace == "spells":
            return dict(self._spells)
        return {}


def _progression_config() -> dict:
    return {
        "schema_version": 1,
        "xp_thresholds": [50, 120, 200],
        "repeating_tail_increment": 100,
        "stat_gains_per_level": {"max_hp": 5, "strength": 1},
        "bonus_points_per_level": 2,
        "spell_choice_options_count": 3,
    }


def _spells_fixture() -> dict[str, dict]:
    return {
        "fireball": {"id": "fireball"},
        "ice_shard": {"id": "ice_shard"},
        "lightning_bolt": {"id": "lightning_bolt"},
        "heal": {"id": "heal"},
    }


def make_system(world: World, event_bus: EventBus, stats_system=None, spells=True):
    registry = FakeRegistry(
        {"progression": _progression_config()},
        _spells_fixture() if spells else {},
    )
    return ProgressionSystem(
        world,
        event_bus,
        registry=registry,
        stats_system=stats_system,
        rng=random.Random(1234),
    )


# -- component registry ------------------------------------------------------


def test_xp_component_registered_in_component_registry():
    assert save._COMPONENT_REGISTRY.get("XpComponent") is XpComponent


def test_xp_component_round_trips_awarded_death_ids_through_to_dict():
    xp = XpComponent(current_xp=10, level=2, unspent_bonus_points=1, awarded_death_ids={3, 1, 2})
    data = xp.to_dict()
    assert data["awarded_death_ids"] == [1, 2, 3]  # JSON-safe sorted list, not a set

    restored = XpComponent.from_dict(data)
    assert restored.awarded_death_ids == {1, 2, 3}
    assert restored.current_xp == 10
    assert restored.level == 2
    assert restored.unspent_bonus_points == 1


# -- XP award + dedup ---------------------------------------------------------


def test_death_awards_xp_to_killer():
    world = World()
    bus = EventBus()
    make_system(world, bus)

    killer = world.create_entity()
    world.add_component(killer, XpComponent())
    victim = world.create_entity()

    bus.emit("death", {"entity_id": victim, "killer_id": killer, "xp_value": 30})

    xp = world.get_component(killer, XpComponent)
    assert xp.current_xp == 30


def test_death_firing_twice_for_same_kill_never_double_awards_xp():
    world = World()
    bus = EventBus()
    make_system(world, bus)

    killer = world.create_entity()
    world.add_component(killer, XpComponent())
    victim = world.create_entity()

    payload = {"entity_id": victim, "killer_id": killer, "xp_value": 30}
    bus.emit("death", payload)
    bus.emit("death", payload)  # double-fire for the exact same kill

    xp = world.get_component(killer, XpComponent)
    assert xp.current_xp == 30


def test_death_for_a_different_victim_still_awards_normally():
    world = World()
    bus = EventBus()
    make_system(world, bus)

    killer = world.create_entity()
    world.add_component(killer, XpComponent())
    victim_a = world.create_entity()
    victim_b = world.create_entity()

    bus.emit("death", {"entity_id": victim_a, "killer_id": killer, "xp_value": 10})
    bus.emit("death", {"entity_id": victim_b, "killer_id": killer, "xp_value": 10})

    xp = world.get_component(killer, XpComponent)
    assert xp.current_xp == 20


def test_death_for_killer_with_no_xp_component_is_a_silent_noop():
    world = World()
    bus = EventBus()
    make_system(world, bus)

    killer = world.create_entity()  # no XpComponent attached
    victim = world.create_entity()

    # Must not raise.
    bus.emit("death", {"entity_id": victim, "killer_id": killer, "xp_value": 10})


# -- level thresholds / repeating tail ----------------------------------------


def test_xp_required_for_level_1_is_zero():
    world = World()
    bus = EventBus()
    system = make_system(world, bus)
    assert system._xp_required_for(1) == 0


def test_xp_required_for_uses_explicit_thresholds_within_table():
    world = World()
    bus = EventBus()
    system = make_system(world, bus)
    assert system._xp_required_for(2) == 50
    assert system._xp_required_for(3) == 120
    assert system._xp_required_for(4) == 200


def test_xp_required_for_uses_repeating_tail_past_defined_table():
    world = World()
    bus = EventBus()
    system = make_system(world, bus)
    # 3 explicit entries -> levels 2, 3, 4 defined; level 5 is the first
    # tail level: last threshold (200) + 1 * increment (100).
    assert system._xp_required_for(5) == 300
    assert system._xp_required_for(6) == 400
    assert system._xp_required_for(7) == 500


# -- multi-level-up in one award ----------------------------------------------


def test_large_xp_award_triggers_multiple_sequential_level_ups():
    world = World()
    bus = EventBus()
    stats = FakeStatsSystem()
    make_system(world, bus, stats_system=stats)

    level_up_events = []
    bus.subscribe("level_up_pending", lambda payload: level_up_events.append(payload))

    killer = world.create_entity()
    world.add_component(killer, XpComponent())
    victim = world.create_entity()

    # 130 xp crosses both the level-2 (50) and level-3 (120) thresholds.
    bus.emit("death", {"entity_id": victim, "killer_id": killer, "xp_value": 130})

    xp = world.get_component(killer, XpComponent)
    assert xp.level == 3
    assert len(level_up_events) == 2


def test_automatic_stat_gains_bonus_points_and_spell_choice_on_every_level_up():
    world = World()
    bus = EventBus()
    stats = FakeStatsSystem()
    make_system(world, bus, stats_system=stats)

    spell_choice_events = []
    bonus_events = []
    bus.subscribe("spell_choice_pending", lambda payload: spell_choice_events.append(payload))
    bus.subscribe("bonus_points_remaining", lambda payload: bonus_events.append(payload))

    killer = world.create_entity()
    world.add_component(killer, XpComponent())
    victim = world.create_entity()

    bus.emit("death", {"entity_id": victim, "killer_id": killer, "xp_value": 130})  # 2 level-ups

    xp = world.get_component(killer, XpComponent)
    assert xp.unspent_bonus_points == 4  # 2 points/level * 2 level-ups

    # add_modifier called for both stat_gains_per_level entries, per level.
    auto_tags = [call[1] for call in stats.calls if call[1].startswith("levelup_auto_")]
    assert any("max_hp" in tag for tag in auto_tags)
    assert any("strength" in tag for tag in auto_tags)
    assert len(auto_tags) == 2 * 2  # 2 stats * 2 level-ups

    assert len(spell_choice_events) == 2
    for event in spell_choice_events:
        assert event["entity_id"] == killer
        assert len(event["options"]) == 3
        assert all(option in _spells_fixture() for option in event["options"])

    assert bonus_events[-1]["remaining"] == 4


# -- spell_chosen: level-up pick vs. casting ambiguity ------------------------


def test_spell_chosen_grants_spell_only_when_a_choice_is_pending():
    world = World()
    bus = EventBus()
    system = make_system(world, bus)

    spell_learned_events = []
    bus.subscribe("spell_learned", lambda payload: spell_learned_events.append(payload))

    killer = world.create_entity()
    world.add_component(killer, XpComponent())
    victim = world.create_entity()

    bus.emit("death", {"entity_id": victim, "killer_id": killer, "xp_value": 60})  # 1 level-up
    assert killer in system._awaiting_spell_choice

    bus.emit("spell_chosen", {"entity_id": killer, "spell_id": "fireball"})

    assert spell_learned_events == [{"entity_id": killer, "spell_id": "fireball"}]
    assert killer not in system._awaiting_spell_choice
    assert system._pending_spell_grants[killer] == ["fireball"]


def test_spell_chosen_without_a_pending_choice_is_ignored():
    world = World()
    bus = EventBus()
    system = make_system(world, bus)

    spell_learned_events = []
    bus.subscribe("spell_learned", lambda payload: spell_learned_events.append(payload))

    entity = world.create_entity()
    # No level-up ever happened for this entity -- e.g. 03's SpellSystem
    # consuming spell_chosen for an ordinary in-combat cast.
    bus.emit("spell_chosen", {"entity_id": entity, "spell_id": "fireball"})

    assert spell_learned_events == []
    assert entity not in system._pending_spell_grants


# -- stat_allocated (bonus point spend) ---------------------------------------


def test_stat_allocated_spends_bonus_points_via_add_modifier():
    world = World()
    bus = EventBus()
    stats = FakeStatsSystem()
    make_system(world, bus, stats_system=stats)

    killer = world.create_entity()
    xp = XpComponent(unspent_bonus_points=3)
    world.add_component(killer, xp)

    remaining_events = []
    bus.subscribe("bonus_points_remaining", lambda payload: remaining_events.append(payload))

    bus.emit("stat_allocated", {"entity_id": killer, "stat": "dexterity", "amount": 2})

    assert xp.unspent_bonus_points == 1
    assert remaining_events[-1] == {"entity_id": killer, "remaining": 1}
    assert stats.calls == [(killer, "levelup_bonus_dexterity_0", "dexterity", "add", 2)]


def test_stat_allocated_rejects_overspend():
    world = World()
    bus = EventBus()
    stats = FakeStatsSystem()
    make_system(world, bus, stats_system=stats)

    killer = world.create_entity()
    xp = XpComponent(unspent_bonus_points=1)
    world.add_component(killer, xp)

    bus.emit("stat_allocated", {"entity_id": killer, "stat": "dexterity", "amount": 5})

    assert xp.unspent_bonus_points == 1  # unchanged
    assert stats.calls == []


def test_stat_allocated_without_stats_system_still_tracks_points_without_raising():
    world = World()
    bus = EventBus()
    make_system(world, bus, stats_system=None)

    killer = world.create_entity()
    xp = XpComponent(unspent_bonus_points=2)
    world.add_component(killer, xp)

    bus.emit("stat_allocated", {"entity_id": killer, "stat": "dexterity", "amount": 2})

    assert xp.unspent_bonus_points == 0


# -- config validation / round-trip (CONTRACTS.md §9) -------------------------


def test_validate_progression_config_accepts_the_real_shipped_config():
    data = json.loads(REAL_PROGRESSION_CONFIG.read_text())
    validate_progression_config(data)  # must not raise


def test_validate_progression_config_accepts_the_fixture_config():
    data = json.loads((FIXTURE_ROOT / "config" / "progression.json").read_text())
    validate_progression_config(data)  # must not raise


@pytest.mark.parametrize(
    "missing_key", ["xp_thresholds", "repeating_tail_increment", "stat_gains_per_level"]
)
def test_validate_progression_config_rejects_missing_required_field(missing_key):
    data = _progression_config()
    del data[missing_key]
    with pytest.raises(ValueError):
        validate_progression_config(data)


def test_validate_progression_config_rejects_wrong_type():
    data = _progression_config()
    data["xp_thresholds"] = "not-a-list"
    with pytest.raises(ValueError):
        validate_progression_config(data)


def test_progression_system_loads_config_through_a_real_data_registry():
    registry = DataRegistry()
    registry.load(FIXTURE_ROOT)

    world = World()
    bus = EventBus()
    system = ProgressionSystem(world, bus, registry=registry, rng=random.Random(1))

    assert system._xp_required_for(2) == 50
    assert system._xp_required_for(5) == 300  # repeating tail, via the real registry


def test_progression_system_without_registry_or_stats_system_never_raises():
    world = World()
    bus = EventBus()
    system = ProgressionSystem(world, bus)  # no registry, no stats_system at all

    killer = world.create_entity()
    world.add_component(killer, XpComponent())
    victim = world.create_entity()

    # Must not raise even with nothing wired in (CONTRACTS.md §2 rule 7).
    bus.emit("death", {"entity_id": victim, "killer_id": killer, "xp_value": 500})
    xp = world.get_component(killer, XpComponent)
    assert xp.level > 1
