"""Unit tests for `engine.systems.ai` — see docs/components/02-ai-system.md §8."""

from __future__ import annotations

import json
from pathlib import Path

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.systems.ai import (
    AIComponent,
    AISystem,
    PlayerTagComponent,
    PositionComponent,
    VALID_BEHAVIORS,
    _sign,
    create_ai_component,
    find_path,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "monsters"


# ---------------------------------------------------------------------------
# Test doubles for 01/03's not-yet-merged systems (CONTRACTS.md §8: depend on
# the documented interface shape, don't import the real not-yet-existing
# module).
# ---------------------------------------------------------------------------


class FakeCombatSystem:
    def __init__(self, hits: bool = True) -> None:
        self.calls: list[tuple[int, int]] = []
        self._hits = hits

    def resolve_hit(self, attacker_id, defender_id, world, event_bus) -> bool:
        self.calls.append((attacker_id, defender_id))
        event_name = "damage_dealt" if self._hits else "miss"
        event_bus.emit(event_name, {"attacker_id": attacker_id, "defender_id": defender_id})
        return self._hits


class FakeSpellSystem:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, object]] = []

    def cast(self, entity_id, spell_id, target) -> None:
        self.calls.append((entity_id, spell_id, target))


class FakeStatsSystem:
    def __init__(self, stats: dict[int, dict[str, float]]) -> None:
        self._stats = stats

    def get_stat(self, entity_id, stat_name, world) -> float:
        return self._stats.get(entity_id, {}).get(stat_name, 0)

    def set_stat(self, entity_id, stat_name, value) -> None:
        self._stats.setdefault(entity_id, {})[stat_name] = value


def make_ai_system(**kwargs) -> tuple[AISystem, World, EventBus]:
    world = World()
    event_bus = EventBus()
    kwargs.setdefault("combat_system", FakeCombatSystem())
    ai_system = AISystem(world, event_bus, **kwargs)
    return ai_system, world, event_bus


def spawn_player(world: World, pos: tuple[int, int]) -> int:
    player_id = world.create_entity()
    world.add_component(player_id, PlayerTagComponent())
    world.add_component(player_id, PositionComponent(*pos))
    return player_id


def spawn_monster(world: World, ai: AIComponent, pos: tuple[int, int]) -> int:
    entity_id = world.create_entity()
    world.add_component(entity_id, ai)
    world.add_component(entity_id, PositionComponent(*pos))
    return entity_id


def load_fixture(name: str) -> dict:
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# create_ai_component / fixture round-trip / load-time validation
# ---------------------------------------------------------------------------


def test_fixture_monsters_round_trip_through_create_ai_component():
    fixture_files = sorted(FIXTURES_DIR.glob("*.json"))
    assert fixture_files, "expected at least one fixture monster JSON"

    seen_behaviors = set()
    for path in fixture_files:
        data = json.loads(path.read_text(encoding="utf-8"))
        ai = create_ai_component(data)
        assert ai is not None, f"{path.name} should produce a valid AIComponent"
        assert ai.behavior in VALID_BEHAVIORS
        seen_behaviors.add(ai.behavior)

    # §4: fixtures must cover all 4 behaviors plus one behavior_phases boss.
    assert seen_behaviors == VALID_BEHAVIORS
    boss_data = load_fixture("boss_twophase.json")
    assert boss_data["ai"]["behavior_phases"]


def test_create_ai_component_rejects_invalid_fifth_behavior():
    data = {"id": "bogus_monster", "ai": {"behavior": "necromancer"}}
    assert create_ai_component(data) is None


def test_create_ai_component_defaults_state_to_asleep_when_absent():
    data = {"id": "no_state_monster", "ai": {"behavior": "chaser"}}
    ai = create_ai_component(data)
    assert ai is not None
    assert ai.state == "asleep"


