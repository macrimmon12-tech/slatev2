"""Integration test required by CONTRACTS.md §7.3 and
docs/components/02-ai-system.md §8: drives AISystem's real entry points
(`get_awake_monster_ids` + `take_turn`) end-to-end against a real `World`
and a real `EventBus`, asserting on genuine bus events — not a mock
assertion that `take_turn` "would have" attacked or moved.

**15-integration-verification.md update**: `01-stats-combat.md` has now
merged, so this re-points AISystem's `combat_system` at the real
`engine.systems.combat` module (`resolve_hit`/`handle_potential_death`)
instead of the `FixtureCombatSystem` stand-in this test used to use — per
this component doc's own §2.1 event-table audit and this test's original
docstring instruction to re-run against the real merged `CombatSystem`
before Wave 3 sign-off. Doing so also caught the exact "damage vs
damage_dealt" class of drift CONTRACTS.md §7.1 warns about, generalized:
the old fixture emitted `damage_dealt` with `attacker_id`/`defender_id`
keys, but the real event (`effects.py`'s `EffectResolver`, per CONTRACTS.md
§3.2) emits `target_id`/`source_id`/`amount`/`damage_type`/`position` — a
consumer coded against the fixture's payload shape would have silently
broken against the real one. Fixed here by asserting the real payload
shape instead of the fixture's invented one.
"""

from __future__ import annotations

import random

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.systems import combat
from engine.systems.ai import AIComponent, AISystem, PlayerTagComponent, PositionComponent
from engine.systems.stats import StatsComponent


def setup_function(_fn):
    # Real CombatSystem module-level state (RNG, difficulty config, monster
    # data lookup) is process-wide -- reset it between tests the same way
    # tests/unit/test_combat.py does, so tests in this file (and any run
    # before/after them in the same process) don't leak state into it.
    combat.set_rng(random.Random(0))
    combat.set_data_registry(None)
    combat.set_monster_data_lookup(None)


def _spawn_player(world: World, pos: tuple[int, int]) -> int:
    player_id = world.create_entity()
    world.add_component(player_id, PlayerTagComponent())
    world.add_component(player_id, PositionComponent(*pos))
    world.add_component(player_id, StatsComponent(base={"dexterity": 5, "hp": 20, "max_hp": 20}, modifiers={}))
    return player_id


def _spawn_chaser(world: World, pos: tuple[int, int], aggro_range: int = 10) -> int:
    entity_id = world.create_entity()
    world.add_component(entity_id, AIComponent(behavior="chaser", state="awake", aggro_range=aggro_range))
    world.add_component(entity_id, PositionComponent(*pos))
    world.add_component(
        entity_id,
        StatsComponent(base={"dexterity": 50, "damage_min": 3, "damage_max": 3, "hp": 10, "max_hp": 10}, modifiers={}),
    )
    return entity_id


def test_awake_chaser_adjacent_to_player_resolves_a_real_hit():
    world = World()
    event_bus = EventBus()
    # Huge DEX gap (monster 50 vs player 5) -> hit chance clamps to the
    # configured max, so a fixed seed reliably lands a hit.
    combat.set_rng(random.Random(0))
    ai_system = AISystem(world, event_bus, combat_system=combat)

    player_id = _spawn_player(world, (1, 0))
    monster_id = _spawn_chaser(world, (0, 0))

    observed_hit_events: list[dict] = []
    event_bus.subscribe("damage_dealt", lambda payload: observed_hit_events.append(payload))

    awake_ids = ai_system.get_awake_monster_ids(world)
    assert awake_ids == [monster_id]

    for entity_id in awake_ids:
        ai_system.take_turn(entity_id, world, event_bus)

    # Real event actually landed on the real bus, not a mock assertion --
    # real CONTRACTS.md §3.2 damage_dealt payload shape, not a fixture's.
    assert len(observed_hit_events) == 1
    assert observed_hit_events[0]["source_id"] == monster_id
    assert observed_hit_events[0]["target_id"] == player_id
    assert observed_hit_events[0]["amount"] == 3.0
    assert observed_hit_events[0]["damage_type"] == "physical"
    player_stats = world.get_component(player_id, StatsComponent)
    assert player_stats.base["hp"] == 17  # 20 - 3 real damage, not a mocked value


def test_awake_chaser_several_tiles_away_advances_toward_player_over_several_turns():
    world = World()
    event_bus = EventBus()
    # Huge DEX gap the other way (player 50 vs monster 5) -> the monster's
    # attacks reliably miss, so this test exercises pathfinding/movement
    # across several turns instead of ending the encounter in one hit.
    combat.set_rng(random.Random(0))

    # A wall at x == 3 with a single opening at y == 0 the monster must
    # route through — real find_path is exercised, not a straight line.
    blocked = {(3, y) for y in range(-5, 6) if y != 0}

    def is_passable(pos: tuple[int, int]) -> bool:
        return pos not in blocked

    ai_system = AISystem(world, event_bus, combat_system=combat, is_passable=is_passable)

    player_id = _spawn_player(world, (6, 0))
    world.get_component(player_id, StatsComponent).base["dexterity"] = 50
    monster_id = _spawn_chaser(world, (0, 2))
    world.get_component(monster_id, StatsComponent).base["dexterity"] = 5

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
