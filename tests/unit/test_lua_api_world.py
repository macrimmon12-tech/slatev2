"""``engine.lua.api.world`` unit tests (component doc §7/§8)."""

from __future__ import annotations

from engine.core.spatial_hash import SpatialHash
from engine.lua.api import world as world_api

from tests.unit._lua_api_helpers import make_ctx


def test_get_tile_no_op_without_hook():
    ctx = make_ctx()
    api = world_api.build(ctx)

    assert api["get_tile"](1, 2) is None


def test_get_tile_calls_injected_hook():
    ctx = make_ctx(get_tile_fn=lambda x, y: {"walkable": True, "room_id": "r1"} if (x, y) == (2, 3) else None)
    api = world_api.build(ctx)

    tile = api["get_tile"](2, 3)

    assert tile.walkable is True
    assert tile.room_id == "r1"
    assert api["get_tile"](0, 0) is None


def test_query_entities_in_radius_empty_without_spatial_hash():
    ctx = make_ctx()
    api = world_api.build(ctx)

    result = api["query_entities_in_radius"](0, 0, 5)
    assert list(result) == []


def test_query_entities_in_radius_wired():
    spatial_hash = SpatialHash()
    spatial_hash.insert(1, (0, 0))
    spatial_hash.insert(2, (10, 10))
    ctx = make_ctx(spatial_hash=spatial_hash)
    api = world_api.build(ctx)

    result = list(api["query_entities_in_radius"](0, 0, 1).values())

    assert result == [1]


def test_spawn_vfx_emits_vfx_play():
    ctx = make_ctx()
    api = world_api.build(ctx)
    events = []
    ctx.event_bus.subscribe("vfx_play", lambda payload: events.append(payload))

    api["spawn_vfx"]("explosion", 3, 4, {"scale": 2})

    assert events == [{"vfx_id": "explosion", "position": (3, 4), "data": {"scale": 2}}]


def test_play_sound_emits_play_sound_with_position():
    ctx = make_ctx()
    api = world_api.build(ctx)
    events = []
    ctx.event_bus.subscribe("play_sound", lambda payload: events.append(payload))

    api["play_sound"]("clang", 1, 2)

    assert events == [{"sound_id": "clang", "position": (1, 2)}]


def test_play_sound_without_position():
    ctx = make_ctx()
    api = world_api.build(ctx)
    events = []
    ctx.event_bus.subscribe("play_sound", lambda payload: events.append(payload))

    api["play_sound"]("clang")

    assert events == [{"sound_id": "clang", "position": None}]
