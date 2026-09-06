"""Lua <-> Python value conversion helpers.

``lupa`` hands Python code raw ``_LuaTable`` proxy objects for anything a
Lua script constructs as a table literal (both array-style and dict-style
tables use the same proxy) rather than converting them to plain
``dict``/``list`` automatically. Every ``engine.*`` wrapper that accepts or
returns a compound value goes through the two functions here so there is
exactly one place that decides "array-like Lua table -> Python list" vs.
"dict-like Lua table -> Python dict", rather than each API module
reinventing that check.
"""

from __future__ import annotations

from typing import Any

# Must match the Lua version LuaHost actually constructs runtimes with
# (see lua_host.py's import comment) -- lua_type() is version-specific,
# not a generic cross-version helper.
import lupa.lua51 as lua


def lua_to_python(value: Any) -> Any:
    """Recursively convert a Lua table (array- or dict-shaped) into a
    plain Python ``list``/``dict``. Anything that isn't a Lua table
    (numbers, strings, booleans, ``None``, already-Python values) passes
    through unchanged.

    A table whose keys are exactly ``1..N`` (a Lua array/sequence) becomes
    a Python ``list`` in order; any other table becomes a ``dict`` keyed
    by its (string or number) keys, converted to ``str`` for use as JSON-
    safe / Python dict keys.
    """
    if lua.lua_type(value) != "table":
        return value

    keys = list(value.keys())
    if keys and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
        return [lua_to_python(value[k]) for k in sorted(keys)]

    return {str(k): lua_to_python(v) for k, v in value.items()}


def python_to_lua(lua_runtime: Any, value: Any) -> Any:
    """Recursively convert a Python ``dict``/``list``/``tuple`` into a Lua
    table via ``lua_runtime.table_from`` so a script can index it with
    ``.field`` / ``[index]`` syntax. Anything else passes through
    unchanged (numbers, strings, booleans, ``None`` -> Lua ``nil``).

    ``lua_runtime`` is required (not optional) — a caller with no live
    runtime yet has no business trying to hand a table to Lua.
    """
    if isinstance(value, dict):
        return lua_runtime.table_from({k: python_to_lua(lua_runtime, v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return lua_runtime.table_from([python_to_lua(lua_runtime, v) for v in value])
    return value
