"""``engine.*`` combat/effects group (component doc §2.2).

Both functions route through ``01``'s ``EffectResolver.apply_effect_list``
— the only path for HP/MP/damage from Lua, by design (see ``entity.py``'s
``set_stat`` blocklist). This is a sanctioned "content calling through a
defined engine boundary" per component doc §1, not a system-to-system call
under CONTRACTS.md rule 4.
"""

from __future__ import annotations

from typing import Any, Callable

from engine.lua.convert import lua_to_python
from engine.lua.lua_host import ApiContext, LuaApiError
from engine.systems.effects import apply_effect_list


def _normalize_effects(effect_or_effect_list: Any) -> list[dict]:
    """Accepts either a single effect table or an array of effect tables
    (a Lua script may write either), normalized to a Python list of dicts."""
    value = lua_to_python(effect_or_effect_list)
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return value
    raise LuaApiError(
        f"apply_effect expected an effect table or array of effect tables, got {value!r}"
    )


def build(ctx: ApiContext) -> dict[str, Callable]:
    def deal_damage(source_id: int, target_id: int, amount: Any, damage_type: str) -> None:
        # `amount` may be a plain Lua number or a formula string
        # ("2d6 + (INT * 0.3)") — both pass straight through to 01's
        # formula evaluation unchanged, no special-casing here.
        effect = {"type": "damage", "amount": amount, "damage_type": damage_type}
        apply_effect_list([effect], source_id, target_id, ctx.world, ctx.event_bus)

    def apply_effect(effect_or_effect_list: Any, source_id: int, target_id: int) -> None:
        effects = _normalize_effects(effect_or_effect_list)
        apply_effect_list(effects, source_id, target_id, ctx.world, ctx.event_bus)

    return {"deal_damage": deal_damage, "apply_effect": apply_effect}
