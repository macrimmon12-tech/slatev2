"""Shared test helpers for the ``engine/lua/api/*.py`` unit tests
(component 10). Not itself a test module — no ``test_*`` functions here,
just a factory other ``test_lua_*.py`` files import.
"""

from __future__ import annotations

import lupa.lua51 as lua

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.lua.lua_host import ApiContext


def make_ctx(world: World | None = None, event_bus: EventBus | None = None, **overrides) -> ApiContext:
    """A real ``lupa.LuaRuntime`` (cheap to construct) backs
    ``lua_runtime`` so ``python_to_lua``'s ``table_from`` calls work
    exactly as they would in production, without needing a full
    ``LuaHost.boot()`` for every API-group unit test."""
    world = world if world is not None else World()
    event_bus = event_bus if event_bus is not None else EventBus()
    lua_runtime = lua.LuaRuntime(unpack_returned_tuples=True)
    ctx = ApiContext(world=world, event_bus=event_bus, registry=None, lua_runtime=lua_runtime)
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx
