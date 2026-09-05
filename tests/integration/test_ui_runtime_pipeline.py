"""Integration test for engine.ui — CONTRACTS.md §7.3 / docs/components/
08-ui-runtime.md §9: drives the real entry points (a real ``EventBus``,
``UIRuntime`` loading the real ``data/config/ui_skin.json`` tree, and a
real ``TargetingOverlay``) end-to-end rather than mocking any of them.
"""

from __future__ import annotations

import json
from pathlib import Path

from engine.core.events import EventBus
from engine.ui.targeting_overlay import TargetingOverlay
from engine.ui.ui_runtime import UIRuntime

UI_SKIN_PATH = Path(__file__).resolve().parents[2] / "data" / "config" / "ui_skin.json"


def test_main_menu_new_game_button_reaches_real_event_bus_subscriber():
    bus = EventBus()
    runtime = UIRuntime(bus, None)

    ui_skin = json.loads(UI_SKIN_PATH.read_text())
    runtime.create_panel("main_menu", ui_skin["screens"]["main_menu"])

    observed: list[dict | None] = []
    bus.subscribe("new_game_selected", lambda payload: observed.append(payload))

    # Simulated ui-context confirm action, routed through handle_ui_input
    # exactly as 07-input-renderer-audio.md's InputHandler would call it —
    # not a direct call into the button's own handler.
    consumed = runtime.handle_ui_input("confirm")

    assert consumed is True
    assert observed == [{"campaign_id": None}]


def test_targeting_overlay_activation_and_confirm_teardown_over_the_real_bus():
    bus = EventBus()
    overlay = TargetingOverlay(bus)

    assert overlay.is_active() is False

    bus.emit(
        "spell_cast_initiated",
        {"entity_id": 7, "spell_id": "fireball", "targeting_mode": "tile"},
    )
    assert overlay.is_active() is True

    bus.emit("target_confirmed", {"target": (5, 5)})
    assert overlay.is_active() is False


def test_targeting_overlay_activation_and_cancel_teardown_over_the_real_bus():
    bus = EventBus()
    overlay = TargetingOverlay(bus)

    bus.emit(
        "spell_cast_initiated",
        {"entity_id": 7, "spell_id": "fireball", "targeting_mode": "tile"},
    )
    assert overlay.is_active() is True

    bus.emit("cancel_targeting", {"entity_id": 7})
    assert overlay.is_active() is False
