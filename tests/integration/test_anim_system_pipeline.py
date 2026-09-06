"""Integration test for engine/ui/anim_system.py -- CONTRACTS.md §7.3 /
docs/components/09-animation-vfx.md §9/§10.

Drives ``AnimSystem`` end-to-end against a real ``EventBus`` with a
realistic combat-exchange event sequence, then proves the component's
defining guarantee: removability (component doc §1.1). Two variants:

1. ``AnimSystem`` is constructed on a real bus; emitting all consumed
   events raises nothing and mutates no state outside its own transient
   list.
2. ``AnimSystem`` is never constructed at all; emitting the exact same
   events on the same bus still raises nothing and no other subscriber's
   behavior differs -- proving removal is inert at the system level, not
   just that the class itself is well-behaved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pygame

from engine.core.events import EventBus
from engine.ui.anim_system import AnimSystem

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "anim_events_sample.json"


def _load_fixture_events() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text())


def _stub_world_to_screen(pos):
    x, y = pos
    return (x * 8, y * 8)


def test_realistic_combat_exchange_produces_expected_transients_and_draws_clean():
    bus = EventBus()
    anim = AnimSystem(bus)
    events = _load_fixture_events()

    expected_kind_by_event = {
        "entity_moved": None,  # position tracking only, no transient
        "damage_dealt": "impact-flash",  # plus a swoosh/projectile, checked separately
        "miss": "swoosh",
        "status_applied": "aura-pulse",
        "status_expired": "tile-fade",
        "vfx_play": "area-burst",
    }

    for entry in events:
        before = len(anim.transients())
        bus.emit(entry["event_type"], entry["payload"])
        after = anim.transients()

        expected_kind = expected_kind_by_event[entry["event_type"]]
        if expected_kind is None:
            assert len(after) == before
            continue

        new_kinds = [t.kind for t in after[before:]]
        assert expected_kind in new_kinds

    # damage_dealt in the fixture is a melee exchange (attacker moved
    # adjacent to the target) -- impact-flash + swoosh, no projectile.
    kinds = [t.kind for t in anim.transients()]
    assert kinds.count("impact-flash") == 1
    assert kinds.count("swoosh") == 2  # one from damage_dealt, one from miss
    assert kinds.count("aura-pulse") == 1
    assert kinds.count("tile-fade") == 1
    assert kinds.count("area-burst") == 1
    assert "projectile" not in kinds

    # tick() past every transient's duration prunes the whole set.
    anim.tick(10.0)
    assert anim.transients() == []

    # draw() against a real pygame.Surface completes without error for
    # every visual type in one pass (re-seed transients first, since the
    # tick above pruned them all).
    for entry in events:
        bus.emit(entry["event_type"], entry["payload"])

    pygame.display.init()
    try:
        surface = pygame.Surface((128, 128))
        anim.tick(0.01)
        anim.draw(surface, _stub_world_to_screen)
    finally:
        pygame.display.quit()


def test_removability_anim_system_present_touches_only_its_own_state():
    bus = EventBus()
    world_marker = {"other_subscriber_calls": 0, "other_subscriber_payloads": []}

    def other_subscriber(payload):
        world_marker["other_subscriber_calls"] += 1
        world_marker["other_subscriber_payloads"].append(payload)

    for event_type in ("damage_dealt", "miss", "status_applied", "status_expired", "vfx_play", "entity_moved"):
        bus.subscribe(event_type, other_subscriber)

    anim = AnimSystem(bus)

    events = _load_fixture_events()
    for entry in events:
        bus.emit(entry["event_type"], entry["payload"])  # must not raise

    # AnimSystem produced its own transients...
    assert len(anim.transients()) > 0
    # ...and the unrelated subscriber saw every event, unaffected by
    # AnimSystem's presence on the same bus.
    assert world_marker["other_subscriber_calls"] == len(events)


def test_removability_anim_system_absent_leaves_bus_and_other_subscribers_unaffected():
    bus = EventBus()
    world_marker = {"other_subscriber_calls": 0}

    def other_subscriber(payload):
        world_marker["other_subscriber_calls"] += 1

    for event_type in ("damage_dealt", "miss", "status_applied", "status_expired", "vfx_play", "entity_moved"):
        bus.subscribe(event_type, other_subscriber)

    # AnimSystem is never constructed at all.
    events = _load_fixture_events()
    for entry in events:
        bus.emit(entry["event_type"], entry["payload"])  # must not raise

    assert world_marker["other_subscriber_calls"] == len(events)
