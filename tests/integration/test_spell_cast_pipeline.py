"""Integration test required by CONTRACTS.md §7.3 / 03-spells-status.md §8:
drives SpellSystem's real entry point end-to-end, not a mock of it.

01-stats-combat.md is not merged as of this writing. Per CONTRACTS.md §8
and 03's §1.1, this uses the documented fallback: a small
``FakeEffectPipeline`` standing in for 01's ``EffectResolver`` +
``StatsSystem``, implementing just enough of the documented contract
(damage, restore_hp, noise, vfx, apply_status — see 01-stats-combat.md §2.2's
effect table) to prove SpellSystem/StatusEffectsSystem's real code actually
works end-to-end. **This must be re-run against the real 01 implementation
before Wave 3 sign-off** (see the component doc's Test Plan) — at that
point this fake should be deleted and the test re-pointed at
``engine.systems.stats.StatsSystem`` / ``engine.systems.effects`` per
CONTRACTS.md §8's stubbing guidance ("delete your fixture/mock only if the
integration test still passes against the real thing").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.formula import eval_formula, roll_dice
from engine.core.registry import DataRegistry
from engine.core.spatial_hash import SpatialHash
from engine.systems.spells import SpellCasterComponent, SpellSystem
from engine.systems.status import StatusEffectsComponent, StatusEffectsSystem

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "data"

_DICE_TOKEN_RE = re.compile(r"\d*d\d+(?:[+-]\d+)?", re.IGNORECASE)


def _eval_amount(expr, intelligence: float) -> float:
    """Reproduces the roll_dice + eval_formula composition CONTRACTS.md §6
    documents (dice tokens rolled first, then the remaining arithmetic
    evaluated against a stat context) — this is the one piece of 01's
    EffectResolver math this fake needs to reimplement to make firebolt's
    real "2d6 + (INT * 0.4)" style amount fields resolve."""
    if isinstance(expr, (int, float)) and not isinstance(expr, bool):
        return float(expr)
    resolved = _DICE_TOKEN_RE.sub(lambda m: str(roll_dice(m.group(0))), str(expr))
    return eval_formula(resolved, {"INT": intelligence})


@dataclass
class FakeStatsComponent:
    base: dict = field(default_factory=dict)
    modifiers: dict = field(default_factory=dict)  # tag -> (stat, op, value)


class FakeEffectPipeline:
    """Stand-in for 01's EffectResolver + StatsSystem. See module
    docstring."""

    def __init__(self, status_effects_system: StatusEffectsSystem) -> None:
        self.status_effects_system = status_effects_system

    def get_stat(self, entity_id: int, stat_name: str, world: World) -> float:
        stats = world.get_component(entity_id, FakeStatsComponent)
        if stats is None:
            return 0.0
        value = float(stats.base.get(stat_name, 0.0))
        additive = 0.0
        multiplier = 1.0
        for stat, op, amount in stats.modifiers.values():
            if stat != stat_name:
                continue
            if op == "add":
                additive += amount
            elif op == "multiply":
                multiplier *= amount
        return (value + additive) * multiplier

    def deduct_mp(self, entity_id: int, amount: float, world: World) -> None:
        stats = world.get_component(entity_id, FakeStatsComponent)
        if stats is not None:
            stats.base["mp"] = stats.base.get("mp", 0.0) - amount

    def apply_effect_list(self, effects, source_id, target_id, world, event_bus, position=None):
        for effect in effects:
            self.apply_effect(effect, source_id, target_id, world, event_bus, position=position)

    def apply_effect(self, effect, source_id, target_id, world, event_bus, position=None):
        etype = effect.get("type")
        intelligence = self.get_stat(source_id, "intelligence", world)

        if etype == "damage":
            amount = _eval_amount(effect["amount"], intelligence)
            stats = world.get_component(target_id, FakeStatsComponent)
            if stats is not None:
                stats.base["hp"] = stats.base.get("hp", 0.0) - amount
            event_bus.emit(
                "damage_dealt",
                {
                    "target_id": target_id,
                    "amount": amount,
                    "damage_type": effect.get("damage_type"),
                    "source_id": source_id,
                    "position": position,
                },
            )
        elif etype == "restore_hp":
            amount = _eval_amount(effect["amount"], intelligence)
            stats = world.get_component(target_id, FakeStatsComponent)
            if stats is not None:
                max_hp = stats.base.get("max_hp", stats.base.get("hp", 0.0))
                stats.base["hp"] = min(max_hp, stats.base.get("hp", 0.0) + amount)
        elif etype == "noise":
            event_bus.emit(
                "noise_emitted",
                {"position": position, "radius": effect.get("radius"), "source_id": source_id},
            )
        elif etype == "vfx":
            event_bus.emit(
                "vfx_play",
                {"vfx_id": effect.get("vfx_id"), "position": position, "data": effect.get("data")},
            )
        elif etype == "apply_status":
            self.status_effects_system.apply(
                target_id,
                effect["effect_id"],
                effect.get("duration", 0),
                effect.get("magnitude"),
                world,
                event_bus,
            )
        # Unknown types silently skipped, matching 01's documented dispatcher.


def _build_pipeline():
    world = World()
    event_bus = EventBus()
    spatial_hash = SpatialHash()
    registry = DataRegistry()
    registry.load(DATA_ROOT)

    def add_modifier(entity_id, tag, stat, op, value):
        stats = world.get_component(entity_id, FakeStatsComponent)
        if stats is not None:
            stats.modifiers[tag] = (stat, op, value)

    def remove_modifiers_by_tag_prefix(entity_id, tag_prefix):
        stats = world.get_component(entity_id, FakeStatsComponent)
        if stats is None:
            return 0
        to_remove = [tag for tag in stats.modifiers if tag.startswith(tag_prefix)]
        for tag in to_remove:
            del stats.modifiers[tag]
        return len(to_remove)

    status_system = StatusEffectsSystem(
        world,
        event_bus,
        registry=registry,
        add_modifier=add_modifier,
        remove_modifiers_by_tag_prefix=remove_modifiers_by_tag_prefix,
    )
    pipeline = FakeEffectPipeline(status_system)

    spell_system = SpellSystem(
        world,
        event_bus,
        registry=registry,
        spatial_hash=spatial_hash,
        get_stat=pipeline.get_stat,
        apply_effect_list=pipeline.apply_effect_list,
        deduct_mp=pipeline.deduct_mp,
    )
    return world, event_bus, spatial_hash, registry, spell_system, status_system


def _add_caster(world, spatial_hash, entity_id, position, base_stats):
    eid = world.create_entity(entity_id)
    world.add_component(eid, SpellCasterComponent())
    world.add_component(eid, FakeStatsComponent(base=dict(base_stats)))
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
    caster_stats = world.get_component(caster_id, FakeStatsComponent)
    assert caster_stats.base["mp"] == 14  # 20 - 6 mp_cost

    target_stats = world.get_component(target_id, FakeStatsComponent)
    assert target_stats.base["hp"] < 30

    assert len(damage_events) == 1
    assert damage_events[0]["damage_type"] == "fire"
    assert damage_events[0]["target_id"] == target_id


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

    # The poisoned status also registered a real (fake-pipeline) stat
    # modifier — proving the 01 -> 03 call boundary connects both ways.
    target_stats = world.get_component(target_id, FakeStatsComponent)
    assert any(stat == "dexterity" for stat, _op, _value in target_stats.modifiers.values())
