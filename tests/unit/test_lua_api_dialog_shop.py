"""``engine.lua.api.dialog_shop`` unit tests (component doc §7/§8)."""

from __future__ import annotations

from engine.lua.api import dialog_shop as dialog_shop_api

from tests.unit._lua_api_helpers import make_ctx


class _FakeRegistry:
    def __init__(self, ui_skin: dict | None) -> None:
        self._ui_skin = ui_skin

    def get(self, namespace: str, key: str):
        if namespace == "configs" and key == "ui_skin":
            return self._ui_skin
        return None


def test_open_dialog_panel_no_registry_is_a_safe_no_op():
    ctx = make_ctx(registry=None)
    api = dialog_shop_api.build(ctx)

    api["open_dialog_panel"]({"npc": "villager"})  # must not raise


def test_open_dialog_panel_missing_screen_is_a_safe_no_op():
    ctx = make_ctx(registry=_FakeRegistry({"screens": {}}))
    api = dialog_shop_api.build(ctx)
    events = []
    ctx.event_bus.subscribe("show_panel", lambda payload: events.append(payload))

    api["open_dialog_panel"]({"npc": "villager"})

    assert events == []


def test_open_dialog_panel_emits_show_panel_with_registered_screen():
    tree = {"type": "Panel", "children": []}
    ctx = make_ctx(registry=_FakeRegistry({"screens": {"dialog": tree}}))
    api = dialog_shop_api.build(ctx)
    events = []
    ctx.event_bus.subscribe("show_panel", lambda payload: events.append(payload))

    api["open_dialog_panel"]({"npc": "villager"})

    assert events == [{"panel_id": "dialog", "tree": tree, "data": {"npc": "villager"}}]


def test_open_shop_panel_emits_show_panel_with_registered_screen():
    tree = {"type": "Panel", "children": []}
    ctx = make_ctx(registry=_FakeRegistry({"screens": {"shop": tree}}))
    api = dialog_shop_api.build(ctx)
    events = []
    ctx.event_bus.subscribe("show_panel", lambda payload: events.append(payload))

    api["open_shop_panel"]({"shop_id": "general_store"})

    assert events == [{"panel_id": "shop", "tree": tree, "data": {"shop_id": "general_store"}}]
