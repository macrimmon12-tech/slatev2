"""Unit tests for engine/ui/anim_system.py -- see
docs/components/09-animation-vfx.md §9/§10.

``tests/fixtures/anim_events_sample.json`` supplies the melee-scenario
fixture (also used by the integration pipeline test); a couple of tests
below construct their own synthetic payloads directly where a distinct
scenario (ranged attack, unmapped fallback ids) needs positions the shared
fixture doesn't set up.
"""

from __future__ import annotations

import json
from pathlib import Path

import pygame
import pytest

from engine.core.events import EventBus
from engine.ui.anim_system import (
    DEFAULT_DAMAGE_COLOR,
    DEFAULT_STATUS_COLOR,
    DEFAULT_VFX_STYLE,
    AnimSystem,
    scale_for_amount,
)

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "anim_events_sample.json"


def _load_fixture_events() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text())


def _feed(bus: EventBus, events: list[dict]) -> None:
    for entry in events:
        bus.emit(entry["event_type"], entry["payload"])


# ---------------------------------------------------------------------------
# Six visual types, one test each (component doc §9 checklist item 1)
# ---------------------------------------------------------------------------


def test_damage_dealt_melee_produces_impact_flash_and_swoosh():
    bus = EventBus()
    anim = AnimSystem(bus)
    events = _load_fixture_events()
    # Feed only the entity_moved + damage_dealt prefix of the fixture.
    _feed(bus, events[:3])

    kinds = [t.kind for t in anim.transients()]
    assert "impact-flash" in kinds
    assert "swoosh" in kinds
    assert "projectile" not in kinds


def test_damage_dealt_ranged_produces_impact_flash_and_projectile():
    bus = EventBus()
    anim = AnimSystem(bus)
    bus.emit("entity_moved", {"entity_id": 1, "from": [0, 0], "to": [0, 0]})
    bus.emit(
        "damage_dealt",
        {
            "target_id": 2,
            "amount": 20,
            "damage_type": "arcane",
            "source_id": 1,
            "position": [5, 5],
        },
    )

    kinds = [t.kind for t in anim.transients()]
    assert "impact-flash" in kinds
    assert "projectile" in kinds
    assert "swoosh" not in kinds

    projectile = next(t for t in anim.transients() if t.kind == "projectile")
    assert projectile.src == (0, 0)
    assert projectile.dst == (5, 5)


def test_miss_produces_swoosh_between_attacker_and_defender():
    bus = EventBus()
    anim = AnimSystem(bus)
    events = _load_fixture_events()
    _feed(bus, events[:2])  # both entity_moved events
    bus.emit("miss", {"attacker_id": 1, "defender_id": 2})

    swooshes = [t for t in anim.transients() if t.kind == "swoosh"]
    assert len(swooshes) == 1
    assert swooshes[0].src == (6, 5)
    assert swooshes[0].dst == (7, 5)


def test_status_applied_produces_aura_pulse():
    bus = EventBus()
    anim = AnimSystem(bus)
    bus.emit("entity_moved", {"entity_id": 2, "from": [1, 1], "to": [3, 3]})
    bus.emit("status_applied", {"entity_id": 2, "effect_id": "poison", "instance_id": "i1", "duration": 4})

    auras = [t for t in anim.transients() if t.kind == "aura-pulse"]
    assert len(auras) == 1
    assert auras[0].dst == (3, 3)


def test_status_expired_produces_tile_fade():
    bus = EventBus()
    anim = AnimSystem(bus)
    bus.emit("entity_moved", {"entity_id": 2, "from": [1, 1], "to": [3, 3]})
    bus.emit("status_expired", {"entity_id": 2, "effect_id": "poison", "instance_id": "i1"})

    fades = [t for t in anim.transients() if t.kind == "tile-fade"]
    assert len(fades) == 1
    assert fades[0].dst == (3, 3)


def test_vfx_play_produces_area_burst():
    bus = EventBus()
    anim = AnimSystem(bus)
    bus.emit("vfx_play", {"vfx_id": "fireball", "position": [10, 10], "data": {}})

    bursts = [t for t in anim.transients() if t.kind == "area-burst"]
    assert len(bursts) == 1
    assert bursts[0].dst == (10, 10)


def test_entity_moved_alone_creates_no_transient():
    bus = EventBus()
    anim = AnimSystem(bus)
    bus.emit("entity_moved", {"entity_id": 1, "from": [0, 0], "to": [1, 1]})

    assert anim.transients() == []
    assert anim.position_of(1) == (1, 1)


# ---------------------------------------------------------------------------
# Heuristic tables cover all 8 canonical damage types + explicit fallbacks
# (component doc §9 checklist item 2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "damage_type",
    ["physical", "fire", "cold", "lightning", "poison", "holy", "arcane", "necrotic"],
)
def test_all_canonical_damage_types_have_explicit_color(damage_type):
    bus = EventBus()
    anim = AnimSystem(bus)
    bus.emit(
        "damage_dealt",
        {"target_id": 1, "amount": 5, "damage_type": damage_type, "source_id": None, "position": [0, 0]},
    )
    flash = next(t for t in anim.transients() if t.kind == "impact-flash")
    assert flash.color != DEFAULT_DAMAGE_COLOR


