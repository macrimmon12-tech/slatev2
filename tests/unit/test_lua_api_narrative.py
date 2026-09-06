"""``engine.lua.api.narrative`` unit tests (component doc §7/§8)."""

from __future__ import annotations

from engine.lua.api import narrative as narrative_api

from tests.unit._lua_api_helpers import make_ctx


def test_log_message_emits_message_event():
    ctx = make_ctx()
    api = narrative_api.build(ctx)
    events = []
    ctx.event_bus.subscribe("message", lambda payload: events.append(payload))

    api["log_message"]("Hello, adventurer.", "flavor")

    assert events == [{"text": "Hello, adventurer.", "category": "flavor"}]


def test_log_message_defaults_category_to_info():
    ctx = make_ctx()
    api = narrative_api.build(ctx)
    events = []
    ctx.event_bus.subscribe("message", lambda payload: events.append(payload))

    api["log_message"]("Hi.")

    assert events == [{"text": "Hi.", "category": "info"}]
