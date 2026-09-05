"""``engine.lua.api.entity`` unit tests (component doc §7/§8)."""

from __future__ import annotations

import pytest

from engine.core.spatial_hash import SpatialHash
from engine.lua.api import entity as entity_api
from engine.lua.lua_host import LuaApiError
from engine.systems.ai import PositionComponent
from engine.systems.stats import StatsComponent

from tests.unit._lua_api_helpers import make_ctx


def test_get_position_none_without_component():
    ctx = make_ctx()
    api = entity_api.build(ctx)
    entity_id = ctx.world.create_entity()

    assert api["get_position"](entity_id) is None


def test_set_position_then_get_position_round_trips():
    ctx = make_ctx()
    api = entity_api.build(ctx)
    entity_id = ctx.world.create_entity()

    api["set_position"](entity_id, 3, 4)
    pos = api["get_position"](entity_id)

    assert pos.x == 3
    assert pos.y == 4


def test_set_position_moves_spatial_hash_when_wired():
    spatial_hash = SpatialHash()
    ctx = make_ctx(spatial_hash=spatial_hash)
    api = entity_api.build(ctx)
    entity_id = ctx.world.create_entity()

    api["set_position"](entity_id, 1, 1)
    assert spatial_hash.position_of(entity_id) == (1, 1)

    api["set_position"](entity_id, 5, 5)
    assert spatial_hash.position_of(entity_id) == (5, 5)


def test_get_component_returns_none_for_missing_component():
    ctx = make_ctx()
    api = entity_api.build(ctx)
    entity_id = ctx.world.create_entity()

    assert api["get_component"](entity_id, "StatsComponent") is None


def test_get_component_returns_read_only_copy():
    ctx = make_ctx()
    api = entity_api.build(ctx)
    entity_id = ctx.world.create_entity()
    ctx.world.add_component(entity_id, StatsComponent(base={"hp": 10.0}, modifiers={}))

    result = api["get_component"](entity_id, "StatsComponent")

    assert result.base.hp == 10.0
    # Mutating the returned Lua table must not affect the real component.
    result.base.hp = 999.0
    assert ctx.world.get_component(entity_id, StatsComponent).base["hp"] == 10.0


def test_get_component_unknown_name_returns_none():
    ctx = make_ctx()
    api = entity_api.build(ctx)
    entity_id = ctx.world.create_entity()

    assert api["get_component"](entity_id, "NotARealComponent") is None


def test_has_component_true_and_false():
    ctx = make_ctx()
    api = entity_api.build(ctx)
    entity_id = ctx.world.create_entity()

    assert api["has_component"](entity_id, "StatsComponent") is False
    ctx.world.add_component(entity_id, StatsComponent(base={}, modifiers={}))
    assert api["has_component"](entity_id, "StatsComponent") is True


def test_get_stat_wraps_stats_system():
    ctx = make_ctx()
    api = entity_api.build(ctx)
    entity_id = ctx.world.create_entity()
    ctx.world.add_component(entity_id, StatsComponent(base={"strength": 5.0}, modifiers={}))

    assert api["get_stat"](entity_id, "strength") == 5.0


def test_get_stat_missing_stats_component_is_zero():
    ctx = make_ctx()
    api = entity_api.build(ctx)
    entity_id = ctx.world.create_entity()

    assert api["get_stat"](entity_id, "strength") == 0.0


@pytest.mark.parametrize("stat_name", sorted(entity_api.COMBAT_DERIVED_STAT_NAMES))
def test_set_stat_rejects_every_combat_derived_stat_name(stat_name):
    ctx = make_ctx()
    api = entity_api.build(ctx)
    entity_id = ctx.world.create_entity()
    ctx.world.add_component(entity_id, StatsComponent(base={}, modifiers={}))

    with pytest.raises(LuaApiError):
        api["set_stat"](entity_id, stat_name, 999.0)
    # Case-insensitivity of the blocklist.
    with pytest.raises(LuaApiError):
        api["set_stat"](entity_id, stat_name.upper(), 999.0)


def test_set_stat_accepts_arbitrary_counter_and_reads_back():
    ctx = make_ctx()
    api = entity_api.build(ctx)
    entity_id = ctx.world.create_entity()
    ctx.world.add_component(entity_id, StatsComponent(base={}, modifiers={}))

    api["set_stat"](entity_id, "times_talked_to", 3)

    assert api["get_stat"](entity_id, "times_talked_to") == 3.0


def test_set_stat_no_stats_component_raises():
    ctx = make_ctx()
    api = entity_api.build(ctx)
    entity_id = ctx.world.create_entity()

    with pytest.raises(LuaApiError):
        api["set_stat"](entity_id, "quest_flag", 1)