def test_create_ai_component_deep_copies_behavior_phases_not_the_shared_dict():
    shared_registry_dict = {
        "id": "boss",
        "ai": {
            "behavior": "chaser",
            "behavior_phases": [{"trigger": {"type": "hp_below", "value": 0.5}, "behavior": "coward", "one_shot": True}],
        },
    }
    ai = create_ai_component(shared_registry_dict)
    assert ai is not None
    ai.behavior_phases.clear()
    # Mutating the AIComponent's copy must never touch the original dict
    # returned by (what would be) registry.get() — CONTRACTS.md §5.2.
    assert shared_registry_dict["ai"]["behavior_phases"] != []


# ---------------------------------------------------------------------------
# Wake gating
# ---------------------------------------------------------------------------


def test_check_wake_wakes_asleep_monster_in_radius():
    ai_system, world, event_bus = make_ai_system()
    ai = AIComponent(behavior="chaser", state="asleep")
    monster_id = spawn_monster(world, ai, (5, 5))

    event_bus.emit("check_wake", {"entity_id": 0, "source_pos": (5, 8), "radius": 4})

    assert world.get_component(monster_id, AIComponent).state == "awake"


def test_check_wake_does_not_wake_monster_out_of_radius():
    ai_system, world, event_bus = make_ai_system()
    ai = AIComponent(behavior="chaser", state="asleep")
    monster_id = spawn_monster(world, ai, (5, 5))

    event_bus.emit("check_wake", {"entity_id": 0, "source_pos": (5, 20), "radius": 4})

    assert world.get_component(monster_id, AIComponent).state == "asleep"


def test_noise_emitted_wakes_asleep_monster_in_radius():
    ai_system, world, event_bus = make_ai_system()
    ai = AIComponent(behavior="chaser", state="asleep")
    monster_id = spawn_monster(world, ai, (0, 0))

    event_bus.emit("noise_emitted", {"position": (2, 2), "radius": 3, "source_id": 0})

    assert world.get_component(monster_id, AIComponent).state == "awake"


def test_noise_emitted_does_not_wake_monster_out_of_radius():
    ai_system, world, event_bus = make_ai_system()
    ai = AIComponent(behavior="chaser", state="asleep")
    monster_id = spawn_monster(world, ai, (0, 0))

    event_bus.emit("noise_emitted", {"position": (10, 10), "radius": 3, "source_id": 0})

    assert world.get_component(monster_id, AIComponent).state == "asleep"


def test_wake_events_never_touch_an_already_awake_monster():
    ai_system, world, event_bus = make_ai_system()
    ai = AIComponent(behavior="patroller", state="awake")
    monster_id = spawn_monster(world, ai, (0, 0))

    event_bus.emit("check_wake", {"entity_id": 0, "source_pos": (0, 0), "radius": 100})

    assert world.get_component(monster_id, AIComponent).state == "awake"


# ---------------------------------------------------------------------------
# A* pathfinding
# ---------------------------------------------------------------------------


def test_find_path_uses_equal_cost_diagonal_steps():
    world = World()
    path = find_path((0, 0), (3, 3), lambda pos: True, world)
    assert path == [(1, 1), (2, 2), (3, 3)]


def test_find_path_returns_empty_list_when_already_at_goal():
    world = World()
    assert find_path((2, 2), (2, 2), lambda pos: True, world) == []


def test_find_path_returns_none_when_goal_unreachable():
    world = World()

    def is_passable(pos):
        return pos[0] != 1  # a solid wall at x == 1 the goal is behind

    assert find_path((0, 0), (5, 0), is_passable, world) is None


