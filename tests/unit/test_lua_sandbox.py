"""Sandbox construction (component doc §2.1/§7): nulled globals are
actually ``nil`` (not merely unused), kept globals survive and work, and a
malicious script touching a nulled global fails cleanly with no side
effect.
"""

from pathlib import Path

import lupa.lua51 as lua
import pytest

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.lua.lua_host import NULLED_GLOBALS, REQUIRED_GLOBALS, LuaHost

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "lua"


@pytest.fixture
def host(tmp_path, monkeypatch) -> LuaHost:
    # boot() loads Path("scripts") with no override point (matches the
    # documented zero-arg signature) -- chdir into an empty tmp dir so
    # these tests are isolated from the repo's real scripts/ directory.
    monkeypatch.chdir(tmp_path)
    lua_host = LuaHost(World(), EventBus(), None)
    lua_host.boot()
    return lua_host


@pytest.mark.parametrize("name", NULLED_GLOBALS)
def test_nulled_globals_are_nil(host, name):
    assert host.lua_runtime.eval(f"{name} == nil") is True


@pytest.mark.parametrize("name", REQUIRED_GLOBALS)
def test_required_globals_survive(host, name):
    assert host.lua_runtime.eval(f"{name} ~= nil") is True


def test_required_globals_are_functional(host):
    rt = host.lua_runtime
    assert rt.eval("math.floor(3.7)") == 3
    assert rt.eval("string.upper('abc')") == "ABC"
    assert rt.eval("#({1, 2, 3})") == 3
    assert rt.eval("(function() local ok = pcall(function() error('x') end); return ok end)()") is False
    assert rt.eval("select('#', 1, 2, 3)") == 3
    assert rt.eval("tostring(42)") == "42"
    assert rt.eval("tonumber('42')") == 42
    assert rt.eval("type({})") == "table"
    rt.execute("local total = 0; for _, v in ipairs({1, 2, 3}) do total = total + v end")
    rt.execute("local total = 0; for k, v in pairs({a = 1, b = 2}) do total = total + v end")
    assert rt.eval("(unpack({7, 8, 9}))") == 7  # parens truncate a multi-return to one value


@pytest.mark.parametrize(
    "fixture_name",
    [
        "malicious_os.lua",
        "malicious_io.lua",
        "malicious_package.lua",
        "malicious_require.lua",
        "malicious_load.lua",
        "malicious_loadfile.lua",
        "malicious_dofile.lua",
        "malicious_debug.lua",
        "malicious_rawget.lua",
        "malicious_rawset.lua",
    ],
)
def test_malicious_script_fixture_fails_cleanly(host, fixture_name):
    source = (FIXTURES_DIR / fixture_name).read_text(encoding="utf-8")

    with pytest.raises(lua.LuaError):
        host.lua_runtime.execute(source)

    # The host process is alive and the runtime still works afterward --
    # the whole point of the sandbox is that a nulled-global access is a
    # normal Lua error, not a crash, and no filesystem/process side effect
    # occurred (the error fires before any dangerous call could execute).
    assert host.lua_runtime.eval("1 + 1") == 2


@pytest.mark.parametrize(
    "fixture_name",
    [
        "malicious_os.lua",
        "malicious_io.lua",
        "malicious_package.lua",
        "malicious_require.lua",
        "malicious_load.lua",
        "malicious_loadfile.lua",
        "malicious_dofile.lua",
        "malicious_debug.lua",
        "malicious_rawget.lua",
        "malicious_rawset.lua",
    ],
)
def test_malicious_script_fixture_caught_via_run_protected(host, fixture_name):
    """Same fixtures, driven through run_protected (the real call path
    load_scripts_dir uses) -- asserts the failure is swallowed, logged,
    and returns None rather than raising."""
    source = (FIXTURES_DIR / fixture_name).read_text(encoding="utf-8")

    result = host.run_protected(lambda: host.lua_runtime.execute(source))

    assert result is None
    assert host.lua_runtime.eval("1 + 1") == 2
