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

``get_registry_entry``/``eval_formula`` are a flagged addition from
**11-npc-dialog-shop-content.md**, not part of this component's own
original §2.2 surface. That doc's ``dialog_walker.lua``/``shop.lua`` need
(a) a generic "read one content entry from any registry namespace by id"
primitive — its own text anticipates this exact name
(``engine.get_registry_entry("dialogs", dialog_id)``) but says to use
whatever this component actually exposes and flag a mismatch; nothing
under ``engine/lua/api/`` exposed *any* namespace-agnostic registry read to
Lua as of 11's implementation, only namespace-specific internal calls
(``lifecycle.py``'s ``registry.get("entities", ...)``,
``dialog_shop.py``'s ``registry.get("configs", "ui_skin")``) — and (b) a
way to evaluate a shop's ``price_formula`` strings (CONTRACTS.md §6's
``eval_formula``) without reimplementing arithmetic parsing in Lua (Lua's
own ``load``/``loadstring`` are nulled by this component's sandbox,
component doc §2.1, so a content-authored formula string can't be handed
to Lua's own evaluator). Both route straight through to the same
``DataRegistry``/``engine.core.formula`` every Python system already uses
-- no new content-reading or arithmetic path, just a Lua-reachable wrapper
around an existing one. 11's PR adds these two functions here (the "world
read/write group" already merged and wired into ``lua_host.py``) rather
than adding a whole new API module + editing ``lua_host.py``'s import
list, to keep the footprint to one file -- flagged in that PR for 10 to
reconcile/relocate if a more fitting home is preferred later.
"""

from __future__ import annotations

from typing import Any, Callable

from engine.core.formula import eval_formula as _eval_formula
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

    # -- 11-npc-dialog-shop-content.md's flagged addition (see module
    # docstring) -------------------------------------------------------

    def get_registry_entry(namespace: str, entry_id: str) -> Any:
        if ctx.registry is None:
            warn_once(
                "lua_world_get_registry_entry_no_registry",
                "engine.get_registry_entry: no DataRegistry wired; no-op",
            )
            return None
        try:
            entry = ctx.registry.get(namespace, entry_id)
        except KeyError:
            warn_once(
                f"lua_world_get_registry_entry_unknown_namespace_{namespace}",
                "engine.get_registry_entry: unknown namespace %r",
                namespace,
            )
            return None
        if entry is None:
            return None
        return python_to_lua(ctx.lua_runtime, entry)

    def eval_formula(expr: str, context: Any = None) -> float:
        return _eval_formula(str(expr), lua_to_python(context) or {})

    return {
        "get_tile": get_tile,
        "query_entities_in_radius": query_entities_in_radius,
        "spawn_vfx": spawn_vfx,
        "play_sound": play_sound,
        "get_registry_entry": get_registry_entry,
        "eval_formula": eval_formula,
    }
