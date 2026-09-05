"""Unit tests for SpellSystem (docs/components/03-spells-status.md §2.1, §8).

01-stats-combat.md isn't merged as of this writing — per CONTRACTS.md §8 /
03's §1.1, every collaborator SpellSystem would otherwise import from 01
(get_stat, apply_effect_list, MP deduction) is a thin stand-in injected
through the constructor here, recording calls instead of doing real
gameplay math. See tests/integration/test_spell_cast_pipeline.py for the
end-to-end version that also exercises status effect application.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.schemas.spells import SpellSchemaError, validate_spell
from engine.core.spatial_hash import SpatialHash
from engine.systems.spells import SpellCasterComponent, SpellSystem

REPO_ROOT = Path(__file__).resolve().parents[2]
SPELLS_DIR = REPO_ROOT / "data" / "spells"
INVALID_SPELLS_DIR = REPO_ROOT / "tests" / "fixtures" / "spells"


class FakeRegistry:
    """Duck-types engine.core.registry.DataRegistry's read API without
    touching disk — CONTRACTS.md §8: author fixtures, don't wait on/import
    real content plumbing you don't need for a unit test."""

    def __init__(self, namespaces: dict[str, dict[str, dict]]):
        self._namespaces = namespaces

    def get(self, namespace: str, id: str) -> dict | None:
        return self._namespaces.get(namespace, {}).get(id)

    def all(self, namespace: str) -> dict[str, dict]:
        return dict(self._namespaces.get(namespace, {}))


