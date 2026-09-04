"""Integration test proving the whole foundation substrate coheres — not
just each module in isolation (CONTRACTS.md §7.3 / component doc §8):
builds a World + DataRegistry + event bus from a fixture data tree, spawns
entities with mixed components (some driven by registry content, one
positioned via the spatial hash), saves, tears everything down, reloads,
and asserts entity/component/position state matches.
"""

from dataclasses import dataclass
from pathlib import Path

import pytest

from engine.core import events, save
from engine.core.ecs import World, component
from engine.core.formula import eval_formula, roll_dice
from engine.core.registry import DataRegistry
from engine.core.spatial_hash import SpatialHash, chebyshev_distance

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "data_registry" / "base"


@component
@dataclass
class PositionComponent:
    x: int
    y: int


@component
@dataclass
class StatsComponent:
    strength: int
    dexterity: int


@component
@dataclass
class MonsterTagComponent:
    entity_kind: str  # the registry id this entity was spawned from


COMPONENT_REGISTRY_ENTRIES = {
    "PositionComponent": PositionComponent,
    "StatsComponent": StatsComponent,
    "MonsterTagComponent": MonsterTagComponent,
}


def test_full_foundation_boot_spawn_save_reload_roundtrip(monkeypatch):
    monkeypatch.setattr(save, "_COMPONENT_REGISTRY", dict(COMPONENT_REGISTRY_ENTRIES))
    monkeypatch.setattr(save, "_warned_unregistered", set())

    bus = events.EventBus()
    spawn_log = []
    bus.subscribe("entity_spawned", lambda payload: spawn_log.append(payload))

    world = World()
    registry = DataRegistry()
    registry.load(FIXTURES)
    spatial = SpatialHash()

    # Spawn a monster driven by registry content, formula evaluation, and
    # dice rolling all at once — this is the "whole substrate" part: no
    # single module is enough on its own to make this meaningful.
    goblin_data = registry.get("entities", "goblin")
    assert goblin_data is not None

    monster = world.create_entity()
    world.add_component(monster, PositionComponent(x=5, y=5))
    world.add_component(monster, StatsComponent(strength=10, dexterity=6))
    world.add_component(monster, MonsterTagComponent(entity_kind=goblin_data["id"]))
    spatial.insert(monster, (5, 5))
    bus.emit("entity_spawned", {"entity_id": monster, "kind": goblin_data["id"], "position": (5, 5)})

    # A second, unpositioned entity to prove multi-entity round-trips work.
    npc_data = registry.get("npcs", "villager")
    assert npc_data is not None
    villager = world.create_entity()
    world.add_component(villager, MonsterTagComponent(entity_kind=npc_data["id"]))

    # Exercise formula.py against the spawned entity's own stats, the way a
    # real system would compute a derived value from StatsComponent.
    stats = world.get_component(monster, StatsComponent)
    to_hit_penalty = eval_formula("0.4 - (DEX * 0.03)", {"DEX": stats.dexterity})
    assert to_hit_penalty == pytest.approx(0.4 - 6 * 0.03)

    damage_roll = roll_dice("1d1")  # deterministic: always 1
    assert damage_roll == 1

    # Exercise the spatial hash + canonical distance function together.
    nearby = spatial.query_radius((5, 5), radius=0)
    assert nearby == [monster]
    assert chebyshev_distance((5, 5), (7, 6)) == 2

    assert spawn_log == [{"entity_id": monster, "kind": "goblin", "position": (5, 5)}]

    # Save, tear down, reload — the round trip the whole test exists to prove.
    spawned_uniques = {"legendary_excalibur"}
    saved = save.serialize_world(world, spawned_uniques=spawned_uniques)

    fresh_world = World()
    restored_uniques: set[str] = set()
    save.deserialize_world(saved, fresh_world, spawned_uniques=restored_uniques)

    assert set(fresh_world.entities()) == {monster, villager}
    assert fresh_world.get_component(monster, PositionComponent) == PositionComponent(5, 5)
    assert fresh_world.get_component(monster, StatsComponent) == StatsComponent(10, 6)
    assert fresh_world.get_component(monster, MonsterTagComponent) == MonsterTagComponent("goblin")
    assert fresh_world.get_component(villager, MonsterTagComponent) == MonsterTagComponent("villager")
    assert fresh_world.get_component(villager, PositionComponent) is None
    assert restored_uniques == spawned_uniques


def test_floor_snapshot_is_part_of_the_same_coherent_boot(monkeypatch):
    monkeypatch.setattr(save, "_COMPONENT_REGISTRY", dict(COMPONENT_REGISTRY_ENTRIES))
    monkeypatch.setattr(save, "_warned_unregistered", set())

    world = World()
    player = world.create_entity()
    world.add_component(player, PositionComponent(0, 0))

    monster = world.create_entity()
    world.add_component(monster, PositionComponent(9, 9))
    world.add_component(monster, MonsterTagComponent("goblin"))

    snapshot = save.serialize_floor_snapshot(
        world, floor_id="depth_1", entity_ids=[monster], tilemap={"width": 20, "height": 20}
    )

    reloaded_world = World()
    save.restore_floor_snapshot(reloaded_world, snapshot)

    assert player not in reloaded_world.entities()
    assert reloaded_world.get_component(monster, PositionComponent) == PositionComponent(9, 9)
    assert snapshot["tilemap"] == {"width": 20, "height": 20}
