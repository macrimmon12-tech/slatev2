"""``engine.lua.api.state`` unit tests (component doc §7/§8)."""

from __future__ import annotations

from engine.lua.api import state as state_api
from engine.lua.lua_host import LuaCampaignStateComponent, LuaFloorStateComponent

from tests.unit._lua_api_helpers import make_ctx


def test_get_floor_state_default_without_entity():
    ctx = make_ctx()
    api = state_api.build(ctx)

    assert api["get_floor_state"]("k", "fallback") == "fallback"


def test_set_then_get_floor_state_round_trips():
    ctx = make_ctx()
    floor_entity = ctx.world.create_entity()
    ctx.world.add_component(floor_entity, LuaFloorStateComponent(data={}))
    ctx.floor_state_entity = floor_entity
    api = state_api.build(ctx)

    api["set_floor_state"]("gold_found", 12)

    assert api["get_floor_state"]("gold_found", 0) == 12
    assert ctx.world.get_component(floor_entity, LuaFloorStateComponent).data["gold_found"] == 12


def test_get_floor_state_missing_key_returns_default():
    ctx = make_ctx()
    floor_entity = ctx.world.create_entity()
    ctx.world.add_component(floor_entity, LuaFloorStateComponent(data={}))
    ctx.floor_state_entity = floor_entity
    api = state_api.build(ctx)

    assert api["get_floor_state"]("nope", "default") == "default"


def test_set_floor_state_without_entity_is_a_safe_no_op():
    ctx = make_ctx()
    api = state_api.build(ctx)

    api["set_floor_state"]("k", 1)  # must not raise


def test_set_then_get_campaign_state_round_trips():
    ctx = make_ctx()
    player_id = ctx.world.create_entity()
    ctx.world.add_component(player_id, LuaCampaignStateComponent(data={}))
    ctx.campaign_state_entity = player_id
    api = state_api.build(ctx)

    api["set_campaign_state"]("times_talked_to_npc_1", 1)

    assert api["get_campaign_state"]("times_talked_to_npc_1", 0) == 1


def test_get_campaign_state_default_without_entity():
    ctx = make_ctx()
    api = state_api.build(ctx)

    assert api["get_campaign_state"]("k", "fallback") == "fallback"


def test_floor_state_and_campaign_state_are_independent():
    ctx = make_ctx()
    floor_entity = ctx.world.create_entity()
    ctx.world.add_component(floor_entity, LuaFloorStateComponent(data={}))
    ctx.floor_state_entity = floor_entity
    player_id = ctx.world.create_entity()
    ctx.world.add_component(player_id, LuaCampaignStateComponent(data={}))
    ctx.campaign_state_entity = player_id
    api = state_api.build(ctx)

    api["set_floor_state"]("k", "floor_value")
    api["set_campaign_state"]("k", "campaign_value")

    assert api["get_floor_state"]("k", None) == "floor_value"
    assert api["get_campaign_state"]("k", None) == "campaign_value"
