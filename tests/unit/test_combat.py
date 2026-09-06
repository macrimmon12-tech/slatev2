"""Unit tests for engine/systems/combat.py — see
docs/components/01-stats-combat.md §7/§8."""

import json
import random
import re
from pathlib import Path

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.systems import combat
from engine.systems.stats import StatsComponent

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _make_entity(world: World, base: dict, modifiers: dict | None = None) -> int:
    entity_id = world.create_entity()
    world.add_component(entity_id, StatsComponent(base=dict(base), modifiers=dict(modifiers or {})))
    return entity_id


def setup_function(_fn):
    combat.set_rng(random.Random(0))
    combat.set_data_registry(None)
    combat.set_monster_data_lookup(None)
    combat._warned_no_data_registry = False
    combat._warned_no_monster_lookup = False
    combat._warned_no_ai_system = False
    combat._warned_depth_not_wired = False


# ---------------------------------------------------------------------------
# resolve_hit
# ---------------------------------------------------------------------------


def test_resolve_hit_miss_emits_miss_and_returns_false():
    world = World()
    bus = EventBus()
    attacker = _make_entity(world, {"dexterity": 5, "damage_min": 3, "damage_max": 6})
    defender = _make_entity(world, {"dexterity": 50, "hp": 20, "max_hp": 20})  # huge DEX gap -> low hit chance
    received_miss = []
    bus.subscribe("miss", lambda payload: received_miss.append(payload))
    combat.set_rng(random.Random(1))  # a roll high enough to miss at this hit chance

    result = combat.resolve_hit(attacker, defender, world, bus)

    assert result is False
    assert received_miss == [{"attacker_id": attacker, "defender_id": defender}]


def test_resolve_hit_hit_deals_damage_and_returns_true():
    world = World()
    bus = EventBus()
    attacker = _make_entity(world, {"dexterity": 50, "damage_min": 5, "damage_max": 5})
    defender = _make_entity(world, {"dexterity": 5, "hp": 20, "max_hp": 20})  # huge DEX gap -> high hit chance
    received_damage = []
    bus.subscribe("damage_dealt", lambda payload: received_damage.append(payload))
    combat.set_rng(random.Random(0))

    result = combat.resolve_hit(attacker, defender, world, bus)

    assert result is True
    assert len(received_damage) == 1
    assert received_damage[0]["amount"] == 5.0
    defender_stats = world.get_component(defender, StatsComponent)
    assert defender_stats.base["hp"] == 15


def test_resolve_hit_hit_chance_is_clamped_to_configured_max():
    world = World()
    bus = EventBus()
    # Absurd DEX gap pushes the raw formula result far above 1.0.
    attacker = _make_entity(world, {"dexterity": 100000, "damage_min": 1, "damage_max": 1})
    defender = _make_entity(world, {"dexterity": 0, "hp": 100, "max_hp": 100})
    rng = random.Random()
    rng.random = lambda: 0.96  # above hit_chance_max (0.95); would still "hit" if unclamped
    combat.set_rng(rng)

    result = combat.resolve_hit(attacker, defender, world, bus)

    assert result is False  # proves the 0.95 clamp is applied, not the raw (huge) formula value


# ---------------------------------------------------------------------------
# Depth multiplier lookup (v1 regression guard: must actually index by
# depth, not hardcode [0] — 01-stats-combat.md §5.3/§6)
# ---------------------------------------------------------------------------


def test_depth_multipliers_differ_between_depth_3_and_depth_12():
    low_depth = combat.depth_multipliers_for(3)
    high_depth = combat.depth_multipliers_for(12)

    assert low_depth != high_depth
    assert low_depth == (1.0, 1.0)
    assert high_depth == (1.6, 1.75)


def test_depth_multipliers_matches_correct_band_for_middle_depth():
    assert combat.depth_multipliers_for(7) == (1.25, 1.3)


def test_depth_multipliers_falls_back_to_identity_for_unmatched_depth():
    config = {"depth_multipliers": [{"min_depth": 1, "max_depth": 2, "monster_damage_mult": 2.0, "monster_hp_mult": 2.0}]}
    assert combat.depth_multipliers_for(50, config=config) == (1.0, 1.0)