def test_unmapped_damage_type_falls_back_without_error():
    bus = EventBus()
    anim = AnimSystem(bus)
    bus.emit(
        "damage_dealt",
        {"target_id": 1, "amount": 5, "damage_type": "made_up_type", "source_id": None, "position": [0, 0]},
    )
    flash = next(t for t in anim.transients() if t.kind == "impact-flash")
    assert flash.color == DEFAULT_DAMAGE_COLOR


def test_unmapped_effect_id_falls_back_without_error():
    bus = EventBus()
    anim = AnimSystem(bus)
    bus.emit("entity_moved", {"entity_id": 1, "from": [0, 0], "to": [2, 2]})
    bus.emit("status_applied", {"entity_id": 1, "effect_id": "not_a_real_effect", "instance_id": "i", "duration": 1})

    aura = next(t for t in anim.transients() if t.kind == "aura-pulse")
    assert aura.color == DEFAULT_STATUS_COLOR


def test_unmapped_vfx_id_falls_back_to_generic_burst_without_error():
    bus = EventBus()
    anim = AnimSystem(bus)
    bus.emit("vfx_play", {"vfx_id": "not_a_real_vfx", "position": [0, 0], "data": {}})

    burst = next(t for t in anim.transients() if t.kind == "area-burst")
    assert (burst.color, burst.scale) == DEFAULT_VFX_STYLE


def test_scale_for_amount_buckets():
    assert scale_for_amount(1) == 0.6
    assert scale_for_amount(8) == 1.0
    assert scale_for_amount(15) == 1.4
    assert scale_for_amount(50) == 1.8
    assert scale_for_amount(None) == 0.6  # missing/non-numeric -> smallest bucket


# ---------------------------------------------------------------------------
# entity_moved-fed local position cache (component doc §9 checklist item 4)
# ---------------------------------------------------------------------------


def test_position_cache_built_purely_from_entity_moved_events():
    bus = EventBus()
    anim = AnimSystem(bus)
    bus.emit("entity_moved", {"entity_id": 42, "from": [0, 0], "to": [4, 4]})
    bus.emit("entity_moved", {"entity_id": 42, "from": [4, 4], "to": [4, 6]})

    assert anim.position_of(42) == (4, 6)

    bus.emit("miss", {"attacker_id": 42, "defender_id": 99})
    # defender_id 99 was never moved -> no resolvable position -> no
    # transient created (absence = zero cost), not a crash.
    assert anim.transients() == []


def test_miss_with_unresolvable_positions_is_a_silent_noop():
    bus = EventBus()
    anim = AnimSystem(bus)
    # Neither attacker nor defender has ever been seen via entity_moved.
    bus.emit("miss", {"attacker_id": 1, "defender_id": 2})
    assert anim.transients() == []


def test_damage_dealt_with_unknown_source_still_flashes():
    bus = EventBus()
    anim = AnimSystem(bus)
    bus.emit(
        "damage_dealt",
        {"target_id": 1, "amount": 5, "damage_type": "fire", "source_id": None, "position": [3, 3]},
    )
    kinds = [t.kind for t in anim.transients()]
    assert kinds == ["impact-flash"]


# ---------------------------------------------------------------------------
# tick() aging/pruning
# ---------------------------------------------------------------------------


def test_tick_prunes_expired_transients():
    bus = EventBus()
    anim = AnimSystem(bus)
    bus.emit("vfx_play", {"vfx_id": "fireball", "position": [0, 0], "data": {}})
    assert len(anim.transients()) == 1

    anim.tick(10.0)  # comfortably past any DEFAULT_DURATIONS entry
    assert anim.transients() == []


def test_tick_does_not_prune_before_duration_elapses():
    bus = EventBus()
    anim = AnimSystem(bus)
    bus.emit("vfx_play", {"vfx_id": "fireball", "position": [0, 0], "data": {}})

    anim.tick(0.001)
    assert len(anim.transients()) == 1


# ---------------------------------------------------------------------------
# Standalone draw()/tick() -- no renderer/UI import (component doc §9
# checklist item 6)
# ---------------------------------------------------------------------------


def test_draw_and_tick_are_fully_standalone_with_a_bare_bus_and_stub_hook():
    import sys

    assert "engine.ui.ui_runtime" not in sys.modules
    assert "engine.render.renderer" not in sys.modules

    pygame.display.init()
    try:
        surface = pygame.Surface((64, 64))
        bus = EventBus()
        anim = AnimSystem(bus)

        events = _load_fixture_events()
        _feed(bus, events)

        def world_to_screen(pos):
            x, y = pos
            return (x * 8, y * 8)

        anim.tick(0.01)
        anim.draw(surface, world_to_screen)  # must not raise
    finally:
        pygame.display.quit()

    # Still no accidental import of the modules this component doesn't own.
    assert "engine.ui.ui_runtime" not in sys.modules
    assert "engine.render.renderer" not in sys.modules
