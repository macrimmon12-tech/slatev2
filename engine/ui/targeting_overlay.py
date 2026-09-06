"""``TargetingOverlay`` — the spell-targeting cursor (``docs/components/
08-ui-runtime.md`` §2.4). Explicitly **not** a panel: never goes through
``UIRuntime``'s ``create_panel``/panel stack, has its own ``draw()`` call
invoked directly by the render loop. Recommended draw order (per that
doc): tile viewport -> targeting overlay (world-space cursor) -> HUD
panels (screen-space) — so the overlay draws over the map but under any
modal panel; the caller (``07-input-renderer-audio.md``'s render loop,
once merged) is responsible for calling ``TargetingOverlay.draw()``
between the tile-viewport draw and ``UIRuntime.draw()``.

Coordination note — ``InputHandler`` context switch (doc §2.4): the
documented constructor signature is ``(event_bus, screen_rect_provider)``,
with no reference to ``07-input-renderer-audio.md``'s (not yet merged)
``InputHandler`` at all, yet the doc also says activation "switches
``InputHandler``'s context to ``targeting`` ... coordinate the exact hook
... in your PR against 07's doc." Since there is no ``InputHandler`` to
call into yet, this module adds an *optional* keyword-only
``input_handler`` parameter (``None`` by default) — when absent, the
context switch is a documented no-op (CONTRACTS.md §2 rule 7 / §7 point 2:
"not yet wired, tracked in ``15-integration-verification.md``"); when
``07`` lands and passes a real ``input_handler`` exposing
``set_context(context: str)``, the switch happens for real with no change
needed here. This is the same "optional keyword extension for a
genuinely underspecified cross-component hook" precedent
``02-ai-system.md``/``05-progression-vision.md`` already established.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from engine.core.events import EventBus
from engine.ui.ui_runtime import ScreenRectProvider, get_pygame_module

logger = logging.getLogger(__name__)

#: Targeting modes that resolve instantly / highlight the caster rather
#: than needing a movable tile cursor (doc §2.4, per
#: ``03-spells-status.md``'s five targeting modes).
_NO_CURSOR_MODES = frozenset({"self", "aoe_self"})

_CURSOR_COLOR = (255, 255, 80)


class TargetingOverlay:
    """Activated by ``spell_cast_initiated``, torn down by either
    ``target_confirmed`` or ``cancel_targeting`` (doc §2.4/§3)."""

    def __init__(
        self,
        event_bus: EventBus,
        screen_rect_provider: ScreenRectProvider | None = None,
        *,
        input_handler: Any | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._screen_rect_provider = screen_rect_provider
        self._input_handler = input_handler

        self._active = False
        self._entity_id: int | None = None
        self._spell_id: str | None = None
        self._targeting_mode: str | None = None
        self._cursor_tile: tuple[int, int] | None = None

        self._tokens = [
            event_bus.subscribe("spell_cast_initiated", self._on_spell_cast_initiated),
            event_bus.subscribe("target_confirmed", self._on_teardown),
            event_bus.subscribe("cancel_targeting", self._on_teardown),
        ]

    def is_active(self) -> bool:
        return self._active

    def set_cursor_tile(self, position: tuple[int, int] | None) -> None:
        """Set the world-space tile the cursor should render at.
        ``07-input-renderer-audio.md``'s input handler is the intended
        caller (translating raw mouse/keyboard movement into a tile
        position) — this module has no opinion on how the position is
        chosen, only how it's drawn."""
        self._cursor_tile = position

    # -- event handlers -----------------------------------------------------

    def _on_spell_cast_initiated(self, payload: dict[str, Any] | None) -> None:
        payload = payload or {}
        self._active = True
        self._entity_id = payload.get("entity_id")
        self._spell_id = payload.get("spell_id")
        self._targeting_mode = payload.get("targeting_mode")
        self._cursor_tile = None
        self._set_input_context("targeting")

    def _on_teardown(self, _payload: dict[str, Any] | None) -> None:
        self._active = False
        self._entity_id = None
        self._spell_id = None
        self._targeting_mode = None
        self._cursor_tile = None
        self._set_input_context("game")

    def _set_input_context(self, context: str) -> None:
        if self._input_handler is None:
            # Not yet wired — see module docstring. Absence = zero cost.
            return
        setter = getattr(self._input_handler, "set_context", None)
        if setter is None:
            return
        try:
            setter(context)
        except Exception:
            logger.warning("TargetingOverlay failed to set input context to %r", context, exc_info=True)

    # -- draw -----------------------------------------------------------

    def draw(self, surface: Any) -> None:
        """No-op when inactive, when ``targeting_mode`` needs no visible
        cursor (``self``/``aoe_self`` — doc §2.4), when no cursor position
        has been set yet, or when pygame isn't installed."""
        if not self._active or self._targeting_mode in _NO_CURSOR_MODES:
            return
        if self._cursor_tile is None:
            return
        pygame_module = get_pygame_module()
        if pygame_module is None:
            return
        try:
            self._draw_cursor(pygame_module, surface)
        except Exception:
            logger.warning("TargetingOverlay draw failed", exc_info=True)

    def _draw_cursor(self, pygame_module: Any, surface: Any) -> None:
        tile_x, tile_y = self._cursor_tile  # type: ignore[misc]
        # No tile-size/camera-offset convention exists yet (that's
        # 07-input-renderer-audio.md's tile viewport) — draw a small
        # fixed-size marker at the raw coordinates handed in, which is
        # already screen-space once 07 supplies real coordinates via
        # set_cursor_tile.
        size = 32
        rect = pygame_module.Rect(tile_x, tile_y, size, size)
        pygame_module.draw.rect(surface, _CURSOR_COLOR, rect, width=2)