def test_find_path_chebyshev_heuristic_differs_from_euclidean():
    """§8: 'A* correctness including the Chebyshev-vs-Euclidean
    distinguishing case.' Builds a wall with a single gap positioned so
    that a Euclidean-heuristic A* (reimplemented locally, standing in for
    'what this would do if Chebyshev weren't actually in effect') picks a
    different, though equally short, route through the gap than the real
    Chebyshev-heuristic `find_path` — proving the production heuristic is
    genuinely Chebyshev, not merely "some admissible distance metric"."""
    import heapq
    import itertools
    import math

    blocked = {(2, y) for y in range(-10, 11) if y != 3}

    def is_passable(pos):
        return pos not in blocked

    def euclidean_find_path(start, goal):
        neighbors = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
        counter = itertools.count()
        open_heap = [(math.dist(start, goal), next(counter), start)]
        came_from = {}
        g_score = {start: 0}
        closed = set()
        while open_heap:
            _f, _tie, current = heapq.heappop(open_heap)
            if current in closed:
                continue
            closed.add(current)
            if current == goal:
                path = [current]
                while current in came_from:
                    current = came_from[current]
                    path.append(current)
                path.reverse()
                return path[1:]
            for dx, dy in neighbors:
                neighbor = (current[0] + dx, current[1] + dy)
                if neighbor != goal and not is_passable(neighbor):
                    continue
                tentative_g = g_score[current] + 1
                if tentative_g < g_score.get(neighbor, math.inf):
                    g_score[neighbor] = tentative_g
                    came_from[neighbor] = current
                    heapq.heappush(open_heap, (tentative_g + math.dist(neighbor, goal), next(counter), neighbor))
        return None

    world = World()
    start, goal = (0, 0), (5, 3)

    chebyshev_path = find_path(start, goal, is_passable, world)
    euclidean_path = euclidean_find_path(start, goal)

    assert chebyshev_path is not None and euclidean_path is not None
    assert len(chebyshev_path) == len(euclidean_path)  # both optimal in step count
    assert chebyshev_path != euclidean_path  # but genuinely different routes
    assert not (blocked & set(chebyshev_path))
    assert not (blocked & set(euclidean_path))


# ---------------------------------------------------------------------------
# Path cache invalidation
# ---------------------------------------------------------------------------


def test_entity_moved_invalidates_path_cache_targeting_the_old_position():
    ai_system, world, event_bus = make_ai_system()
    ai = AIComponent(behavior="chaser", state="awake", path_cache=[(1, 1)], path_cache_target=(4, 4))
    spawn_monster(world, ai, (0, 0))

    event_bus.emit("entity_moved", {"entity_id": 999, "from": (4, 4), "to": (5, 4)})

    assert ai.path_cache is None
    assert ai.path_cache_target is None


def test_player_moved_invalidates_path_cache_targeting_the_old_position():
    ai_system, world, event_bus = make_ai_system()
    ai = AIComponent(behavior="chaser", state="awake", path_cache=[(1, 1)], path_cache_target=(4, 4))
    spawn_monster(world, ai, (0, 0))

    event_bus.emit("player_moved", {"from": (4, 4), "to": (5, 4)})

    assert ai.path_cache is None
    assert ai.path_cache_target is None


def test_entity_moved_leaves_unrelated_path_cache_alone():
    ai_system, world, event_bus = make_ai_system()
    ai = AIComponent(behavior="chaser", state="awake", path_cache=[(1, 1)], path_cache_target=(4, 4))
    spawn_monster(world, ai, (0, 0))

    event_bus.emit("entity_moved", {"entity_id": 999, "from": (9, 9), "to": (9, 8)})

    assert ai.path_cache == [(1, 1)]
    assert ai.path_cache_target == (4, 4)


def test_floor_changed_resets_all_ai_state():
    ai_system, world, event_bus = make_ai_system()
    ai = AIComponent(
        behavior="chaser",
        state="awake",
        path_cache=[(1, 1)],
        path_cache_target=(4, 4),
        turns_in_current_state=7,
        current_phase_index=1,
    )
    spawn_monster(world, ai, (0, 0))

    event_bus.emit("floor_changed", {"from_depth": 1, "to_depth": 2})

    assert ai.path_cache is None
    assert ai.path_cache_target is None
    assert ai.turns_in_current_state == 0
    assert ai.current_phase_index == -1
    assert ai.state == "awake"  # sleep state is untouched by floor_changed


