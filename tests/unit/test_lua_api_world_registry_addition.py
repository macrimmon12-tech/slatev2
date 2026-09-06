"""Unit tests for ``engine.get_registry_entry``/``engine.eval_formula`` --
the flagged addition ``11-npc-dialog-shop-content.md`` made to
``engine/lua/api/world.py`` (see that module's docstring for why). Kept in
this component's own test file rather than editing 10's
``tests/unit/test_lua_api_world.py``.
"""

from __future__ import annotations

import pytest

from engine.lua.api import world as world_api

from tests.unit._lua_api_helpers import make_ctx


class _FakeRegistry:
    def __init__(self, data: dict[str, dict[str, dict]]) -> None:
        self._data = data

    def get(self, namespace: str, entry_id: str):
        return self._data.get(namespace, {}).get(entry_id)


def test_get_registry_entry_returns_none_without_a_registry():
    ctx = make_ctx(registry=None)
    api = world_api.build(ctx)
    assert api["get_registry_entry"]("dialogs", "x") is None


def test_get_registry_entry_returns_none_for_missing_id():
    ctx = make_ctx(registry=_FakeRegistry({"dialogs": {}}))
    api = world_api.build(ctx)
    assert api["get_registry_entry"]("dialogs", "nope") is None


def test_get_registry_entry_returns_lua_table_for_a_hit():
    ctx = make_ctx(registry=_FakeRegistry({"shops": {"general": {"id": "general", "starting_gold": 10}}}))
    api = world_api.build(ctx)

    entry = api["get_registry_entry"]("shops", "general")

    assert entry["id"] == "general"
    assert entry["starting_gold"] == 10


def test_get_registry_entry_unknown_namespace_is_a_safe_no_op():
    class _RaisingRegistry:
        def get(self, namespace, entry_id):
            raise KeyError(namespace)

    ctx = make_ctx(registry=_RaisingRegistry())
    api = world_api.build(ctx)
    assert api["get_registry_entry"]("not_a_namespace", "x") is None


def test_eval_formula_evaluates_against_context():
    ctx = make_ctx()
    api = world_api.build(ctx)

    result = api["eval_formula"]("base_price * 1.15", {"base_price": 100})

    assert result == pytest.approx(115.0)


def test_eval_formula_with_no_context_defaults_to_empty():
    ctx = make_ctx()
    api = world_api.build(ctx)

    assert api["eval_formula"]("2 + 2") == 4.0
