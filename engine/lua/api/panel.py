"""``engine.*`` panel-control group (component doc §2.2). ``tree`` is the
exact same widget-tree JSON format ``08-ui-runtime.md`` defines — this
module only passes it through unmodified, it does not redefine that
schema (component doc §1).

Implementation is event-driven, per the doc's own explicit choice, to
avoid a new direct Lua -> 08 call boundary: ``create_panel``/
``update_panel`` reuse the existing ``show_panel`` event (``tree: None``
= update-in-place); ``destroy_panel`` emits a **new** event,
``panel_closed``, flagged for addition to CONTRACTS.md §3.2 in this
component's PR (component doc §2.2/§4).

Gap observed against ``08``'s actual merged ``UIRuntime._on_show_panel``:
it currently only re-binds data for an *already-live* panel or looks one
up by id in ``ui_skin.json``'s pre-registered ``screens`` — it does not
yet build/replace a panel from an ad-hoc ``tree`` carried on the
``show_panel`` payload the way this doc's §2.2 describes. Flagged in this
component's PR for ``08`` to reconcile (not this component's file to
edit) rather than silently working around it with a second, undocumented
call path.
"""

from __future__ import annotations

from typing import Any, Callable

from engine.lua.convert import lua_to_python
from engine.lua.lua_host import ApiContext


def build(ctx: ApiContext) -> dict[str, Callable]:
    def create_panel(panel_id: str, tree: Any, data: Any = None) -> None:
        ctx.event_bus.emit(
            "show_panel",
            {"panel_id": panel_id, "tree": lua_to_python(tree), "data": lua_to_python(data) or {}},
        )

    def update_panel(panel_id: str, data: Any = None) -> None:
        ctx.event_bus.emit(
            "show_panel",
            {"panel_id": panel_id, "tree": None, "data": lua_to_python(data) or {}},
        )

    def destroy_panel(panel_id: str) -> None:
        ctx.event_bus.emit("panel_closed", {"panel_id": panel_id})

    return {
        "create_panel": create_panel,
        "update_panel": update_panel,
        "destroy_panel": destroy_panel,
    }
