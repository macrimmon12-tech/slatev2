"""``engine.lua.api.lifecycle`` unit tests (component doc §7/§8)."""

from __future__ import annotations

from engine.core.registry import DataRegistry
from engine.core.spatial_hash import SpatialHash
from engine.lua.api import lifecycle as lifecycle_api
from engine.systems.ai import AIComponent, PositionComponent
from engine.systems.stats import StatsComponent

from tests.unit._lua_api_helpers import make_ctx


class _FakeRegistry:
    def __init__(self, entities: dict[str, dict]) -> None:
        self._entities = entities

    def get(self, namespace: str, entity_id: str):
        if namespace != "entities":
            return None
        return self._entities.get(entity_id)


def test_spawn_without_registry_is_a_safe_no_op():
    ctx = make_ctx(registry=None)
    api = lifecycle_api.build(ctx)

    assert api["spawn"]("goblin", 1, 1) is None


def test_spawn_unknown_entity_id_is_a_safe_no_op():
    ctx = make_ctx(registry=_FakeRegistry({}))
    api = lifecycle_api.build(ctx)

    assert api["spawn"]("does_not_exist", 1, 1) is None


def test_spawn_creates_position_and_stats():
    registry = _FakeRegistry({"goblin": {"stats": {"strength": 4}, "combat": {"damage_min": 1, "damage_max": 3}}})
    ctx = make_ctx(registry=registry)
    api = lifecycle_api.build(ctx)

    entity_id = api["spawn"]("goblin", 5, 6)

    assert entity_id is not None
    pos = ctx.world.get_component(entity_id, PositionComponent)
    assert (pos.x, pos.y) == (5, 6)
    stats = ctx.world.get_component(entity_id, StatsComponent)
    assert stats.base["strength"] == 4
    assert stats.base["damage_min"] == 1
    assert stats.base["damage_max"] == 3


def test_spawn_with_ai_block_attaches_ai_component():
    registry = _FakeRegistry({
        "goblin": {"stats": {}, "ai": {"behavior": "chaser"}},
    })
    ctx = make_ctx(registry=registry)
    api = lifecycle_api.build(ctx)

    entity_id = api["spawn"]("goblin", 0, 0)

    ai = ctx.world.get_component(entity_id, AIComponent)
    assert ai is not None
    assert ai.behavior == "chaser"


def test_spawn_without_ai_block_does_not_attach_ai_component():
    registry = _FakeRegistry({"villager": {"stats": {}}})
    ctx = make_ctx(registry=registry)
    api = lifecycle_api.build(ctx)

    entity_id = api["spawn"]("villager", 0, 0)

    assert ctx.world.get_component(entity_id, AIComponent) is None


def test_spawn_registers_with_spatial_hash_when_wired():
    spatial_hash = SpatialHash()
    registry = _FakeRegistry({"goblin": {"stats": {}}})
    ctx = make_ctx(registry=registry, spatial_hash=spatial_hash)
    api = lifecycle_api.build(ctx)

    entity_id = api["spawn"]("goblin", 2, 3)

    assert spatial_hash.position_of(entity_id) == (2, 3)


def test_spawn_emits_entity_spawned():
    registry = _FakeRegistry({"goblin": {"stats": {}}})
    ctx = make_ctx(registry=registry)
    api = lifecycle_api.build(ctx)
    events = []
    ctx.event_bus.subscribe("entity_spawned", lambda payload: events.append(payload))

    entity_id = api["spawn"]("goblin", 7, 8)

    assert events == [{"entity_id": entity_id, "kind": "goblin", "position": (7, 8)}]


def test_destroy_removes_entity_and_spatial_hash_entry():
    spatial_hash = SpatialHash()
    ctx = make_ctx(spatial_hash=spatial_hash)
    api = lifecycle_api.build(ctx)
    entity_id = ctx.world.create_entity()
    spatial_hash.insert(entity_id, (0, 0))

    api["destroy"](entity_id)

    assert entity_id not in ctx.world.entities()
    assert spatial_hash.position_of(entity_id) is None
