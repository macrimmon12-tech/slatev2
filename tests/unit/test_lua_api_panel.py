"""``engine.lua.api.panel`` unit tests (component doc §7/§8)."""

from __future__ import annotations

from engine.lua.api import panel as panel_api

from tests.unit._lua_api_helpers import make_ctx


def test_create_panel_emits_show_panel_with_tree():
    ctx = make_ctx()
    api = panel_api.build(ctx)
    events = []
    ctx.event_bus.subscribe("show_panel", lambda payload: events.append(payload))

    api["create_panel"]("inventory", {"type": "Panel"}, {"gold": 10})

    assert events == [{"panel_id": "inventory", "tree": {"type": "Panel"}, "data": {"gold": 10}}]


def test_update_panel_emits_show_panel_with_tree_none():
    ctx = make_ctx()
    api = panel_api.build(ctx)
    events = []
    ctx.event_bus.subscribe("show_panel", lambda payload: events.append(payload))

    api["update_panel"]("inventory", {"gold": 20})

    assert events == [{"panel_id": "inventory", "tree": None, "data": {"gold": 20}}]


def test_destroy_panel_emits_panel_closed():
    ctx = make_ctx()
    api = panel_api.build(ctx)
    events = []
    ctx.event_bus.subscribe("panel_closed", lambda payload: events.append(payload))

    api["destroy_panel"]("inventory")

    assert events == [{"panel_id": "inventory"}]