# ---------------------------------------------------------------------------
# behavior_phases
# ---------------------------------------------------------------------------


def test_behavior_phases_hp_below_trigger_overrides_behavior():
    stats = FakeStatsSystem({})
    ai_system, world, event_bus = make_ai_system(stats_system=stats)
    ai = AIComponent(
        behavior="chaser",
        behavior_phases=[{"trigger": {"type": "hp_below", "value": 0.5}, "behavior": "coward", "one_shot": True}],
    )
    entity_id = spawn_monster(world, ai, (0, 0))
    stats.set_stat(entity_id, "hp", 5)
    stats.set_stat(entity_id, "max_hp", 40)

    active = ai_system._active_behavior(ai, world, entity_id)

    assert active == "coward"


def test_behavior_phases_hp_above_trigger():
    stats = FakeStatsSystem({})
    ai_system, world, event_bus = make_ai_system(stats_system=stats)
    ai = AIComponent(
        behavior="chaser",
        behavior_phases=[{"trigger": {"type": "hp_above", "value": 0.9}, "behavior": "coward", "one_shot": False}],
    )
    entity_id = spawn_monster(world, ai, (0, 0))
    stats.set_stat(entity_id, "hp", 39)
    stats.set_stat(entity_id, "max_hp", 40)

    assert ai_system._active_behavior(ai, world, entity_id) == "coward"

    stats.set_stat(entity_id, "hp", 10)
    assert ai_system._active_behavior(ai, world, entity_id) == "chaser"  # falls back, one_shot False re-evaluates


def test_behavior_phases_turn_count_above_trigger():
    ai_system, world, event_bus = make_ai_system()
    ai = AIComponent(
        behavior="patroller",
        turns_in_current_state=21,
        behavior_phases=[{"trigger": {"type": "turn_count_above", "value": 20}, "behavior": "chaser", "one_shot": False}],
    )
    entity_id = spawn_monster(world, ai, (0, 0))

    assert ai_system._active_behavior(ai, world, entity_id) == "chaser"


def test_behavior_phases_turn_count_below_trigger():
    ai_system, world, event_bus = make_ai_system()
    ai = AIComponent(
        behavior="patroller",
        turns_in_current_state=1,
        behavior_phases=[{"trigger": {"type": "turn_count_below", "value": 5}, "behavior": "coward", "one_shot": False}],
    )
    entity_id = spawn_monster(world, ai, (0, 0))

    assert ai_system._active_behavior(ai, world, entity_id) == "coward"


def test_behavior_phases_unknown_trigger_type_is_skipped():
    ai_system, world, event_bus = make_ai_system()
    ai = AIComponent(
        behavior="chaser",
        behavior_phases=[{"trigger": {"type": "made_up_trigger", "value": 1}, "behavior": "coward", "one_shot": False}],
    )
    entity_id = spawn_monster(world, ai, (0, 0))

    assert ai_system._active_behavior(ai, world, entity_id) == "chaser"


def test_behavior_phases_one_shot_fires_once_then_falls_back():
    stats = FakeStatsSystem({})
    ai_system, world, event_bus = make_ai_system(stats_system=stats)
    ai = AIComponent(
        behavior="chaser",
        behavior_phases=[{"trigger": {"type": "hp_below", "value": 0.5}, "behavior": "coward", "one_shot": True}],
    )
    entity_id = spawn_monster(world, ai, (0, 0))
    stats.set_stat(entity_id, "hp", 5)
    stats.set_stat(entity_id, "max_hp", 40)

    assert ai_system._active_behavior(ai, world, entity_id) == "coward"
    assert ai.behavior_phases == []  # one_shot entry removed after firing

    # Still below the threshold, but the phase is gone — falls back to base.
    assert ai_system._active_behavior(ai, world, entity_id) == "chaser"


