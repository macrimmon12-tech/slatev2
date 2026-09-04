from dataclasses import dataclass

import pytest

from engine.core.ecs import World, component, is_component


@component
@dataclass
class PositionComponent:
    x: int
    y: int


@component
@dataclass
class NameComponent:
    name: str


@dataclass
class NotAComponent:
    value: int = 0


def test_component_decorator_marks_class():
    assert is_component(PositionComponent)
    assert not is_component(NotAComponent)


def test_component_decorator_requires_dataclass():
    class PlainClass:
        pass

    with pytest.raises(TypeError):
        component(PlainClass)


def test_component_default_to_dict_from_dict_roundtrip():
    pos = PositionComponent(x=3, y=4)
    data = pos.to_dict()
    assert data == {"x": 3, "y": 4}
    restored = PositionComponent.from_dict(data)
    assert restored == pos


def test_component_respects_custom_to_dict():
    @component
    @dataclass
    class CustomComponent:
        value: int

        def to_dict(self):
            return {"custom": self.value}

        @classmethod
        def from_dict(cls, data):
            return cls(value=data["custom"])

    inst = CustomComponent(value=7)
    assert inst.to_dict() == {"custom": 7}
    assert CustomComponent.from_dict({"custom": 7}) == inst


def test_create_entity_returns_unique_ids():
    world = World()
    a = world.create_entity()
    b = world.create_entity()
    assert a != b
    assert set(world.entities()) == {a, b}


def test_create_entity_with_explicit_id():
    world = World()
    world.create_entity(entity_id=42)
    assert 42 in world.entities()
    # Next auto-allocated ID must not collide with the explicit one.
    next_id = world.create_entity()
    assert next_id != 42


def test_create_entity_explicit_id_collision_raises():
    world = World()
    world.create_entity(entity_id=1)
    with pytest.raises(ValueError):
        world.create_entity(entity_id=1)


def test_add_component_requires_existing_entity():
    world = World()
    with pytest.raises(ValueError):
        world.add_component(999, PositionComponent(0, 0))


def test_add_get_remove_component():
    world = World()
    eid = world.create_entity()
    world.add_component(eid, PositionComponent(1, 2))

    got = world.get_component(eid, PositionComponent)
    assert got == PositionComponent(1, 2)

    world.remove_component(eid, PositionComponent)
    assert world.get_component(eid, PositionComponent) is None


def test_get_component_missing_returns_none():
    world = World()
    eid = world.create_entity()
    assert world.get_component(eid, PositionComponent) is None


def test_destroy_entity_removes_all_components():
    world = World()
    eid = world.create_entity()
    world.add_component(eid, PositionComponent(1, 1))
    world.add_component(eid, NameComponent("goblin"))

    world.destroy_entity(eid)

    assert eid not in world.entities()
    assert world.get_component(eid, PositionComponent) is None
    assert world.get_component(eid, NameComponent) is None


def test_destroy_entity_is_noop_if_missing():
    world = World()
    world.destroy_entity(12345)  # must not raise


def test_query_multi_component_intersection():
    world = World()
    a = world.create_entity()
    b = world.create_entity()
    c = world.create_entity()

    world.add_component(a, PositionComponent(0, 0))
    world.add_component(a, NameComponent("a"))

    world.add_component(b, PositionComponent(1, 1))
    # b has no NameComponent

    world.add_component(c, NameComponent("c"))
    # c has no PositionComponent

    results = world.query(PositionComponent, NameComponent)
    assert len(results) == 1
    entity_id, pos, name = results[0]
    assert entity_id == a
    assert pos == PositionComponent(0, 0)
    assert name == NameComponent("a")


def test_query_single_component():
    world = World()
    a = world.create_entity()
    world.add_component(a, PositionComponent(5, 5))
    results = world.query(PositionComponent)
    assert results == [(a, PositionComponent(5, 5))]


def test_query_no_component_types_returns_all_entities():
    world = World()
    a = world.create_entity()
    b = world.create_entity()
    results = world.query()
    assert set(r[0] for r in results) == {a, b}


def test_query_empty_when_no_entity_has_type():
    world = World()
    world.create_entity()
    assert world.query(PositionComponent) == []


def test_destroy_mid_query_safety():
    """Destroying an entity while iterating over an already-obtained query
    result must not raise — query() returns a materialized snapshot, not a
    live view over component storage."""
    world = World()
    entities = [world.create_entity() for _ in range(5)]
    for eid in entities:
        world.add_component(eid, PositionComponent(eid, eid))

    results = world.query(PositionComponent)
    assert len(results) == 5

    for entity_id, _pos in results:
        world.destroy_entity(entity_id)  # must not raise mid-iteration

    assert world.entities() == []
    assert world.query(PositionComponent) == []


def test_components_for_returns_all_types():
    world = World()
    eid = world.create_entity()
    world.add_component(eid, PositionComponent(1, 2))
    world.add_component(eid, NameComponent("x"))

    comps = world.components_for(eid)
    assert comps == {
        PositionComponent: PositionComponent(1, 2),
        NameComponent: NameComponent("x"),
    }
