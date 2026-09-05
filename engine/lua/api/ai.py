"""``engine.*`` AI-control group (component doc §2.2), documented **(soft,
02)** — the doc's guessed entry-point names (``override_behavior``,
``get_ai_state``, ``set_enabled``) were this component's best guess ahead
of ``02-ai-system.md`` merging. Now that ``02`` has merged, its real
``AISystem`` exposes none of those three as a public method (only
``get_awake_monster_ids``/``take_turn``, both driven by the turn loop, not
by scripted overrides) — genuinely absent, not a naming typo to patch
around. Per CONTRACTS.md §8 / component doc §9, ``override_ai_behavior``
and ``set_ai_enabled`` stay documented no-ops (logged once) until ``02``
grows a real override entry point; ``get_ai_behavior`` *is* wired for
real, since ``AIComponent``'s ``behavior``/``state`` fields are plain,
safely-readable data via ``World.get_component`` — no override API needed
just to read them.
"""

from __future__ import annotations

from typing import Any, Callable

from engine.lua.convert import python_to_lua
from engine.lua.lua_host import ApiContext, warn_once
from engine.systems.ai import AIComponent


def build(ctx: ApiContext) -> dict[str, Callable]:
    def get_ai_behavior(entity_id: int) -> Any:
        ai = ctx.world.get_component(entity_id, AIComponent)
        if ai is None:
            return None
        return python_to_lua(
            ctx.lua_runtime,
            {
                "behavior": ai.behavior,
                "state": ai.state,
                "params": {
                    "aggro_range": ai.aggro_range,
                    "wake_radius": ai.wake_radius,
                    "flee_hp_threshold": ai.flee_hp_threshold,
                },
            },
        )

    def override_ai_behavior(entity_id: int, behavior: str, params: Any = None) -> None:
        warn_once(
            "lua_ai_override_behavior_not_wired",
            "engine.override_ai_behavior: no-op — 02's AISystem exposes no "
            "override entry point yet (documented soft dependency, "
            "CONTRACTS.md §8).",
        )

    def set_ai_enabled(entity_id: int, enabled: bool) -> None:
        warn_once(
            "lua_ai_set_enabled_not_wired",
            "engine.set_ai_enabled: no-op — 02's AISystem exposes no "
            "enable/disable entry point yet (documented soft dependency, "
            "CONTRACTS.md §8).",
        )

    return {
        "get_ai_behavior": get_ai_behavior,
        "override_ai_behavior": override_ai_behavior,
        "set_ai_enabled": set_ai_enabled,
    }
