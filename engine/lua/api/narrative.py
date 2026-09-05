"""``engine.*`` narrative logging group (component doc §2.2). A thin wrapper
over the same ``message`` event any Python system uses (CONTRACTS.md §3.2)
— no second logging path.
"""

from __future__ import annotations

from typing import Callable

from engine.lua.lua_host import ApiContext


def build(ctx: ApiContext) -> dict[str, Callable]:
    def log_message(text: str, category: str = "info") -> None:
        ctx.event_bus.emit("message", {"text": text, "category": category})

    return {"log_message": log_message}
