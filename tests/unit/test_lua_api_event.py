"""``engine.lua.api.event`` unit tests (component doc §7/§8): scripts
subscribe to the exact same ``EventBus`` Python systems do, every
Lua-registered handler is wrapped through ``run_protected``, and
``tag_floor_scoped`` bookkeeping matches what ``LuaHost.clear_floor_subscriptions``
drains.
"""

from __future__ import annotations

from engine.lua.api import event as event_api

from tests.unit._lua_api_helpers import make_ctx


def _make_ctx_with_run_protected():
    calls = []

    def run_protected(callable_, *args):
        calls.append(args)
        return callable_(*args)

    ctx = make_ctx()
    ctx.run_protected = run_protected
    return ctx, calls


def test_subscribe_wraps_handler_through_run_protected():
    ctx, calls = _make_ctx_with_run_protected()
    api = event_api.build(ctx)
    received = []

    def lua_callback(payload):
        received.append(payload)

    api["subscribe"]("my_event", lua_callback)
    ctx.event_bus.emit("my_event", {"x": 1})

    assert len(received) == 1
    assert received[0].x == 1  # converted to a Lua-style table via python_to_lua
    assert calls  # run_protected was the actual call path


def test_subscribe_with_tag_floor_scoped_records_token():
    ctx, _ = _make_ctx_with_run_protected()
    api = event_api.build(ctx)

    token = api["subscribe"]("floor_event", lambda payload: None, {"tag_floor_scoped": True})

    assert token in ctx.floor_scoped_tokens


def test_subscribe_without_tag_floor_scoped_does_not_record_token():
    ctx, _ = _make_ctx_with_run_protected()
    api = event_api.build(ctx)

    token = api["subscribe"]("persistent_event", lambda payload: None)

    assert token not in ctx.floor_scoped_tokens


def test_unsubscribe_removes_handler_and_token():
    ctx, _ = _make_ctx_with_run_protected()
    api = event_api.build(ctx)
    received = []
    token = api["subscribe"]("my_event", lambda payload: received.append(payload), {"tag_floor_scoped": True})

    api["unsubscribe"](token)

    assert token not in ctx.floor_scoped_tokens
    ctx.event_bus.emit("my_event", {})
    assert received == []


def test_emit_dispatches_on_the_real_event_bus():
    ctx, _ = _make_ctx_with_run_protected()
    api = event_api.build(ctx)
    received = []
    ctx.event_bus.subscribe("scripted_event", lambda payload: received.append(payload))

    api["emit"]("scripted_event", {"quest": "started"})

    assert received == [{"quest": "started"}]


def test_clear_floor_subscriptions_delegates_to_ctx_hook():
    ctx, _ = _make_ctx_with_run_protected()
    calls = []
    ctx.clear_floor_subscriptions = lambda: calls.append(True)
    api = event_api.build(ctx)

    api["clear_floor_subscriptions"]()

    assert calls == [True]


def test_register_script_handler_populates_ctx_script_handlers():
    ctx, _ = _make_ctx_with_run_protected()
    api = event_api.build(ctx)

    def handler(source_id, target_id):
        pass

    api["register_script_handler"]("npc_interaction", handler)

    assert ctx.script_handlers["npc_interaction"] is handler