def test_two_phase_boss_fixture_hp_then_turn_count():
    boss_data = load_fixture("boss_twophase.json")
    ai = create_ai_component(boss_data)
    assert ai is not None
    stats = FakeStatsSystem({})
    ai_system, world, event_bus = make_ai_system(stats_system=stats)
    entity_id = spawn_monster(world, ai, (0, 0))

    # Healthy and early: base behavior (chaser).
    stats.set_stat(entity_id, "hp", 40)
    stats.set_stat(entity_id, "max_hp", 40)
    assert ai_system._active_behavior(ai, world, entity_id) == "chaser"

    # HP drops below 50%: one_shot coward phase fires.
    stats.set_stat(entity_id, "hp", 15)
    assert ai_system._active_behavior(ai, world, entity_id) == "coward"
    # Healed back up — one_shot already consumed, stays base (chaser) even
    # though hp_below no longer matters; simulate enough turns for the
    # turn_count_above phase to then take over.
    stats.set_stat(entity_id, "hp", 40)
    ai.turns_in_current_state = 25
    assert ai_system._active_behavior(ai, world, entity_id) == "chaser"


# ---------------------------------------------------------------------------
# get_awake_monster_ids
# ---------------------------------------------------------------------------


def test_get_awake_monster_ids_returns_ascending_order_and_only_awake():
    ai_system, world, event_bus = make_ai_system()
    awake_a = spawn_monster(world, AIComponent(behavior="chaser", state="awake"), (0, 0))
    _asleep = spawn_monster(world, AIComponent(behavior="chaser", state="asleep"), (1, 1))
    awake_b = spawn_monster(world, AIComponent(behavior="patroller", state="awake"), (2, 2))

    ids = ai_system.get_awake_monster_ids(world)

    assert ids == sorted([awake_a, awake_b])


# ---------------------------------------------------------------------------
# The four behaviors
# ---------------------------------------------------------------------------


def test_chaser_moves_toward_player_when_out_of_melee_range():
    combat = FakeCombatSystem()
    ai_system, world, event_bus = make_ai_system(combat_system=combat)
    spawn_player(world, (5, 0))
    ai = AIComponent(behavior="chaser", state="awake", aggro_range=10)
    entity_id = spawn_monster(world, ai, (0, 0))

    ai_system.take_turn(entity_id, world, event_bus)

    new_pos = world.get_component(entity_id, PositionComponent)
    # Several 5-step routes tie for optimal (Chebyshev distance from (0,0)
    # to (5,0) is 5) — assert it took one valid step that got strictly
    # closer, not one specific tie-broken tile.
    assert max(abs(new_pos.x - 0), abs(new_pos.y - 0)) == 1  # moved exactly one tile
    old_distance = max(abs(0 - 5), abs(0 - 0))
    new_distance = max(abs(new_pos.x - 5), abs(new_pos.y - 0))
    assert new_distance == old_distance - 1
    assert combat.calls == []  # not adjacent yet, no attack


def test_chaser_attacks_when_adjacent():
    combat = FakeCombatSystem()
    ai_system, world, event_bus = make_ai_system(combat_system=combat)
    player_id = spawn_player(world, (1, 0))
    ai = AIComponent(behavior="chaser", state="awake", aggro_range=10)
    entity_id = spawn_monster(world, ai, (0, 0))

    ai_system.take_turn(entity_id, world, event_bus)

    assert combat.calls == [(entity_id, player_id)]
    pos = world.get_component(entity_id, PositionComponent)
    assert (pos.x, pos.y) == (0, 0)  # didn't move — attacked instead


def test_chaser_casts_spell_instead_of_melee_when_available():
    spells = FakeSpellSystem()
    combat = FakeCombatSystem()
    ai_system, world, event_bus = make_ai_system(combat_system=combat, spell_system=spells)
    player_id = spawn_player(world, (1, 0))
    ai = AIComponent(behavior="chaser", state="awake", aggro_range=10, spells=["firebolt"])
    entity_id = spawn_monster(world, ai, (0, 0))

    ai_system.take_turn(entity_id, world, event_bus)

    assert spells.calls == [(entity_id, "firebolt", player_id)]
    assert combat.calls == []


def test_chaser_stands_still_when_player_out_of_aggro_range():
    combat = FakeCombatSystem()
    ai_system, world, event_bus = make_ai_system(combat_system=combat)
    spawn_player(world, (50, 50))
    ai = AIComponent(behavior="chaser", state="awake", aggro_range=3)
    entity_id = spawn_monster(world, ai, (0, 0))

    ai_system.take_turn(entity_id, world, event_bus)

    pos = world.get_component(entity_id, PositionComponent)
    assert (pos.x, pos.y) == (0, 0)


def test_ambusher_stays_asleep_and_stationary_until_woken():
    ai_system, world, event_bus = make_ai_system()
    spawn_player(world, (1, 0))
    ai = AIComponent(behavior="ambusher", state="asleep", aggro_range=10)
    entity_id = spawn_monster(world, ai, (0, 0))

    assert ai_system.get_awake_monster_ids(world) == []  # never given a turn

    event_bus.emit("noise_emitted", {"position": (0, 0), "radius": 1, "source_id": 0})
    assert world.get_component(entity_id, AIComponent).state == "awake"
    assert ai_system.get_awake_monster_ids(world) == [entity_id]


def test_ambusher_behaves_as_chaser_once_awake():
    combat = FakeCombatSystem()
    ai_system, world, event_bus = make_ai_system(combat_system=combat)
    player_id = spawn_player(world, (1, 0))
    ai = AIComponent(behavior="ambusher", state="awake", aggro_range=10)
    entity_id = spawn_monster(world, ai, (0, 0))

    ai_system.take_turn(entity_id, world, event_bus)

    assert combat.calls == [(entity_id, player_id)]


def test_patroller_cycles_patrol_points():
    ai_system, world, event_bus = make_ai_system()
    ai = AIComponent(behavior="patroller", state="awake", aggro_range=1, patrol_points=[(2, 0), (2, 2), (0, 2)])
    entity_id = spawn_monster(world, ai, (0, 0))

    visited = []
    for _ in range(20):
        pos = world.get_component(entity_id, PositionComponent)
        visited.append((pos.x, pos.y))
        ai_system.take_turn(entity_id, world, event_bus)

    assert (2, 0) in visited
    assert (2, 2) in visited
    assert (0, 2) in visited


def test_patroller_switches_to_chasing_when_player_enters_aggro_range():
    combat = FakeCombatSystem()
    ai_system, world, event_bus = make_ai_system(combat_system=combat)
    player_id = spawn_player(world, (1, 0))
    ai = AIComponent(behavior="patroller", state="awake", aggro_range=10, patrol_points=[(9, 9)])
    entity_id = spawn_monster(world, ai, (0, 0))

    ai_system.take_turn(entity_id, world, event_bus)

    # Adjacent to the player and within aggro_range -> chases/attacks
    # instead of walking off toward its patrol point.
    assert combat.calls == [(entity_id, player_id)]


def test_patroller_does_nothing_with_no_patrol_points_and_no_player():
    ai_system, world, event_bus = make_ai_system()
    ai = AIComponent(behavior="patroller", state="awake", aggro_range=5, patrol_points=[])
    entity_id = spawn_monster(world, ai, (3, 3))

    ai_system.take_turn(entity_id, world, event_bus)

    pos = world.get_component(entity_id, PositionComponent)
    assert (pos.x, pos.y) == (3, 3)


def test_coward_does_not_flee_above_hp_threshold():
    stats = FakeStatsSystem({})
    ai_system, world, event_bus = make_ai_system(stats_system=stats)
    spawn_player(world, (1, 0))
    ai = AIComponent(behavior="coward", state="awake", flee_hp_threshold=0.2)
    entity_id = spawn_monster(world, ai, (0, 0))
    stats.set_stat(entity_id, "hp", 40)
    stats.set_stat(entity_id, "max_hp", 40)

    ai_system.take_turn(entity_id, world, event_bus)

    pos = world.get_component(entity_id, PositionComponent)
    assert (pos.x, pos.y) == (0, 0)


def test_coward_flees_below_hp_threshold():
    stats = FakeStatsSystem({})
    ai_system, world, event_bus = make_ai_system(stats_system=stats)
    spawn_player(world, (1, 0))
    ai = AIComponent(behavior="coward", state="awake", flee_hp_threshold=0.5)
    entity_id = spawn_monster(world, ai, (0, 0))
    stats.set_stat(entity_id, "hp", 5)
    stats.set_stat(entity_id, "max_hp", 40)

    ai_system.take_turn(entity_id, world, event_bus)

    pos = world.get_component(entity_id, PositionComponent)
    assert (pos.x, pos.y) != (0, 0)
    # Fled away from the player at (1, 0), i.e. to negative x.
    assert pos.x < 0


def test_coward_never_initiates_melee():
    combat = FakeCombatSystem()
    stats = FakeStatsSystem({})
    ai_system, world, event_bus = make_ai_system(combat_system=combat, stats_system=stats)
    spawn_player(world, (1, 0))  # adjacent
    ai = AIComponent(behavior="coward", state="awake", flee_hp_threshold=0.5)
    entity_id = spawn_monster(world, ai, (0, 0))
    stats.set_stat(entity_id, "hp", 5)
    stats.set_stat(entity_id, "max_hp", 40)

    ai_system.take_turn(entity_id, world, event_bus)

    assert combat.calls == []


def test_coward_casts_spell_while_fleeing_when_allowed():
    stats = FakeStatsSystem({})
    spells = FakeSpellSystem()
    ai_system, world, event_bus = make_ai_system(stats_system=stats, spell_system=spells)
    player_id = spawn_player(world, (1, 0))
    ai = AIComponent(
        behavior="coward",
        state="awake",
        flee_hp_threshold=0.5,
        can_cast_while_fleeing=True,
        spells=["minor_poison_spit"],
    )
    entity_id = spawn_monster(world, ai, (0, 0))
    stats.set_stat(entity_id, "hp", 5)
    stats.set_stat(entity_id, "max_hp", 40)

    ai_system.take_turn(entity_id, world, event_bus)

    assert spells.calls == [(entity_id, "minor_poison_spit", player_id)]


def test_coward_without_stats_system_never_flees():
    ai_system, world, event_bus = make_ai_system()  # no stats_system wired
    spawn_player(world, (1, 0))
    ai = AIComponent(behavior="coward", state="awake", flee_hp_threshold=0.99)
    entity_id = spawn_monster(world, ai, (0, 0))

    ai_system.take_turn(entity_id, world, event_bus)

    pos = world.get_component(entity_id, PositionComponent)
    assert (pos.x, pos.y) == (0, 0)


# ---------------------------------------------------------------------------
# entity_moved emission
# ---------------------------------------------------------------------------


def test_monster_move_emits_entity_moved_with_correct_payload():
    ai_system, world, event_bus = make_ai_system()
    spawn_player(world, (5, 0))
    ai = AIComponent(behavior="chaser", state="awake", aggro_range=10)
    entity_id = spawn_monster(world, ai, (0, 0))

    received = []
    event_bus.subscribe("entity_moved", lambda payload: received.append(payload))

    ai_system.take_turn(entity_id, world, event_bus)

    assert len(received) == 1
    payload = received[0]
    assert payload["entity_id"] == entity_id
    assert payload["from"] == (0, 0)
    # One valid step closer to the player — see test_chaser_moves_toward_
    # player_when_out_of_melee_range for why the exact tile isn't asserted.
    assert max(abs(payload["to"][0]), abs(payload["to"][1])) == 1


def test_sign_helper():
    assert _sign(5) == 1
    assert _sign(-5) == -1
    assert _sign(0) == 0
