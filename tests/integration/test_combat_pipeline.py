"""Integration test proving the combat pipeline actually produces
damage_dealt -> death -> entity_died -> loot_drop end-to-end through the
real public entry point (resolve_hit), not a mock of EffectResolver or of
death handling (CONTRACTS.md §7.3 / 01-stats-combat.md §8)."""

import random

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.systems import combat, effects
from engine.systems.stats import StatsComponent, StatsSystem


def setup_function(_fn):
    combat.set_rng(random.Random(0))
    effects.set_rng(random.Random(0))
    combat.set_data_registry(None)
    combat.set_monster_data_lookup(None)
    combat._warned_no_data_registry = False
    combat._warned_no_monster_lookup = False


def test_resolve_hit_one_shot_kill_drives_full_death_pipeline():
    world = World()
    bus = EventBus()

    player = world.create_entity()
    world.add_component(player, combat.PlayerTagComponent())
    world.add_component(
        player,
        StatsComponent(
            base={"dex": 50, "damage_min": 50, "damage_max": 50, "hp": 30, "max_hp": 30},
            modifiers={},
        ),
    )

    monster = world.create_entity()
    world.add_component(
        monster,
        StatsComponent(base={"dex": 5, "hp": 5, "max_hp": 5}, modifiers={}),
    )
    combat.set_monster_data_lookup(
        lambda entity_id, w: {"xp_value": 7, "loot_table": {"entries": [{"item": "coin", "qty": 1}]}}
        if entity_id == monster
        else None
    )
    # Nudge the odds so a huge attacker-DEX gap gives a near-certain hit.
    rng = random.Random()
    rng.random = lambda: 0.0
    combat.set_rng(rng)

    damage_events = []
    death_events = []
    entity_died_events = []
    loot_drop_events = []
    bus.subscribe("damage_dealt", lambda p: damage_events.append(p))
    bus.subscribe("death", lambda p: death_events.append(p))
    bus.subscribe("entity_died", lambda p: entity_died_events.append(p))
    bus.subscribe("loot_drop", lambda p: loot_drop_events.append(p))

    hit = combat.resolve_hit(player, monster, world, bus)

    assert hit is True
    assert len(damage_events) == 1
    assert damage_events[0]["target_id"] == monster
    assert damage_events[0]["amount"] == 50.0
    assert damage_events[0]["source_id"] == player

    assert death_events == [{"entity_id": monster, "killer_id": player, "xp_value": 7}]
    assert entity_died_events == [{"entity_id": monster, "killer_id": player}]
    assert loot_drop_events == [
        {"position": None, "entries": [{"item": "coin", "qty": 1}], "is_boss": False}
    ]

    assert monster not in world.entities()
    assert world.get_component(monster, StatsComponent) is None
    # Player entity is untouched by the monster's death handling.
    assert player in world.entities()


def test_apply_effect_list_lifesteal_end_to_end_against_real_systems():
    """A full effect list [damage, lifesteal] run through apply_effect_list
    end-to-end against real StatsSystem/World instances -- proves the
    shared-context mechanism works against real components, not a mocked
    context dict."""
    world = World()
    bus = EventBus()
    stats_system = StatsSystem(world, bus)

    source = world.create_entity()
    world.add_component(source, StatsComponent(base={"hp": 20, "max_hp": 50}, modifiers={}))

    target = world.create_entity()
    world.add_component(target, StatsComponent(base={"hp": 40, "max_hp": 40}, modifiers={}))

    effect_list = [
        {"type": "damage", "amount": 16, "damage_type": "physical"},
        {"type": "lifesteal", "percent": 50},
    ]

    effects.apply_effect_list(effect_list, source, target, world, bus)

    assert stats_system.get_stat(target, "hp", world) == 24  # 40 - 16
    assert stats_system.get_stat(source, "hp", world) == 28  # 20 + (16 * 0.5)
