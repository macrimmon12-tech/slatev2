"""``engine.*`` event-subscription group (component doc §2.2). Lua scripts
subscribe to **the exact same event bus** Python systems do — no
second-class event source. Every Lua-registered handler is wrapped so the
actual handler registered with ``EventBus`` is
``lambda payload: lua_host.run_protected(lua_callback, payload)`` — this is
what guarantees a throwing Lua handler can't take down event dispatch for
every other subscriber.
"""

from __future__ import annotations

from typing import Any, Callable

from engine.lua.convert import lua_to_python, python_to_lua
from engine.lua.lua_host import ApiContext


def build(ctx: ApiContext) -> dict[str, Callable]:
    def subscribe(event_type: str, lua_callback: Any, opts: Any = None) -> int:
        opts = lua_to_python(opts) or {}

        def handler(payload: dict | None) -> None:
            lua_payload = python_to_lua(ctx.lua_runtime, payload) if payload is not None else None
            ctx.run_protected(lua_callback, lua_payload)

        token = ctx.event_bus.subscribe(event_type, handler)
        if opts.get("tag_floor_scoped"):
            ctx.floor_scoped_tokens.add(token)
        return token

    def unsubscribe(token: int) -> None:
        token = int(token)
        ctx.event_bus.unsubscribe(token)
        ctx.floor_scoped_tokens.discard(token)

    def emit(event_type: str, payload: Any = None) -> None:
        ctx.event_bus.emit(event_type, lua_to_python(payload))

    def clear_floor_subscriptions() -> None:
        ctx.clear_floor_subscriptions()

    def register_script_handler(script_ref: str, lua_callback: Any) -> None:
        # Backs 01's "script" effect type call boundary (see
        # engine/lua/lua_host.py's run_script docstring) -- a loaded
        # script opts in to being invoked ad hoc by name, distinct from
        # (and in addition to) the ordinary engine.subscribe self-
        # registration every script also uses.
        ctx.script_handlers[script_ref] = lua_callback

    return {
        "subscribe": subscribe,
        "unsubscribe": unsubscribe,
        "emit": emit,
        "clear_floor_subscriptions": clear_floor_subscriptions,
        "register_script_handler": register_script_handler,
    }
