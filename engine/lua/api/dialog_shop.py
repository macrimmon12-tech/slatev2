"""``engine.*`` dialog/shop screen-opener group (component doc §2.2). Thin
convenience wrappers only — all real dialog/shop logic lives in ``11``'s
Lua scripts, not here. Each looks up the pre-authored "dialog"/"shop"
screen entry from ``registry.get("configs", "ui_skin")["screens"]`` (owned
by ``08``) and routes through the same ``show_panel`` event
``panel.py``'s ``create_panel`` uses.
"""

from __future__ import annotations

from typing import Any, Callable

from engine.lua.convert import lua_to_python
from engine.lua.lua_host import ApiContext, warn_once


def build(ctx: ApiContext) -> dict[str, Callable]:
    def _open_screen(panel_id: str, data: Any) -> None:
        if ctx.registry is None:
            warn_once(
                f"lua_dialog_shop_no_registry_{panel_id}",
                "engine.open_%s_panel: no DataRegistry wired; no-op",
                panel_id,
            )
            return
        ui_skin = ctx.registry.get("configs", "ui_skin") or {}
        screens = ui_skin.get("screens") or {}
        tree = screens.get(panel_id)
        if tree is None:
            # Absence = zero cost (component doc §2.2) — 11's own PR is
            # expected to add "dialog"/"shop" screen entries to
            # ui_skin.json if 08 hasn't authored generic ones.
            warn_once(
                f"lua_dialog_shop_missing_screen_{panel_id}",
                "engine.open_%s_panel: no %r screen registered in "
                "ui_skin.json; no-op",
                panel_id, panel_id,
            )
            return
        ctx.event_bus.emit(
            "show_panel",
            {"panel_id": panel_id, "tree": tree, "data": lua_to_python(data) or {}},
        )

    def open_dialog_panel(data: Any = None) -> None:
        _open_screen("dialog", data)

    def open_shop_panel(data: Any = None) -> None:
        _open_screen("shop", data)

    return {"open_dialog_panel": open_dialog_panel, "open_shop_panel": open_shop_panel}
