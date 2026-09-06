"""``engine.lua.api.inventory`` unit tests (component doc §7/§8/§9): 04
doesn't exist yet, and 05 exposes no public ``grant_xp`` entry point, so
every function is a logged-once no-op by default, wired for real when the
caller injects a callable.
"""

from __future__ import annotations

from engine.lua.api import inventory as inventory_api

from tests.unit._lua_api_helpers import make_ctx


def test_grant_item_no_op_without_hook():
    ctx = make_ctx()
    api = inventory_api.build(ctx)

    assert api["grant_item"](1, "sword", 1) is None


def test_grant_item_calls_injected_hook():
    calls = []

    def fake_grant_item(entity_id, item_id, quantity, world, event_bus):
        calls.append((entity_id, item_id, quantity))
        return "instance-1"

    ctx = make_ctx(inventory_fns={"grant_item": fake_grant_item})
    api = inventory_api.build(ctx)

    result = api["grant_item"](7, "sword", 2)

    assert result == "instance-1"
    assert calls == [(7, "sword", 2)]


def test_remove_item_no_op_without_hook_returns_false():
    ctx = make_ctx()
    api = inventory_api.build(ctx)

    assert api["remove_item"](1, "sword", 1) is False


def test_remove_item_calls_injected_hook():
    ctx = make_ctx(inventory_fns={"remove_item": lambda *a: True})
    api = inventory_api.build(ctx)

    assert api["remove_item"](1, "sword", 1) is True


def test_has_item_no_op_without_hook_returns_false():
    ctx = make_ctx()
    api = inventory_api.build(ctx)

    assert api["has_item"](1, "sword", 1) is False


def test_has_item_calls_injected_hook():
    ctx = make_ctx(inventory_fns={"has_item": lambda *a: True})
    api = inventory_api.build(ctx)

    assert api["has_item"](1, "sword", 1) is True


def test_grant_xp_no_op_without_hook():
    ctx = make_ctx()
    api = inventory_api.build(ctx)

    api["grant_xp"](1, 100)  # must not raise


def test_grant_xp_calls_injected_hook():
    calls = []
    ctx = make_ctx(grant_xp_fn=lambda entity_id, amount: calls.append((entity_id, amount)))
    api = inventory_api.build(ctx)

    api["grant_xp"](3, 50)

    assert calls == [(3, 50)]
