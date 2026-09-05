"""``engine.*`` world read/write group (component doc §2.2).

``query_entities_in_radius`` wraps foundation's ``SpatialHash.query_radius``
directly — a hard dependency, but with no shared-global accessor the way
the event bus has ``_default_bus`` (see ``ApiContext.spatial_hash``'s
docstring); the constructing caller injects the live instance, and this
degrades to an empty list (logged once) if none was wired. ``get_tile``
is **(soft, 06)** — worldgen has no per-``(x, y)`` tile accessor or
"current floor for this world" accessor as of this component's
implementation, only ``get_tilemap(floor_id) -> TileMap``; per component
doc §2.2's own documented fallback this stays a no-op returning ``nil``
until a caller injects ``ApiContext.get_tile_fn``. ``spawn_vfx``/
``play_sound`` need no Python system at all — they just emit the existing
CONTRACTS.md §3.2 events.
"""

from __future__ import annotations

from typing import Any, Callable

from engine.lua.convert import lua_to_python, python_to_lua
from engine.lua.lua_host import ApiContext, warn_once


def build(ctx: ApiContext) -> dict[str, Callable]:
    def get_tile(x: int, y: int) -> Any:
        if ctx.get_tile_fn is None:
            warn_once(
                "lua_world_get_tile_not_wired",
                "engine.get_tile: no-op — no tile accessor wired (documented "
                "soft dependency on 06's worldgen, CONTRACTS.md §8).",
            )
            return None
        tile = ctx.get_tile_fn(int(x), int(y))
        if tile is None:
            return None
        return python_to_lua(ctx.lua_runtime, tile)

    def query_entities_in_radius(x: int, y: int, radius: int) -> Any:
        if ctx.spatial_hash is None:
            warn_once(
                "lua_world_no_spatial_hash",
                "engine.query_entities_in_radius: no-op — no SpatialHash "
                "wired into this LuaHost.",
            )
            return python_to_lua(ctx.lua_runtime, [])
        entity_ids = ctx.spatial_hash.query_radius((int(x), int(y)), int(radius))
        return python_to_lua(ctx.lua_runtime, list(entity_ids))

    def spawn_vfx(vfx_id: str, x: int, y: int, data: Any = None) -> None:
        ctx.event_bus.emit(
            "vfx_play",
            {"vfx_id": vfx_id, "position": (int(x), int(y)), "data": lua_to_python(data) or {}},
        )

    def play_sound(sound_id: str, x: Any = None, y: Any = None) -> None:
        position = (int(x), int(y)) if x is not None and y is not None else None
        ctx.event_bus.emit("play_sound", {"sound_id": sound_id, "position": position})

    return {
        "get_tile": get_tile,
        "query_entities_in_radius": query_entities_in_radius,
        "spawn_vfx": spawn_vfx,
        "play_sound": play_sound,
    }
