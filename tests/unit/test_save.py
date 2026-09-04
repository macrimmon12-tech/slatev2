from dataclasses import dataclass

import pytest

from engine.core import save
from engine.core.ecs import World, component


@component
@dataclass
class PositionComponent:
    x: int
    y: int


@component
@dataclass
class HealthComponent:
    current: int
    max: int


@pytest.fixture(autouse=True)
def _isolated_component_registry(monkeypatch):
    """Save tests register their own throwaway component types — isolate
    ``_COMPONENT_REGISTRY`` per test so they don't leak into each other or
    into the real lint check in test_component_registry.py."""
    monkeypatch.setattr(save, "_COMPONENT_REGISTRY", {
        "PositionComponent": PositionComponent,
        "HealthComponent": HealthComponent,
    })
    monkeypatch.setattr(save, "_warned_unregistered", set())
    yield


def test_serialize_world_roundtrip_multiple_component_types():
    world = World()
    a = world.create_entity()
    world.add_component(a, PositionComponent(1, 2))
    world.add_component(a, HealthComponent(10, 10))

    b = world.create_entity()
    world.add_component(b, PositionComponent(5, 5))

    data = save.serialize_world(world)

    restored = World()
    save.deserialize_world(data, restored)

    assert set(restored.entities()) == {a, b}
    assert restored.get_component(a, PositionComponent) == PositionComponent(1, 2)
    assert restored.get_component(a, HealthComponent) == HealthComponent(10, 10)
    assert restored.get_component(b, PositionComponent) == PositionComponent(5, 5)
    assert restored.get_component(b, HealthComponent) is None


def test_serialize_world_preserves_entity_ids():
    world = World()
    world.create_entity()  # id 1
    third = world.create_entity()
    world.destroy_entity(1)
    world.create_entity()  # reallocates differently depending on next_id
    data = save.serialize_world(world)

    restored = World()
    save.deserialize_world(data, restored)
    assert set(restored.entities()) == set(world.entities())
    assert third in restored.entities()


def test_serialize_world_skips_unregistered_component_type(monkeypatch, caplog):
    @component
    @dataclass
    class UnregisteredComponent:
        value: int

    world = World()
    eid = world.create_entity()
    world.add_component(eid, PositionComponent(0, 0))
    world.add_component(eid, UnregisteredComponent(99))

    with caplog.at_level("WARNING"):
        data = save.serialize_world(world)

    restored = World()
    save.deserialize_world(data, restored)

    assert restored.get_component(eid, PositionComponent) == PositionComponent(0, 0)
    assert restored.get_component(eid, UnregisteredComponent) is None
    assert any("UnregisteredComponent" in r.message for r in caplog.records)


def test_spawned_uniques_persist_at_top_level():
    world = World()
    spawned = {"legendary_excalibur", "legendary_frostmourne"}

    data = save.serialize_world(world, spawned_uniques=spawned)
    assert set(data["spawned_uniques"]) == spawned

    restored = World()
    restored_uniques: set[str] = set()
    save.deserialize_world(data, restored, spawned_uniques=restored_uniques)
    assert restored_uniques == spawned


def test_spawned_uniques_default_to_empty_set():
    world = World()
    data = save.serialize_world(world)
    assert data["spawned_uniques"] == []


def test_floor_snapshot_roundtrip_excludes_unlisted_entities():
    world = World()
    player = world.create_entity()
    world.add_component(player, PositionComponent(0, 0))

    monster = world.create_entity()
    world.add_component(monster, PositionComponent(3, 3))
    world.add_component(monster, HealthComponent(5, 5))

    # Caller excludes the player explicitly (component doc §2.5: player
    # state persists across floors outside any snapshot).
    snapshot = save.serialize_floor_snapshot(
        world, floor_id="floor_1", entity_ids=[monster], tilemap={"width": 10, "height": 10}
    )

    assert snapshot["floor_id"] == "floor_1"
    assert snapshot["tilemap"] == {"width": 10, "height": 10}
    assert str(player) not in snapshot["entities"]
    assert str(monster) in snapshot["entities"]

    restored = World()
    save.restore_floor_snapshot(restored, snapshot)

    assert player not in restored.entities()
    assert monster in restored.entities()
    assert restored.get_component(monster, PositionComponent) == PositionComponent(3, 3)
    assert restored.get_component(monster, HealthComponent) == HealthComponent(5, 5)


def test_floor_snapshot_defaults_to_all_world_entities():
    world = World()
    eid = world.create_entity()
    world.add_component(eid, PositionComponent(1, 1))

    snapshot = save.serialize_floor_snapshot(world, floor_id="f")
    assert str(eid) in snapshot["entities"]


def test_floor_snapshot_tilemap_is_opaque_passthrough():
    world = World()
    tilemap = {"tiles": [[0, 1], [1, 0]], "anything": "the save system doesn't parse this"}
    snapshot = save.serialize_floor_snapshot(world, floor_id="f", entity_ids=[], tilemap=tilemap)
    assert snapshot["tilemap"] == tilemap
