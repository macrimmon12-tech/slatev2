"""Integration tests for ProgressionSystem and VisionSystem — required per
CONTRACTS.md §7.3 and docs/components/05-progression-vision.md §8. These
drive the real public entry points (event emission), not internal
function calls, and use a real DataRegistry against fixture content
rather than a mocked options list.

**15-integration-verification.md update**: `01-stats-combat.md` has now
merged, so both tests below use the real `engine.systems.stats.StatsSystem`
/`StatsComponent` instead of the `FakeStatsSystem` stand-in this file used
to carry (CONTRACTS.md §8: replace the stub once the real dependency
merges and the integration test still passes against it unchanged).
"""

from __future__ import annotations

import random
from pathlib import Path

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.registry import DataRegistry
from engine.core.spatial_hash import SpatialHash
from engine.systems.progression import ProgressionSystem, XpComponent
from engine.systems.stats import StatsComponent, StatsSystem
from engine.systems.vision import VisionSystem

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "progression" / "data"


def test_progression_pipeline_multi_level_up_with_real_registry_and_fixture_spells():
    world = World()
    bus = EventBus()
    registry = DataRegistry()
    registry.load(FIXTURE_ROOT)
    stats = StatsSystem(world, bus)

    ProgressionSystem(world, bus, registry=registry, stats_system=stats, rng=random.Random(7))

    level_up_events = []
    spell_choice_events = []
    bonus_events = []
    modifier_events = []
    bus.subscribe("level_up_pending", lambda p: level_up_events.append(p))
    bus.subscribe("spell_choice_pending", lambda p: spell_choice_events.append(p))
    bus.subscribe("bonus_points_remaining", lambda p: bonus_events.append(p))
    bus.subscribe("stat_modifier_applied", lambda p: modifier_events.append(p))

    killer = world.create_entity()
    world.add_component(killer, XpComponent())
    world.add_component(killer, StatsComponent(base={}, modifiers={}))
    victim = world.create_entity()

    # Fixture thresholds: [50, 120, 200] -- 130 xp crosses level 2 (50) and
    # level 3 (120) in one award.
    bus.emit("death", {"entity_id": victim, "killer_id": killer, "xp_value": 130})

    xp = world.get_component(killer, XpComponent)
    assert xp.current_xp == 130
    assert xp.level == 3

    assert len(level_up_events) == 2
    assert len(spell_choice_events) == 2
    real_spell_ids = set(registry.all("spells").keys())
    for event in spell_choice_events:
        assert event["entity_id"] == killer
        assert len(event["options"]) == 3
        assert set(event["options"]) <= real_spell_ids  # drawn from the real registry

    # bonus_points_per_level=2, two level-ups -> 4 total.
    assert bonus_events[-1] == {"entity_id": killer, "remaining": 4}

    # Automatic stat gains actually reached the real StatsSystem.add_modifier
    # (real stat_modifier_applied events landing on the real bus).
    auto_tags = [evt["tag"] for evt in modifier_events if evt["tag"].startswith("levelup_auto_")]
    assert len(auto_tags) == 2 * len({"max_hp", "strength"})


def test_vision_pipeline_lit_room_then_corridor_via_real_player_moved_event():
    world = World()
    bus = EventBus()
    spatial_hash = SpatialHash()
    stats = StatsSystem(world, bus)
    vision = VisionSystem(world, bus, stats_system=stats, spatial_hash=spatial_hash)

    lit_room = {
        "room_id": "throne_room",
        "lit": True,
        "tiles": [[x, y] for x in range(10, 14) for y in range(4, 8)],
    }
    vision.register_floor_rooms("1", [lit_room])

    entity = world.create_entity()
    world.add_component(
        entity, StatsComponent(base={"vision_range": 2, "trap_detect_radius": 0}, modifiers={})
    )

    # Step 1: enter the lit room -- the whole room is visible regardless of
    # distance to its far corner, driven through the real player_moved event.
    bus.emit("player_moved", {"entity_id": entity, "from": (0, 0), "to": (10, 4)})
    visible_in_room = vision.get_visible_tiles("1")
    assert (13, 7) in visible_in_room  # far corner of the lit room
    assert set(tuple(t) for t in lit_room["tiles"]) <= visible_in_room

    # Step 2: step out into a corridor well outside the lit room and outside
    # personal vision range of it.
    bus.emit("player_moved", {"entity_id": entity, "from": (10, 4), "to": (30, 30)})
    visible_in_corridor = vision.get_visible_tiles("1")
    seen = vision.get_seen_tiles("1")

    assert (13, 7) not in visible_in_corridor  # lit room no longer currently visible
    assert (13, 7) in seen  # but permanently remembered
    assert (30, 30) in visible_in_corridor  # personal radius disc around new position
    assert (32, 32) in visible_in_corridor  # chebyshev distance 2
    assert (33, 30) not in visible_in_corridor  # chebyshev distance 3, out of range
