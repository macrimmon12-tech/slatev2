"""``engine.*`` entity lifecycle group (component doc §2.2).

``spawn`` mirrors how ``06``'s worldgen spawns monsters
(``engine.systems.worldgen._spawn_one_monster``): create the entity, attach
``PositionComponent``/``StatsComponent`` (and ``AIComponent`` if the entity
def carries an ``ai`` block), register it with the spatial hash if one is
wired, and emit ``entity_spawned`` per CONTRACTS.md §3.2. This is a
simplified mirror, not a byte-for-byte port of 06's private helper (content-
schema quirks like the "dexterity"/"dex" alias stay 06's problem to solve
in its own schema, not duplicated here) — flagged in this component's PR.
``destroy`` wraps ``World.destroy_entity`` + ``SpatialHash.remove``.
"""

from __future__ import annotations

from typing import Callable

from engine.lua.lua_host import ApiContext, warn_once
from engine.systems.ai import PositionComponent, create_ai_component
from engine.systems.stats import StatsComponent


def _build_stats_base(entity_def: dict) -> dict[str, float]:
    stats_block = dict(entity_def.get("stats", {}))
    combat_block = entity_def.get("combat", {})
    base: dict[str, float] = dict(stats_block)
    base.setdefault("damage_min", combat_block.get("damage_min", 0))
    base.setdefault("damage_max", combat_block.get("damage_max", 0))
    return base


def build(ctx: ApiContext) -> dict[str, Callable]:
    def spawn(entity_def_id: str, x: int, y: int) -> int | None:
        if ctx.registry is None:
            warn_once("lua_lifecycle_no_registry", "engine.spawn: no DataRegistry wired; no-op")
            return None
        entity_def = ctx.registry.get("entities", entity_def_id)
        if entity_def is None:
            warn_once(
                f"lua_lifecycle_unknown_entity_{entity_def_id}",
                "engine.spawn: unknown entities id %r; no-op",
                entity_def_id,
            )
            return None

        x, y = int(x), int(y)
        entity_id = ctx.world.create_entity()
        ctx.world.add_component(entity_id, PositionComponent(x, y))
        ctx.world.add_component(entity_id, StatsComponent(base=_build_stats_base(entity_def), modifiers={}))

        # Only attempt AI-component creation for defs that actually carry
        # an "ai" block — create_ai_component() logs a warning for a
        # missing/invalid behavior, which would be a false "data error"
        # log for every ordinary non-monster spawn (NPCs, decor, etc.)
        # otherwise.
        if "ai" in entity_def:
            ai_component = create_ai_component(entity_def, home_position=(x, y))
            if ai_component is not None:
                ctx.world.add_component(entity_id, ai_component)

        if ctx.spatial_hash is not None:
            ctx.spatial_hash.insert(entity_id, (x, y))

        ctx.event_bus.emit(
            "entity_spawned",
            {"entity_id": entity_id, "kind": entity_def_id, "position": (x, y)},
        )
        return entity_id

    def destroy(entity_id: int) -> None:
        ctx.world.destroy_entity(entity_id)
        if ctx.spatial_hash is not None:
            ctx.spatial_hash.remove(entity_id)

    return {"spawn": spawn, "destroy": destroy}
