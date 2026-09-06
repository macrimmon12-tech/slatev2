"""Integration test required by CONTRACTS.md §7.3 / 03-spells-status.md §8:
drives SpellSystem's real entry point end-to-end, not a mock of it.

**15-integration-verification.md update**: `01-stats-combat.md` has now
merged. `SpellSystem`/`StatusEffectsSystem` already carry lazy-import
defaults for exactly this boundary (`_lazy_get_stat`/`_lazy_apply_effect_list`/
`_lazy_deduct_mp` importing `engine.systems.stats.StatsSystem`/
`engine.systems.effects.apply_effect_list` on first use — see those
classes' own docstrings), so this no longer needs a hand-rolled
``FakeEffectPipeline``/``FakeStatsComponent`` (CONTRACTS.md §8) at all:
constructing `SpellSystem`/`StatusEffectsSystem` with no explicit
`get_stat`/`apply_effect_list`/`deduct_mp`/`add_modifier` overrides, against
entities carrying a real `StatsComponent`, already drives the real 01
pipeline end-to-end.
"""

from __future__ import annotations

import random
from pathlib import Path

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.registry import DataRegistry
from engine.core.spatial_hash import SpatialHash
from engine.systems import effects
from engine.systems.spells import SpellCasterComponent, SpellSystem
from engine.systems.stats import StatsComponent
from engine.systems.status import StatusEffectsComponent, StatusEffectsSystem

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "data"


def setup_function(_fn):
    effects.set_data_registry(None)


def teardown_function(_fn):
    effects.set_data_registry(None)


def _build_pipeline():
    world = World()
    event_bus = EventBus()
    spatial_hash = SpatialHash()
    registry = DataRegistry()
    registry.load(DATA_ROOT)
    # EffectResolver's apply_status handler reads this process-wide holder
    # (see effects.set_data_registry's own docstring) since it can't
    # receive a registry through apply_effect_list's fixed signature.
    effects.set_data_registry(registry)

    # No get_stat/apply_effect_list/deduct_mp/add_modifier overrides: both
    # systems' lazy defaults resolve to the real 01 StatsSystem/EffectResolver.
    status_system = StatusEffectsSystem(world, event_bus, registry=registry)
    # Seeded RNG: real spells roll a real fail-chance (spells.py's own
    # fail_chance_formula, e.g. ward_of_thorns' 10% at INT 0) -- unseeded,
    # these tests would be genuinely (if rarely) flaky now that they drive
    # the real SpellSystem instead of the old fake, which never modeled a
    # fail chance at all. Seed 3 lands the non-fizzle branch every case
    # below relies on.
    spell_system = SpellSystem(
        world, event_bus, registry=registry, spatial_hash=spatial_hash,
        rng=random.Random(3),
    )
    return world, event_bus, spatial_hash, registry, spell_system, status_system


def _add_caster(world, spatial_hash, entity_id, position, base_stats):
    eid = world.create_entity(entity_id)
    world.add_component(eid, SpellCasterComponent())
    world.add_component(eid, StatsComponent(base=dict(base_stats), modifiers={}))
    spatial_hash.insert(eid, position)
    return eid


def test_single_enemy_immediate_resolve_deals_real_damage():
    world, event_bus, spatial_hash, registry, spell_system, status_system = _build_pipeline()
    caster_id = _add_caster(
        world, spatial_hash, 1, (0, 0), {"mp": 20, "intelligence": 5}
    )
    target_id = _add_caster(
        world, spatial_hash, 2, (1, 0), {"mp": 0, "hp": 30, "max_hp": 30}
    )
    damage_events: list[dict] = []
    event_bus.subscribe("damage_dealt", damage_events.append)

    result = spell_system.cast(caster_id, "firebolt", {"entity_id": target_id})

    assert result is True
    caster_stats = world.get_component(caster_id, StatsComponent)
    assert caster_stats.base["mp"] == 14  # 20 - 6 mp_cost

    target_stats = world.get_component(target_id, StatsComponent)
    assert target_stats.base["hp"] < 30

    # firebolt's real content (data/spells/firebolt.json) is authored as two
    # damage effects -- "2d6" (dice-only) and "INT * 0.4" (formula-only) --
    # per CONTRACTS.md §6/effects.py's documented limit that a single
    # formula field can't mix dice notation and stat arithmetic; see
    # data/spells/firebolt.json's own history for the content fix this
    # component made once real-content casting surfaced the ValueError a
    # combined "2d6 + (INT * 0.4)" field raised against the real formula
    # pipeline (only ever exercised end-to-end here, not by 03's own fake).
    assert len(damage_events) == 2
    assert all(evt["damage_type"] == "fire" for evt in damage_events)
    assert all(evt["target_id"] == target_id for evt in damage_events)
    total_damage = sum(evt["amount"] for evt in damage_events)
    assert total_damage == 30 - target_stats.base["hp"]
    assert any(evt["amount"] == 2.0 for evt in damage_events)  # INT(5) * 0.4


def test_deferred_target_path_resolves_at_confirmed_position():
    world, event_bus, spatial_hash, registry, spell_system, status_system = _build_pipeline()
    caster_id = _add_caster(world, spatial_hash, 1, (0, 0), {"mp": 20})
    noise_events: list[dict] = []
    event_bus.subscribe("noise_emitted", noise_events.append)

    initiated_events: list[dict] = []
    event_bus.subscribe("spell_cast_initiated", initiated_events.append)

    result = spell_system.cast(caster_id, "ward_of_thorns", None)

    assert result is True
    assert initiated_events == [
        {"entity_id": caster_id, "spell_id": "ward_of_thorns", "targeting_mode": "targeted_tile"}
    ]
    caster = world.get_component(caster_id, SpellCasterComponent)
    assert caster.state == "TARGETING"
    assert noise_events == []  # not resolved yet

    spell_system.confirm_target(caster_id, {"position": (9, 9)})

    caster = world.get_component(caster_id, SpellCasterComponent)
    assert caster.state == "IDLE"
    assert len(noise_events) == 1
    assert noise_events[0]["position"] == (9, 9)
    assert noise_events[0]["radius"] == 5


def test_apply_status_effect_through_real_effect_resolution_boundary():
    world, event_bus, spatial_hash, registry, spell_system, status_system = _build_pipeline()
    caster_id = _add_caster(world, spatial_hash, 1, (0, 0), {"mp": 20})
    target_id = _add_caster(world, spatial_hash, 2, (1, 0), {"mp": 0, "hp": 30, "max_hp": 30})
    status_applied_events: list[dict] = []
    event_bus.subscribe("status_applied", status_applied_events.append)

    result = spell_system.cast(caster_id, "poison_bolt", {"entity_id": target_id})

    assert result is True
    assert len(status_applied_events) == 1
    assert status_applied_events[0]["entity_id"] == target_id
    assert status_applied_events[0]["effect_id"] == "poisoned"

    status_comp = world.get_component(target_id, StatusEffectsComponent)
    assert status_comp is not None
    assert len(status_comp.instances) == 1
    instance = next(iter(status_comp.instances.values()))
    assert instance.effect_id == "poisoned"
    assert instance.remaining_duration == 3

    # The poisoned status also registered a real StatsSystem.add_modifier
    # call — proving the 01 -> 03 call boundary connects both ways.
    target_stats = world.get_component(target_id, StatsComponent)
    assert any(stat == "dexterity" for stat, _op, _value in target_stats.modifiers.values())
