"""``engine.*`` entity read/write group (component doc §2.2).

``get_position``/``set_position`` wrap ``World.get_component``/
``add_component`` on ``PositionComponent`` and, on write, ``SpatialHash.move``
— ``SpatialHash`` is authoritative for position per CONTRACTS.md §2.5/§9;
``set_position`` must never update ``PositionComponent`` without also
moving the spatial hash, exactly like every Python system must. This is a
sanctioned "content calling through a defined engine boundary" per
component doc §1, not a system-to-system call under CONTRACTS.md rule 4.
"""

from __future__ import annotations

from typing import Any, Callable

from engine.core import save
from engine.lua.convert import python_to_lua
from engine.lua.lua_host import ApiContext, LuaApiError, warn_once
from engine.systems.ai import PositionComponent
from engine.systems.stats import StatsComponent, StatsSystem

#: Combat-derived stats ``set_stat`` must never touch — those always route
#: through ``engine.deal_damage``/``engine.apply_effect`` so they hit the
#: same resolution pipeline (01's EffectResolver) as everything else
#: (component doc §2.2). Shared, additive-only: 01/05 are expected to
#: extend this list (via a PR touching this file) if they add new
#: combat/attribute stat names not already listed here.
COMBAT_DERIVED_STAT_NAMES = frozenset({
    "hp", "max_hp", "mp", "max_mp",
    "damage_min", "damage_max", "damage",
    "armor", "strength", "dexterity", "intelligence", "constitution",
    "vision_range", "evasion", "accuracy", "crit_chance", "crit_multiplier",
})


def build(ctx: ApiContext) -> dict[str, Callable]:
    def get_position(entity_id: int) -> Any:
        pos = ctx.world.get_component(entity_id, PositionComponent)
        if pos is None:
            return None
        return python_to_lua(ctx.lua_runtime, {"x": pos.x, "y": pos.y})

    def set_position(entity_id: int, x: int, y: int) -> None:
        x, y = int(x), int(y)
        pos = ctx.world.get_component(entity_id, PositionComponent)
        old = (pos.x, pos.y) if pos is not None else None
        if pos is None:
            ctx.world.add_component(entity_id, PositionComponent(x, y))
        else:
            pos.x, pos.y = x, y
        if ctx.spatial_hash is not None:
            if old is None:
                ctx.spatial_hash.insert(entity_id, (x, y))
            else:
                ctx.spatial_hash.move(entity_id, old, (x, y))

    def get_component(entity_id: int, component_name: str) -> Any:
        comp_cls = save._COMPONENT_REGISTRY.get(component_name)
        if comp_cls is None:
            warn_once(
                f"lua_entity_unknown_component_{component_name}",
                "engine.get_component: %r is not a registered component name",
                component_name,
            )
            return None
        instance = ctx.world.get_component(entity_id, comp_cls)
        if instance is None:
            return None
        # Read-only copy (dataclasses.asdict via @component's to_dict) —
        # mutating the returned table has no effect back on the entity;
        # there is deliberately no generic set_component (component doc
        # §2.2).
        return python_to_lua(ctx.lua_runtime, instance.to_dict())

    def has_component(entity_id: int, component_name: str) -> bool:
        comp_cls = save._COMPONENT_REGISTRY.get(component_name)
        if comp_cls is None:
            return False
        return ctx.world.get_component(entity_id, comp_cls) is not None

    def get_stat(entity_id: int, stat_name: str) -> float:
        stats_system = StatsSystem(ctx.world, ctx.event_bus)
        return stats_system.get_stat(entity_id, stat_name, ctx.world)

    def set_stat(entity_id: int, stat_name: str, value: float) -> None:
        if stat_name.lower() in COMBAT_DERIVED_STAT_NAMES:
            raise LuaApiError(
                f"set_stat rejected for {stat_name!r}: combat-derived stats "
                "must go through engine.deal_damage or engine.apply_effect."
            )
        stats = ctx.world.get_component(entity_id, StatsComponent)
        if stats is None:
            raise LuaApiError(f"entity {entity_id} has no StatsComponent")
        stats.base[stat_name] = float(value)

    return {
        "get_position": get_position,
        "set_position": set_position,
        "get_component": get_component,
        "has_component": has_component,
        "get_stat": get_stat,
        "set_stat": set_stat,
    }
