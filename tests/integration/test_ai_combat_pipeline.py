"""Integration test required by CONTRACTS.md §7.3 and
docs/components/02-ai-system.md §8: drives AISystem's real entry points
(`get_awake_monster_ids` + `take_turn`) end-to-end against a real `World`
and a real `EventBus`, asserting on genuine bus events — not a mock
assertion that `take_turn` "would have" attacked or moved.

`01-stats-combat.md`'s real `CombatSystem` isn't merged yet. Per
CONTRACTS.md §8, this mocks only the `events.emit`/`subscribe` boundary
(via a small stand-in exposing the documented `resolve_hit` shape) rather
than importing a not-yet-existing module — the stand-in still emits real
events through the real `EventBus`, so what this test observes (a `miss`/
`damage_dealt` event landing on the bus, `entity_moved` events firing,
positions actually changing in `World`) is genuine bus/world state, not a
mock call log. Re-run this test against the real merged `CombatSystem`
before Wave 3 integration sign-off (docs/components/15-integration-verification.md).
"""

from __future__ import annotations

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.systems.ai import AIComponent, AISystem, PlayerTagComponent, PositionComponent


class FixtureCombatSystem:
    """Stand-in for 01's `CombatSystem.resolve_hit(attacker_id, defender_id,
    world, event_bus) -> bool` — see module docstring."""

    def __init__(self, hits: bool = True) -> None:
        self.calls: list[tuple[int, int]] = []
        self._hits = hits

    def resolve_hit(self, attacker_id, defender_id, world, event_bus) -> bool:
        self.calls.append((attacker_id, defender_id))
        event_name = "damage_dealt" if self._hits else "miss"
        event_bus.emit(
            event_name,
            {"attacker_id": attacker_id, "defender_id": defender_id, "amount": 3, "damage_type": "physical"},
        )
        return self._hits


def _spawn_player(world: World, pos: tuple[int, int]) -> int:
    player_id = world.create_entity()
    world.add_component(player_id, PlayerTagComponent())
    world.add_component(player_id, PositionComponent(*pos))
    return player_id


def _spawn_chaser(world: World, pos: tuple[int, int], aggro_range: int = 10) -> int:
    entity_id = world.create_entity()
    world.add_component(entity_id, AIComponent(behavior="chaser", state="awake", aggro_range=aggro_range))
    world.add_component(entity_id, PositionComponent(*pos))
    return entity_id


def test_awake_chaser_adjacent_to_player_resolves_a_real_hit():
    world = World()
    event_bus = EventBus()
    combat = FixtureCombatSystem(hits=True)
    ai_system = AISystem(world, event_bus, combat_system=combat)

    player_id = _spawn_player(world, (1, 0))
    monster_id = _spawn_chaser(world, (0, 0))

    observed_hit_events: list[dict] = []
    event_bus.subscribe("damage_dealt", lambda payload: observed_hit_events.append(payload))

    awake_ids = ai_system.get_awake_monster_ids(world)
    assert awake_ids == [monster_id]

    for entity_id in awake_ids:
        ai_system.take_turn(entity_id, world, event_bus)

    # Real event actually landed on the real bus, not a mock assertion.
    assert len(observed_hit_events) == 1
    assert observed_hit_events[0]["attacker_id"] == monster_id
    assert observed_hit_events[0]["defender_id"] == player_id
    assert combat.calls == [(monster_id, player_id)]


def test_awake_chaser_several_tiles_away_advances_toward_player_over_several_turns():
    world = World()
    event_bus = EventBus()
    combat = FixtureCombatSystem(hits=False)

    # A wall at x == 3 with a single opening at y == 0 the monster must
    # route through — real find_path is exercised, not a straight line.
    blocked = {(3, y) for y in range(-5, 6) if y != 0}

    def is_passable(pos: tuple[int, int]) -> bool:
        return pos not in blocked

    ai_system = AISystem(world, event_bus, combat_system=combat, is_passable=is_passable)

    _player_id = _spawn_player(world, (6, 0))
    monster_id = _spawn_chaser(world, (0, 2))

    moved_events: list[dict] = []
    event_bus.subscribe("entity_moved", lambda payload: moved_events.append(payload))

    positions_over_time = []
    for _ in range(12):
        awake_ids = ai_system.get_awake_monster_ids(world)
        for entity_id in awake_ids:
            ai_system.take_turn(entity_id, world, event_bus)
        pos = world.get_component(monster_id, PositionComponent)
        positions_over_time.append((pos.x, pos.y))

    start_distance = max(abs(0 - 6), abs(2 - 0))
    final_pos = positions_over_time[-1]
    final_distance = max(abs(final_pos[0] - 6), abs(final_pos[1] - 0))

    assert final_distance < start_distance  # actually advanced toward the player
    assert len(moved_events) >= 3  # real entity_moved events, not a mock
    assert all(evt["entity_id"] == monster_id for evt in moved_events)
    # Never stepped onto a blocked tile en route.
    assert all((evt["to"][0], evt["to"][1]) not in blocked for evt in moved_events)