class Recorder:
    """Records apply_effect_list calls for assertions."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, effects, source_id, target_id, world, event_bus, position=None):
        self.calls.append(
            {
                "effects": effects,
                "source_id": source_id,
                "target_id": target_id,
                "position": position,
            }
        )


class FixedRng:
    def __init__(self, value: float) -> None:
        self._value = value

    def random(self) -> float:
        return self._value


def make_system(
    spells: dict[str, dict] | None = None,
    stats: dict[int, dict[str, float]] | None = None,
    spatial_hash: SpatialHash | None = None,
    rng_value: float = 0.99,  # default: never fizzle unless a test forces it
):
    world = World()
    event_bus = EventBus()
    registry = FakeRegistry({"spells": spells or {}})
    stats = stats or {}
    apply_effect_list = Recorder()
    deducted: list[tuple[int, float]] = []

    def get_stat(entity_id, stat_name, world):
        return stats.get(entity_id, {}).get(stat_name, 0.0)

    def deduct_mp(entity_id, amount, world):
        deducted.append((entity_id, amount))
        stats.setdefault(entity_id, {})["mp"] = stats.get(entity_id, {}).get("mp", 0.0) - amount

    system = SpellSystem(
        world,
        event_bus,
        registry=registry,
        spatial_hash=spatial_hash,
        get_stat=get_stat,
        apply_effect_list=apply_effect_list,
        deduct_mp=deduct_mp,
        rng=FixedRng(rng_value),
    )
    return system, world, event_bus, stats, apply_effect_list, deducted


def add_caster(world, entity_id=None, known_spells=None):
    eid = world.create_entity(entity_id)
    world.add_component(eid, SpellCasterComponent(known_spells=known_spells or []))
    return eid


FIREBOLT = {
    "id": "firebolt",
    "display_name": "Firebolt",
    "targeting_mode": "single_enemy",
    "mp_cost": 6,
    "int_requirement": 3,
    "fail_chance_formula": "max(0.0, 0.15 - (INT * 0.01))",
    "aoe_radius": 0,
    "backfire_effects": [{"type": "damage", "amount": "1d4", "damage_type": "fire"}],
    "effects": [{"type": "damage", "amount": "2d6", "damage_type": "fire"}],
}

HEAL_SELF = {
    "id": "heal_self",
    "display_name": "Heal Self",
    "targeting_mode": "self",
    "mp_cost": 4,
    "fail_chance_formula": "0.0",
    "effects": [{"type": "restore_hp", "amount": "2d4"}],
}

RALLY = {
    "id": "rally",
    "display_name": "Rally",
    "targeting_mode": "aoe_self",
    "mp_cost": 8,
    "fail_chance_formula": "0.0",
    "aoe_radius": 3,
    "effects": [{"type": "stat_modifier", "stat": "strength", "op": "add", "value": 2}],
}

WARD = {
    "id": "ward",
    "display_name": "Ward",
    "targeting_mode": "targeted_tile",
    "mp_cost": 5,
    "fail_chance_formula": "0.0",
    "effects": [{"type": "noise", "radius": 5}],
}

METEOR = {
    "id": "meteor",
    "display_name": "Meteor",
    "targeting_mode": "aoe_targeted",
    "mp_cost": 14,
    "fail_chance_formula": "0.0",
    "aoe_radius": 2,
    "effects": [{"type": "damage", "amount": "3d6", "damage_type": "fire"}],
}


# -- targeting modes: immediate-resolve path -----------------------------


def test_self_mode_resolves_immediately():
    system, world, event_bus, stats, recorder, deducted = make_system(
        spells={"heal_self": HEAL_SELF}, stats={1: {"mp": 10}}
    )
    caster_id = add_caster(world, 1)

    result = system.cast(caster_id, "heal_self", None)

    assert result is True
    caster = world.get_component(caster_id, SpellCasterComponent)
    assert caster.state == "IDLE"
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["target_id"] == caster_id
    assert recorder.calls[0]["source_id"] == caster_id
    assert deducted == [(caster_id, 4.0)]


def test_aoe_self_mode_enumerates_via_spatial_hash():
    spatial_hash = SpatialHash()
    system, world, event_bus, stats, recorder, deducted = make_system(
        spells={"rally": RALLY}, stats={1: {"mp": 10}}, spatial_hash=spatial_hash
    )
    caster_id = add_caster(world, 1)
    ally_id = world.create_entity(2)
    far_id = world.create_entity(3)
    spatial_hash.insert(caster_id, (5, 5))
    spatial_hash.insert(ally_id, (6, 6))  # within radius 3 (Chebyshev)
    spatial_hash.insert(far_id, (50, 50))  # far away

    result = system.cast(caster_id, "rally", None)

    assert result is True
    target_ids = {call["target_id"] for call in recorder.calls}
    assert target_ids == {caster_id, ally_id}


def test_single_enemy_mode_immediate_when_target_supplied():
    system, world, event_bus, stats, recorder, deducted = make_system(
        spells={"firebolt": FIREBOLT}, stats={1: {"mp": 10, "intelligence": 5}}
    )
    caster_id = add_caster(world, 1)
    target_id = world.create_entity(2)

    result = system.cast(caster_id, "firebolt", {"entity_id": target_id})

    assert result is True
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["target_id"] == target_id
    assert recorder.calls[0]["source_id"] == caster_id
    assert recorder.calls[0]["effects"] == FIREBOLT["effects"]
    caster = world.get_component(caster_id, SpellCasterComponent)
    assert caster.state == "IDLE"


def test_targeted_tile_mode_immediate_when_target_supplied():
    system, world, event_bus, stats, recorder, deducted = make_system(
        spells={"ward": WARD}, stats={1: {"mp": 10}}
    )
    caster_id = add_caster(world, 1)

    result = system.cast(caster_id, "ward", {"position": (3, 4)})

    assert result is True
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["position"] == (3, 4)
    assert recorder.calls[0]["target_id"] == caster_id  # no entity target in this mode


def test_aoe_targeted_mode_enumerates_via_spatial_hash():
    spatial_hash = SpatialHash()
    system, world, event_bus, stats, recorder, deducted = make_system(
        spells={"meteor": METEOR}, stats={1: {"mp": 20}}, spatial_hash=spatial_hash
    )
    caster_id = add_caster(world, 1)
    near_id = world.create_entity(2)
    far_id = world.create_entity(3)
    spatial_hash.insert(caster_id, (0, 0))
    spatial_hash.insert(near_id, (11, 10))  # within radius 2 of (10, 10)
    spatial_hash.insert(far_id, (50, 50))

    result = system.cast(caster_id, "meteor", {"position": (10, 10)})

    assert result is True
    target_ids = {call["target_id"] for call in recorder.calls}
    assert target_ids == {near_id}
    assert all(call["position"] == (10, 10) for call in recorder.calls)


# -- deferred-target path -------------------------------------------------


def test_single_enemy_mode_defers_to_targeting_when_no_target():
    system, world, event_bus, stats, recorder, deducted = make_system(
        spells={"firebolt": FIREBOLT}, stats={1: {"mp": 10, "intelligence": 5}}
    )
    caster_id = add_caster(world, 1)
    events: list[dict] = []
    event_bus.subscribe("spell_cast_initiated", events.append)

    result = system.cast(caster_id, "firebolt", None)

    assert result is True
    caster = world.get_component(caster_id, SpellCasterComponent)
    assert caster.state == "TARGETING"
    assert caster.pending_spell_id == "firebolt"
    assert recorder.calls == []
    assert deducted == []  # MP only spent on actual resolution
    assert events == [
        {"entity_id": caster_id, "spell_id": "firebolt", "targeting_mode": "single_enemy"}
    ]

    target_id = world.create_entity(2)
    system.confirm_target(caster_id, {"entity_id": target_id})

    caster = world.get_component(caster_id, SpellCasterComponent)
    assert caster.state == "IDLE"
    assert caster.pending_spell_id is None
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["target_id"] == target_id
    assert deducted == [(caster_id, 6.0)]


def test_targeted_tile_mode_defers_then_confirm_target_resolves_at_position():
    system, world, event_bus, stats, recorder, deducted = make_system(
        spells={"ward": WARD}, stats={1: {"mp": 10}}
    )
    caster_id = add_caster(world, 1)

    system.cast(caster_id, "ward", None)
    assert world.get_component(caster_id, SpellCasterComponent).state == "TARGETING"

    system.confirm_target(caster_id, {"position": (7, 8)})

    caster = world.get_component(caster_id, SpellCasterComponent)
    assert caster.state == "IDLE"
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["position"] == (7, 8)


# -- one-pending-cast-at-a-time -------------------------------------------


def test_second_cast_while_targeting_returns_false_without_side_effects():
    system, world, event_bus, stats, recorder, deducted = make_system(
        spells={"firebolt": FIREBOLT, "heal_self": HEAL_SELF},
        stats={1: {"mp": 10, "intelligence": 5}},
    )
    caster_id = add_caster(world, 1)

    assert system.cast(caster_id, "firebolt", None) is True
    events: list[dict] = []
    event_bus.subscribe("spell_cast_initiated", events.append)

    result = system.cast(caster_id, "heal_self", None)

    assert result is False
    assert events == []
    assert recorder.calls == []
    assert deducted == []
    caster = world.get_component(caster_id, SpellCasterComponent)
    assert caster.state == "TARGETING"
    assert caster.pending_spell_id == "firebolt"


# -- MP / INT-gate / fail-chance pipeline ----------------------------------


def test_insufficient_mp_silently_no_casts():
    system, world, event_bus, stats, recorder, deducted = make_system(
        spells={"firebolt": FIREBOLT}, stats={1: {"mp": 1, "intelligence": 5}}
    )
    caster_id = add_caster(world, 1)
    target_id = world.create_entity(2)

    result = system.cast(caster_id, "firebolt", {"entity_id": target_id})

    assert result is False
    assert recorder.calls == []
    assert deducted == []
    caster = world.get_component(caster_id, SpellCasterComponent)
    assert caster.state == "IDLE"


def test_int_gate_fails_outright_but_mp_is_spent():
    system, world, event_bus, stats, recorder, deducted = make_system(
        spells={"firebolt": FIREBOLT}, stats={1: {"mp": 10, "intelligence": 0}}
    )
    caster_id = add_caster(world, 1)
    target_id = world.create_entity(2)

    result = system.cast(caster_id, "firebolt", {"entity_id": target_id})

    assert result is True  # cast() returns True: resolution happened, just failed the gate
    assert recorder.calls == []  # no effects, no backfire
    assert deducted == [(caster_id, 6.0)]


def test_fail_chance_roll_resolves_backfire_on_fizzle():
    system, world, event_bus, stats, recorder, deducted = make_system(
        spells={"firebolt": FIREBOLT},
        stats={1: {"mp": 10, "intelligence": 5}},
        rng_value=0.0,  # 0.0 < any positive fail_chance -> fizzle
    )
    caster_id = add_caster(world, 1)
    target_id = world.create_entity(2)

    system.cast(caster_id, "firebolt", {"entity_id": target_id})

    assert len(recorder.calls) == 1
    assert recorder.calls[0]["effects"] == FIREBOLT["backfire_effects"]
    assert recorder.calls[0]["target_id"] == caster_id  # backfire targets the caster
    assert deducted == [(caster_id, 6.0)]


def test_fail_chance_roll_resolves_real_effects_on_success():
    system, world, event_bus, stats, recorder, deducted = make_system(
        spells={"firebolt": FIREBOLT},
        stats={1: {"mp": 10, "intelligence": 5}},
        rng_value=0.999,  # above any fail_chance -> success
    )
    caster_id = add_caster(world, 1)
    target_id = world.create_entity(2)

    system.cast(caster_id, "firebolt", {"entity_id": target_id})

    assert len(recorder.calls) == 1
    assert recorder.calls[0]["effects"] == FIREBOLT["effects"]
    assert recorder.calls[0]["target_id"] == target_id


# -- cancel_cast ------------------------------------------------------------


def test_cancel_cast_clears_targeting_state_and_emits_event():
    system, world, event_bus, stats, recorder, deducted = make_system(
        spells={"firebolt": FIREBOLT}, stats={1: {"mp": 10, "intelligence": 5}}
    )
    caster_id = add_caster(world, 1)
    system.cast(caster_id, "firebolt", None)
    events: list[dict] = []
    event_bus.subscribe("spell_cast_cancelled", events.append)

    system.cancel_cast(caster_id)

    caster = world.get_component(caster_id, SpellCasterComponent)
    assert caster.state == "IDLE"
    assert caster.pending_spell_id is None
    assert events == [{"entity_id": caster_id, "spell_id": "firebolt"}]
    assert deducted == []  # cancelling never spends MP


def test_cancel_cast_is_noop_when_not_targeting():
    system, world, event_bus, stats, recorder, deducted = make_system()
    caster_id = add_caster(world, 1)
    events: list[dict] = []
    event_bus.subscribe("spell_cast_cancelled", events.append)

    system.cancel_cast(caster_id)

    assert events == []


# -- absence / edge cases ---------------------------------------------------


def test_unknown_spell_id_returns_false():
    system, world, event_bus, stats, recorder, deducted = make_system(stats={1: {"mp": 10}})
    caster_id = add_caster(world, 1)

    assert system.cast(caster_id, "does_not_exist", None) is False
    assert recorder.calls == []


def test_entity_without_spell_caster_component_returns_false():
    system, world, event_bus, stats, recorder, deducted = make_system(
        spells={"firebolt": FIREBOLT}, stats={1: {"mp": 10}}
    )
    entity_id = world.create_entity(1)

    assert system.cast(entity_id, "firebolt", None) is False


# -- schema round trip ------------------------------------------------------


def test_all_data_spells_are_valid():
    for path in sorted(SPELLS_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        validate_spell(data, source=str(path))  # must not raise


def test_invalid_targeting_mode_fixture_is_rejected():
    data = json.loads((INVALID_SPELLS_DIR / "invalid_targeting_mode.json").read_text())
    with pytest.raises(SpellSchemaError):
        validate_spell(data)


def test_invalid_aoe_radius_fixture_is_rejected():
    data = json.loads((INVALID_SPELLS_DIR / "invalid_aoe_radius.json").read_text())
    with pytest.raises(SpellSchemaError):
        validate_spell(data)
