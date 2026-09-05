"""``engine.*`` floor-/campaign-scoped persistent state group (component doc
§2.2/§2.4). Reads/writes ``LuaFloorStateComponent``/``LuaCampaignStateComponent``
directly off the entities ``LuaHost.set_context`` tracks — zero special-case
Python handling, zero NPC/dialog/shop-specific save code anywhere.
"""

from __future__ import annotations

from typing import Any, Callable

from engine.lua.convert import lua_to_python, python_to_lua
from engine.lua.lua_host import ApiContext, LuaCampaignStateComponent, LuaFloorStateComponent


def build(ctx: ApiContext) -> dict[str, Callable]:
    def get_floor_state(key: str, default: Any = None) -> Any:
        if ctx.floor_state_entity is None:
            return default
        comp = ctx.world.get_component(ctx.floor_state_entity, LuaFloorStateComponent)
        if comp is None or key not in comp.data:
            return default
        return python_to_lua(ctx.lua_runtime, comp.data[key])

    def set_floor_state(key: str, value: Any) -> None:
        if ctx.floor_state_entity is None:
            return
        comp = ctx.world.get_component(ctx.floor_state_entity, LuaFloorStateComponent)
        if comp is None:
            return
        comp.data[key] = lua_to_python(value)

    def get_campaign_state(key: str, default: Any = None) -> Any:
        if ctx.campaign_state_entity is None:
            return default
        comp = ctx.world.get_component(ctx.campaign_state_entity, LuaCampaignStateComponent)
        if comp is None or key not in comp.data:
            return default
        return python_to_lua(ctx.lua_runtime, comp.data[key])

    def set_campaign_state(key: str, value: Any) -> None:
        if ctx.campaign_state_entity is None:
            return
        comp = ctx.world.get_component(ctx.campaign_state_entity, LuaCampaignStateComponent)
        if comp is None:
            return
        comp.data[key] = lua_to_python(value)

    return {
        "get_floor_state": get_floor_state,
        "set_floor_state": set_floor_state,
        "get_campaign_state": get_campaign_state,
        "set_campaign_state": set_campaign_state,
    }