def test_resolve_hit_applies_monster_damage_mult_for_non_player_attacker():
    world = World()
    bus = EventBus()
    attacker = _make_entity(world, {"dexterity": 50, "damage_min": 10, "damage_max": 10})
    defender = _make_entity(world, {"dexterity": 5, "hp": 100, "max_hp": 100})
    combat.set_rng(random.Random(0))

    monkeypatched_config = dict(combat._DEFAULT_DIFFICULTY_CONFIG)
    monkeypatched_config["depth_multipliers"] = [
        {"min_depth": 1, "max_depth": 999, "monster_damage_mult": 2.0, "monster_hp_mult": 1.0}
    ]

    class _StubRegistry:
        def get(self, namespace, id):
            assert (namespace, id) == ("configs", "difficulty")
            return monkeypatched_config

    combat.set_data_registry(_StubRegistry())

    combat.resolve_hit(attacker, defender, world, bus)

    defender_stats = world.get_component(defender, StatsComponent)
    assert defender_stats.base["hp"] == 100 - 20  # 10 base damage * 2.0 monster_damage_mult


def test_resolve_hit_does_not_apply_monster_damage_mult_for_player_attacker():
    world = World()
    bus = EventBus()
    attacker = _make_entity(world, {"dexterity": 50, "damage_min": 10, "damage_max": 10})
    world.add_component(attacker, combat.PlayerTagComponent())
    defender = _make_entity(world, {"dexterity": 5, "hp": 100, "max_hp": 100})
    combat.set_rng(random.Random(0))

    monkeypatched_config = dict(combat._DEFAULT_DIFFICULTY_CONFIG)
    monkeypatched_config["depth_multipliers"] = [
        {"min_depth": 1, "max_depth": 999, "monster_damage_mult": 2.0, "monster_hp_mult": 1.0}
    ]

    class _StubRegistry:
        def get(self, namespace, id):
            return monkeypatched_config

    combat.set_data_registry(_StubRegistry())

    combat.resolve_hit(attacker, defender, world, bus)

    defender_stats = world.get_component(defender, StatsComponent)
    assert defender_stats.base["hp"] == 100 - 10  # no monster mult for the player


# ---------------------------------------------------------------------------
# handle_potential_death
# ---------------------------------------------------------------------------


def test_handle_potential_death_noop_if_still_alive():
    world = World()
    bus = EventBus()
    target = _make_entity(world, {"hp": 5, "max_hp": 10})
    received = []
    bus.subscribe("player_died", lambda p: received.append(p))
    bus.subscribe("death", lambda p: received.append(p))

    combat.handle_potential_death(target, killer_id=0, world=world, event_bus=bus)

    assert received == []
    assert world.get_component(target, StatsComponent) is not None


def test_handle_potential_death_player_survives_and_emits_player_died():
    world = World()
    bus = EventBus()
    target = _make_entity(world, {"hp": 0, "max_hp": 10})
    world.add_component(target, combat.PlayerTagComponent())
    received = []
    bus.subscribe("player_died", lambda p: received.append(("player_died", p)))
    bus.subscribe("death", lambda p: received.append(("death", p)))
    bus.subscribe("entity_died", lambda p: received.append(("entity_died", p)))

    combat.handle_potential_death(target, killer_id=999, world=world, event_bus=bus)

    assert received == [("player_died", {"entity_id": target})]
    assert world.get_component(target, StatsComponent) is not None  # entity stays intact
    assert target in world.entities()


def test_handle_potential_death_monster_destroyed_with_correct_event_order():
    world = World()
    bus = EventBus()
    killer = world.create_entity()
    target = _make_entity(world, {"hp": 0, "max_hp": 10})
    order = []
    bus.subscribe("death", lambda p: order.append(("death", p)))
    bus.subscribe("entity_died", lambda p: order.append(("entity_died", p)))
    bus.subscribe("loot_drop", lambda p: order.append(("loot_drop", p)))

    combat.set_monster_data_lookup(
        lambda entity_id, w: {"xp_value": 42, "loot_table": {"entries": [{"item": "gold", "qty": 5}]}}
    )

    combat.handle_potential_death(target, killer_id=killer, world=world, event_bus=bus)

    assert [name for name, _ in order] == ["death", "entity_died", "loot_drop"]
    assert order[0][1] == {"entity_id": target, "killer_id": killer, "xp_value": 42}
    assert order[1][1] == {"entity_id": target, "killer_id": killer}
    assert order[2][1]["entries"] == [{"item": "gold", "qty": 5}]
    assert target not in world.entities()
    assert world.get_component(target, StatsComponent) is None


