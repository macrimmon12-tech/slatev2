"""Unit tests for engine.systems.vision — see
docs/components/05-progression-vision.md §7/§8 for the Definition of Done
and Test Plan bullets these correspond to.
"""

from __future__ import annotations

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.spatial_hash import SpatialHash
from engine.systems.vision import VisionSystem


class FakeStatsSystem:
    """Stands in for 01-stats-combat.md's StatsSystem (not merged yet,
    CONTRACTS.md §8 stubbing)."""

    def __init__(self, stats: dict[str, float]) -> None:
        self._stats = stats

    def get_stat(self, entity_id: int, stat_name: str, world) -> float:
        return self._stats.get(stat_name, 0.0)


def make_system(stats: dict[str, float] | None = None) -> tuple[VisionSystem, World, EventBus, SpatialHash]:
    world = World()
    bus = EventBus()
    spatial_hash = SpatialHash()
    stats_system = FakeStatsSystem(stats or {"vision_range": 2, "trap_detect_radius": 3})
    system = VisionSystem(world, bus, stats_system=stats_system, spatial_hash=spatial_hash)
    return system, world, bus, spatial_hash


LIT_ROOM = {
    "room_id": "r_lit",
    "lit": True,
    "tiles": [[10, 4], [10, 5], [11, 4], [11, 5], [20, 20]],  # far corner included
}


def test_update_visibility_unions_lit_room_and_personal_radius():
    system, world, bus, spatial_hash = make_system()
    entity = world.create_entity()
    spatial_hash.insert(entity, (10, 4))
    system.register_floor_rooms("1", [LIT_ROOM])

    visible = system.update_visibility(entity, world)

    # Whole lit room visible regardless of distance to the far corner.
    assert (20, 20) in visible
    assert (11, 5) in visible
    # Personal radius disc around (10, 4) with vision_range=2 also present.
    assert (10, 6) in visible  # chebyshev distance 2
    assert (12, 4) in visible  # chebyshev distance 2
    assert (13, 4) not in visible  # chebyshev distance 3, out of range and not in room


def test_seen_tiles_are_a_permanent_superset_of_visible_tiles():
    system, world, bus, spatial_hash = make_system()
    entity = world.create_entity()
    spatial_hash.insert(entity, (10, 4))
    system.register_floor_rooms("1", [LIT_ROOM])

    system.update_visibility(entity, world)
    seen_before = system.get_seen_tiles("1")
    assert (20, 20) in seen_before

    # Move far away from the lit room, outside its tiles and personal range.
    spatial_hash.move(entity, (10, 4), (100, 100))
    visible_after = system.update_visibility(entity, world)

    assert (20, 20) not in visible_after
    assert (20, 20) in system.get_seen_tiles("1")  # still remembered
    assert system.get_seen_tiles("1") >= seen_before  # never shrinks


def test_unlit_room_does_not_reveal_tiles_outside_personal_radius():
    system, world, bus, spatial_hash = make_system()
    unlit_room = {**LIT_ROOM, "lit": False}
    system.register_floor_rooms("1", [unlit_room])
    entity = world.create_entity()
    spatial_hash.insert(entity, (10, 4))

    visible = system.update_visibility(entity, world)

    assert (20, 20) not in visible  # far corner not revealed, room isn't lit


def test_degrades_to_personal_radius_only_when_no_room_data_registered():
    system, world, bus, spatial_hash = make_system()
    entity = world.create_entity()
    spatial_hash.insert(entity, (0, 0))
    # No register_floor_rooms call at all -- simulates a handcrafted floor
    # missing room_id/lit data (spec §5.2/§6).

    visible = system.update_visibility(entity, world)  # must not raise

    assert (0, 0) in visible
    assert (2, 2) in visible  # within vision_range=2
    assert (3, 0) not in visible


def test_update_visibility_with_unknown_entity_position_returns_empty_and_does_not_raise():
    system, world, bus, spatial_hash = make_system()
    entity = world.create_entity()  # never inserted into spatial_hash

    visible = system.update_visibility(entity, world)

    assert visible == set()


