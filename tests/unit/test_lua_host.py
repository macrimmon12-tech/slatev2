"""``LuaHost`` unit tests (component doc §7/§8): boot/engine-table wiring,
``run_protected`` error containment, script-directory loading, and
``set_context``/``clear_floor_subscriptions`` (the floor/campaign state
mechanism component §2.4 describes).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.lua import lua_host as lua_host_module
from engine.lua.lua_host import (
    LuaApiError,
    LuaCampaignStateComponent,
    LuaFloorStateComponent,
    LuaHost,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "lua"


@pytest.fixture
def host(tmp_path, monkeypatch) -> LuaHost:
    monkeypatch.chdir(tmp_path)
    lua_host = LuaHost(World(), EventBus(), None)
    lua_host.boot()
    return lua_host


def test_boot_assigns_exactly_one_new_global(host):
    assert host.lua_runtime.eval("engine ~= nil") is True
    assert host.lua_runtime.eval("type(engine)") == "table"
    # Python callables come through as callable userdata (lupa's wrapping),
    # not Lua's native "function" type -- what matters is that they're
    # non-nil and actually callable from Lua, exercised below.
    assert host.lua_runtime.eval("engine.subscribe ~= nil") is True
    assert host.lua_runtime.eval("engine.deal_damage ~= nil") is True
    calls = []
    host.event_bus.subscribe("boot_smoke_test_event", lambda payload: calls.append(payload))
    host.lua_runtime.execute("engine.emit('boot_smoke_test_event', {ok = true})")
    assert len(calls) == 1
    assert calls[0]["ok"] is True


def test_run_protected_catches_lua_error_and_returns_none(host):
    broken = host.lua_runtime.eval("function() error('boom') end")
    assert host.run_protected(broken) is None


def test_run_protected_catches_lua_api_error_and_returns_none(host):
    def raises_api_error():
        raise LuaApiError("rejected")

    assert host.run_protected(raises_api_error) is None


def test_run_protected_returns_value_on_success(host):
    fn = host.lua_runtime.eval("function(x) return x + 1 end")
    assert host.run_protected(fn, 41) == 42


def test_broken_handler_does_not_prevent_other_subscribers(host):
    calls = []
    host.event_bus.subscribe("test_broken_handler_event", lambda payload: calls.append("python"))

    source = (FIXTURES_DIR / "throwing_handler.lua").read_text(encoding="utf-8")
    host.lua_runtime.execute(source)

    host.event_bus.emit("test_broken_handler_event", {})

    # The Lua handler threw and was caught; the second (Python) subscriber
    # still ran.
    assert calls == ["python"]


def test_load_scripts_dir_skips_broken_script_loads_others(tmp_path, host):
    scripts_dir = tmp_path / "scripts_under_test"
    scripts_dir.mkdir()
    (scripts_dir / "a_broken.lua").write_text((FIXTURES_DIR / "broken_syntax.lua").read_text(encoding="utf-8"))
    (scripts_dir / "b_good.lua").write_text((FIXTURES_DIR / "good_script.lua").read_text(encoding="utf-8"))

    results = host.load_scripts_dir(scripts_dir)

    assert results == {"a_broken.lua": False, "b_good.lua": True}

    calls = []
    host.event_bus.subscribe("test_good_script_loaded_event", lambda payload: calls.append(True))
    host.event_bus.emit("test_good_script_loaded_event", {})
    assert calls == [True]


def test_load_scripts_dir_is_alphabetical_and_deterministic(tmp_path, host):
    scripts_dir = tmp_path / "scripts_under_test"
    scripts_dir.mkdir()
    for name in ("zzz.lua", "aaa.lua", "mmm.lua"):
        (scripts_dir / name).write_text("-- no-op\n")

    results = host.load_scripts_dir(scripts_dir)

    assert list(results.keys()) == ["aaa.lua", "mmm.lua", "zzz.lua"]
    assert all(results.values())


def test_load_scripts_dir_missing_directory_is_not_an_error(tmp_path, host):
    results = host.load_scripts_dir(tmp_path / "does_not_exist")
    assert results == {}


def test_load_scripts_dir_finds_examples_subdirectory(tmp_path, host):
    scripts_dir = tmp_path / "scripts_under_test"
    (scripts_dir / "examples").mkdir(parents=True)
    (scripts_dir / "examples" / "demo.lua").write_text("-- no-op\n")

    results = host.load_scripts_dir(scripts_dir)

    assert results == {str(Path("examples") / "demo.lua"): True}


def test_set_context_creates_floor_state_entity_on_first_visit(host):
    assert host.ctx.floor_state_entity is None
    host.set_context(None, None)
    assert host.ctx.floor_state_entity is not None
    comp = host.world.get_component(host.ctx.floor_state_entity, LuaFloorStateComponent)
    assert comp is not None
    assert comp.data == {}


def test_set_context_rebinds_across_two_floors_and_back(host):
    world = host.world

    # Floor A: create its state entity, write a value.
    host.set_context(None, None)
    floor_a_entity = host.ctx.floor_state_entity
    world.get_component(floor_a_entity, LuaFloorStateComponent).data["k"] = "floor_a_value"

    # Simulate leaving floor A (its entities, including the state
    # singleton, get torn down by a real FloorManager snapshot/restore
    # cycle -- here we just destroy it directly to simulate "gone from
    # this world" the way a real floor transition would after snapshotting).
    world.destroy_entity(floor_a_entity)

    # Floor B: no existing LuaFloorStateComponent in the world -> a fresh
    # one is created.
    host.set_context(None, None)
    floor_b_entity = host.ctx.floor_state_entity
    assert floor_b_entity != floor_a_entity
    world.get_component(floor_b_entity, LuaFloorStateComponent).data["k"] = "floor_b_value"

    # Simulate restoring floor A's snapshot back into the (now floor-B)
    # world: recreate an entity carrying floor A's original data.
    world.destroy_entity(floor_b_entity)
    restored_a = world.create_entity()
    world.add_component(restored_a, LuaFloorStateComponent(data={"k": "floor_a_value"}))

    host.set_context(None, None)
    assert host.ctx.floor_state_entity == restored_a
    assert world.get_component(restored_a, LuaFloorStateComponent).data["k"] == "floor_a_value"


def test_set_context_uses_floor_entity_hint_when_valid(host):
    entity_id = host.world.create_entity()
    host.world.add_component(entity_id, LuaFloorStateComponent(data={"hinted": True}))

    host.set_context(entity_id, None)

    assert host.ctx.floor_state_entity == entity_id


def test_set_context_binds_campaign_state_to_player_entity(host):
    player_id = host.world.create_entity()

    host.set_context(None, player_id)

    assert host.ctx.campaign_state_entity == player_id
    comp = host.world.get_component(player_id, LuaCampaignStateComponent)
    assert comp is not None
    assert comp.data == {}


def test_set_context_no_player_id_clears_campaign_state_entity(host):
    player_id = host.world.create_entity()
    host.set_context(None, player_id)
    assert host.ctx.campaign_state_entity is not None

    host.set_context(None, None)

    assert host.ctx.campaign_state_entity is None


def test_clear_floor_subscriptions_removes_only_tagged_tokens(host):
    floor_calls = []
    persistent_calls = []

    # Use the real engine.subscribe wrapper so tag_floor_scoped bookkeeping
    # is exercised exactly as a script would trigger it.
    engine = host.lua_runtime.globals().engine
    subscribe = engine.subscribe

    floor_scoped_handler = host.lua_runtime.eval(
        "function(payload) end"
    )
    persistent_handler = host.lua_runtime.eval(
        "function(payload) end"
    )

    floor_opts = host.lua_runtime.table_from({"tag_floor_scoped": True})
    token_floor = subscribe("floor_scoped_test_event", floor_scoped_handler, floor_opts)
    token_persistent = subscribe("persistent_test_event", persistent_handler)

    assert token_floor in host.ctx.floor_scoped_tokens
    assert token_persistent not in host.ctx.floor_scoped_tokens

    host.clear_floor_subscriptions()

    assert host.ctx.floor_scoped_tokens == set()
    # The floor-scoped subscription is gone -- emitting no longer reaches
    # it (no observable crash either way, but the bus's internal handler
    # list should have shrunk back to nothing for this event type).
    assert host.event_bus._handlers.get("floor_scoped_test_event", []) == []
    assert len(host.event_bus._handlers.get("persistent_test_event", [])) == 1


def test_run_script_no_active_host_is_a_safe_no_op():
    lua_host_module.set_active_host(None)
    lua_host_module.run_script("whatever", 1, 2, World(), EventBus())  # must not raise


def test_run_script_no_registered_handler_is_a_safe_no_op(host):
    lua_host_module.set_active_host(host)
    try:
        lua_host_module.run_script("not_registered", 1, 2, host.world, host.event_bus)  # must not raise
    finally:
        lua_host_module.set_active_host(None)


def test_run_script_calls_registered_handler(host):
    engine = host.lua_runtime.globals().engine
    calls = []
    handler = host.lua_runtime.eval(
        "function(source_id, target_id) return {source_id, target_id} end"
    )
    engine.register_script_handler("npc_interaction", handler)

    lua_host_module.set_active_host(host)
    try:
        lua_host_module.run_script("npc_interaction", 7, 9, host.world, host.event_bus)
    finally:
        lua_host_module.set_active_host(None)

    # No exception is the main assertion (the handler ran through
    # run_protected); also confirm the registration is exactly what
    # run_script looked up.
    assert host.ctx.script_handlers["npc_interaction"] is handler


def test_floor_changed_event_triggers_clear_and_rebind(host):
    # Subscribe a floor-scoped handler via the real engine table.
    engine = host.lua_runtime.globals().engine
    handler = host.lua_runtime.eval("function(payload) end")
    opts = host.lua_runtime.table_from({"tag_floor_scoped": True})
    token = engine.subscribe("some_floor_scoped_event", handler, opts)
    assert token in host.ctx.floor_scoped_tokens

    host.event_bus.emit("floor_changed", {"from_depth": 1, "to_depth": 2})

    assert host.ctx.floor_scoped_tokens == set()
    assert host.ctx.floor_state_entity is not None