def test_handle_potential_death_monster_defaults_xp_and_loot_without_lookup():
    world = World()
    bus = EventBus()
    target = _make_entity(world, {"hp": 0, "max_hp": 10})
    received = []
    bus.subscribe("death", lambda p: received.append(p))

    combat.handle_potential_death(target, killer_id=0, world=world, event_bus=bus)

    assert received == [{"entity_id": target, "killer_id": 0, "xp_value": 0}]


# ---------------------------------------------------------------------------
# process_monster_turns
# ---------------------------------------------------------------------------


def test_process_monster_turns_is_a_noop_if_ai_system_unavailable(monkeypatch):
    """Regression guard for the documented CONTRACTS.md §8 stubbing
    fallback -- engine.systems.ai is merged in this repo now, so this test
    forces the ImportError branch via sys.modules rather than relying on
    the dependency's absence."""
    import sys

    world = World()
    bus = EventBus()
    _make_entity(world, {"hp": 10})
    monkeypatch.setitem(sys.modules, "engine.systems.ai", None)  # forces ImportError on import

    combat.process_monster_turns(world, bus)  # must not raise


def test_process_monster_turns_calls_real_ai_system_for_awake_monsters_in_ascending_order():
    """Drives the real, now-merged 02-ai-system.md AISystem end-to-end
    (CONTRACTS.md §8: once a stubbed dependency merges, integrate against
    the real thing) rather than a hand-rolled fake module."""
    from engine.systems.ai import AIComponent

    world = World()
    bus = EventBus()

    def make_monster(awake: bool) -> int:
        entity_id = _make_entity(world, {"hp": 10})
        world.add_component(
            entity_id, AIComponent(behavior="patroller", state="awake" if awake else "asleep")
        )
        return entity_id

    monster_c = make_monster(awake=True)
    monster_a = make_monster(awake=True)
    asleep_monster = make_monster(awake=False)
    player = _make_entity(world, {"hp": 10})
    world.add_component(player, combat.PlayerTagComponent())

    call_order = []
    ai_system = combat._get_ai_system(world, bus)
    ai_system.take_turn = lambda entity_id, w, eb: call_order.append(entity_id)

    combat.process_monster_turns(world, bus)

    assert call_order == sorted([monster_a, monster_c])
    assert asleep_monster not in call_order
    assert player not in call_order


def test_process_monster_turns_reuses_one_ai_system_instance_per_world():
    """AISystem.__init__ subscribes handlers onto the event bus -- calling
    process_monster_turns repeatedly must not construct (and re-subscribe)
    a fresh instance each time."""
    world = World()
    bus = EventBus()
    _make_entity(world, {"hp": 10})

    combat.process_monster_turns(world, bus)
    first = combat._get_ai_system(world, bus)
    combat.process_monster_turns(world, bus)
    second = combat._get_ai_system(world, bus)

    assert first is second


# ---------------------------------------------------------------------------
# difficulty.json schema validation (CONTRACTS.md §9)
# ---------------------------------------------------------------------------


def test_validate_difficulty_config_accepts_real_file():
    config = json.loads((REPO_ROOT / "data" / "config" / "difficulty.json").read_text())
    assert combat.validate_difficulty_config(config) == []


def test_validate_difficulty_config_flags_missing_field():
    assert combat.validate_difficulty_config({}) != []


def test_default_difficulty_config_matches_real_file():
    """The Python fallback used when no DataRegistry is wired must mirror
    the real file exactly, so behavior doesn't silently change once wiring
    lands."""
    config = json.loads((REPO_ROOT / "data" / "config" / "difficulty.json").read_text())
    assert combat._DEFAULT_DIFFICULTY_CONFIG == config


# ---------------------------------------------------------------------------
# v1 lesson: exactly one damage event in the codebase (§6)
# ---------------------------------------------------------------------------

_EMIT_LITERAL_DAMAGE_RE = re.compile(r'''emit\(\s*["']damage["']''')


def test_no_literal_damage_event_emitted_anywhere_in_systems():
    systems_dir = REPO_ROOT / "engine" / "systems"
    for path in sorted(systems_dir.glob("*.py")):
        text = path.read_text()
        assert not _EMIT_LITERAL_DAMAGE_RE.search(text), (
            f"found a literal emit(\"damage\", ...) in {path} -- only "
            "damage_dealt is allowed (01-stats-combat.md §6)"
        )