# -- event wiring --------------------------------------------------------------


def test_player_moved_event_drives_update_visibility():
    system, world, bus, spatial_hash = make_system()
    entity = world.create_entity()
    system.register_floor_rooms("1", [LIT_ROOM])

    bus.emit(
        "player_moved",
        {"entity_id": entity, "from": (0, 0), "to": (10, 4)},
    )

    assert (20, 20) in system.get_visible_tiles("1")


def test_player_moved_integration_lit_room_then_corridor():
    """Mirrors the component doc's integration test scenario at the unit
    level: player_moved into the lit room reveals it all; moving to the
    corridor shrinks 'visible' to the personal disc while 'seen' still
    holds the lit room's tiles."""
    system, world, bus, spatial_hash = make_system()
    entity = world.create_entity()
    system.register_floor_rooms("1", [LIT_ROOM])

    bus.emit("player_moved", {"entity_id": entity, "from": (0, 0), "to": (10, 4)})
    assert (20, 20) in system.get_visible_tiles("1")

    bus.emit("player_moved", {"entity_id": entity, "from": (10, 4), "to": (100, 100)})
    assert (20, 20) not in system.get_visible_tiles("1")
    assert (20, 20) in system.get_seen_tiles("1")


def test_floor_changed_recomputes_visibility_for_the_known_player():
    system, world, bus, spatial_hash = make_system()
    entity = world.create_entity()
    spatial_hash.insert(entity, (5, 5))
    system.register_floor_rooms("2", [{"room_id": "r", "lit": True, "tiles": [[5, 5], [6, 6]]}])

    # Establish the player entity via a player_moved event first (see
    # module docstring's player-id-convention note).
    bus.emit("player_moved", {"entity_id": entity, "from": (5, 5), "to": (5, 5)})

    bus.emit("floor_changed", {"from_depth": 1, "to_depth": 2})

    assert (6, 6) in system.get_visible_tiles("2")


def test_floor_changed_without_a_known_player_is_a_silent_noop():
    system, world, bus, spatial_hash = make_system()

    # Must not raise even though no player_moved has ever established an
    # entity id yet.
    bus.emit("floor_changed", {"from_depth": 1, "to_depth": 2})


def test_player_moved_without_resolvable_entity_id_is_a_silent_noop():
    system, world, bus, spatial_hash = make_system()

    # No entity_id in the payload and none previously cached -- must not
    # raise (see module docstring's player-id-convention gap note).
    bus.emit("player_moved", {"from": (0, 0), "to": (1, 1)})


# -- trap detection stub (spec §12 / component doc §1.1) ----------------------


def test_scan_for_traps_reads_real_stat_and_never_raises():
    system, world, bus, spatial_hash = make_system(
        {"vision_range": 2, "trap_detect_radius": 5}
    )
    entity = world.create_entity()
    spatial_hash.insert(entity, (0, 0))

    trap_revealed_events = []
    bus.subscribe("trap_revealed", lambda payload: trap_revealed_events.append(payload))

    system.scan_for_traps(entity, world, bus)  # must not raise

    assert trap_revealed_events == []  # permanent no-op: no TrapComponent exists


def test_scan_for_traps_is_called_from_the_same_hook_as_update_visibility(monkeypatch):
    system, world, bus, spatial_hash = make_system()
    entity = world.create_entity()
    spatial_hash.insert(entity, (0, 0))

    calls = []
    monkeypatch.setattr(
        system, "scan_for_traps", lambda *args, **kwargs: calls.append(args)
    )

    bus.emit("player_moved", {"entity_id": entity, "from": (0, 0), "to": (0, 0)})

    assert len(calls) == 1


def test_scan_for_traps_with_unknown_position_is_a_noop():
    system, world, bus, spatial_hash = make_system()
    entity = world.create_entity()  # never inserted

    system.scan_for_traps(entity, world, bus)  # must not raise
