"""``engine.*`` inventory/rewards group (component doc §2.2), documented
**(soft, 04 for the first three, 05 for ``grant_xp``)**. Neither ``04``
(inventory-items-loot) nor a public ``ProgressionSystem.grant_xp`` entry
point exists as of this component's implementation — every function here
is a logged-once no-op with a safe default return value until a caller
injects real callables via ``ApiContext.inventory_fns``/``grant_xp_fn``
(component doc §9: "update the corresponding engine/lua/api/*.py module's
import ... when 04/05 actually merge").
"""

from __future__ import annotations

from typing import Callable

from engine.lua.lua_host import ApiContext, warn_once


def build(ctx: ApiContext) -> dict[str, Callable]:
    def _inventory_fn(name: str) -> Callable | None:
        if ctx.inventory_fns is None:
            return None
        return ctx.inventory_fns.get(name)

    def grant_item(entity_id: int, item_id: str, quantity: float) -> str | None:
        fn = _inventory_fn("grant_item")
        if fn is None:
            warn_once(
                "lua_inventory_grant_item_not_wired",
                "engine.grant_item: no-op — 04's inventory system is not "
                "merged/wired yet (documented soft dependency).",
            )
            return None
        return fn(entity_id, item_id, quantity, ctx.world, ctx.event_bus)

    def remove_item(entity_id: int, item_id_or_instance: str, quantity: float) -> bool:
        fn = _inventory_fn("remove_item")
        if fn is None:
            warn_once(
                "lua_inventory_remove_item_not_wired",
                "engine.remove_item: no-op — 04's inventory system is not "
                "merged/wired yet (documented soft dependency).",
            )
            return False
        return bool(fn(entity_id, item_id_or_instance, quantity, ctx.world, ctx.event_bus))

    def has_item(entity_id: int, item_id: str, quantity: float) -> bool:
        fn = _inventory_fn("has_item")
        if fn is None:
            warn_once(
                "lua_inventory_has_item_not_wired",
                "engine.has_item: no-op — 04's inventory system is not "
                "merged/wired yet (documented soft dependency).",
            )
            return False
        return bool(fn(entity_id, item_id, quantity, ctx.world))

    def grant_xp(entity_id: int, amount: float) -> None:
        if ctx.grant_xp_fn is None:
            warn_once(
                "lua_inventory_grant_xp_not_wired",
                "engine.grant_xp: no-op — 05's ProgressionSystem exposes no "
                "public grant-arbitrary-XP entry point yet (documented soft "
                "dependency).",
            )
            return
        ctx.grant_xp_fn(entity_id, amount)

    return {
        "grant_item": grant_item,
        "remove_item": remove_item,
        "has_item": has_item,
        "grant_xp": grant_xp,
    }
