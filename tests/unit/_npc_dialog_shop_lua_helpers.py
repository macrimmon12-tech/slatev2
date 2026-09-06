"""Shared test helpers for this component's Lua-driving unit tests
(``test_dialog_walker_lua.py``, ``test_shop_lua.py``) and its integration
test. Not itself a test module.

Test-setup choice (component doc §9's Test Plan: "can be tested by driving
dialog_walker.lua's exposed SLATE.DialogWalker table directly through a
lightweight Lua test harness (or through 10's LuaHost with a fixture
World), whichever this component's test setup finds more ergonomic;
document the choice."): this uses a real, booted ``LuaHost`` against a
real fixture ``World``/``DataRegistry``, loading *only* this component's
three scripts (copied into an isolated tmp scripts dir, ``chdir``'d into
per ``tests/integration/test_lua_pipeline.py``'s own established
precedent) rather than the whole ``scripts/`` tree --
``scripts/examples/*.lua`` both subscribe unconditionally to
``entity_interacted`` and would otherwise fire alongside these tests' own
assertions for no reason.
"""

from __future__ import annotations

import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_NAMES = ("dialog_walker.lua", "npc_interaction.lua", "shop.lua")


class FixtureRegistry:
    """Minimal ``DataRegistry``-shaped stand-in: ``{namespace: {id: dict}}``
    plumbed straight through, no disk I/O, no priority/override semantics
    -- this component's Lua only ever calls ``get``, never ``all``."""

    def __init__(self, data: dict[str, dict[str, dict]] | None = None) -> None:
        self._data: dict[str, dict[str, dict]] = data or {}

    def get(self, namespace: str, entry_id: str) -> dict | None:
        return self._data.get(namespace, {}).get(entry_id)

    def all(self, namespace: str) -> dict[str, dict]:
        return dict(self._data.get(namespace, {}))

    def put(self, namespace: str, entry_id: str, entry: dict) -> None:
        self._data.setdefault(namespace, {})[entry_id] = entry


def copy_scripts_to(tmp_path: Path) -> Path:
    """Copies this component's three real ``scripts/*.lua`` files into
    ``tmp_path/scripts`` and returns that directory. Callers still need to
    ``monkeypatch.chdir(tmp_path)`` before ``LuaHost.boot()`` (which
    defaults to loading ``Path("scripts")`` relative to the process cwd) --
    matching ``tests/integration/test_lua_pipeline.py``'s own established
    pattern for isolating script loading in a test.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for name in SCRIPT_NAMES:
        shutil.copy(REPO_ROOT / "scripts" / name, scripts_dir / name)
    return scripts_dir
