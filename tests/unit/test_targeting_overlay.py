"""Unit tests for engine.ui.targeting_overlay — see
docs/components/08-ui-runtime.md §8/§9.
"""

from __future__ import annotations

from engine.core.events import EventBus
from engine.ui.targeting_overlay import TargetingOverlay


class FakeInputHandler:
    def __init__(self) -> None:
        self.contexts: list[str] = []

    def set_context(self, context: str) -> None:
        self.contexts.append(context)


def test_starts_inactive():
    bus = EventBus()
    overlay = TargetingOverlay(bus)
    assert overlay.is_active() is False


def test_activates_on_spell_cast_initiated():
    bus = EventBus()
    overlay = TargetingOverlay(bus)

    bus.emit("spell_cast_initiated", {"entity_id": 1, "spell_id": "fireball", "targeting_mode": "tile"})

    assert overlay.is_active() is True


def test_tears_down_on_target_confirmed():
    bus = EventBus()
    overlay = TargetingOverlay(bus)
    bus.emit("spell_cast_initiated", {"entity_id": 1, "spell_id": "fireball", "targeting_mode": "tile"})
    assert overlay.is_active() is True

    bus.emit("target_confirmed", {"target": (3, 4)})

    assert overlay.is_active() is False


def test_tears_down_on_cancel_targeting():
    bus = EventBus()
    overlay = TargetingOverlay(bus)
    bus.emit("spell_cast_initiated", {"entity_id": 1, "spell_id": "fireball", "targeting_mode": "tile"})
    assert overlay.is_active() is True

    bus.emit("cancel_targeting", {"entity_id": 1})

    assert overlay.is_active() is False


def test_restores_input_context_on_both_teardown_paths():
    bus = EventBus()
    handler = FakeInputHandler()
    overlay = TargetingOverlay(bus, input_handler=handler)

    bus.emit("spell_cast_initiated", {"entity_id": 1, "spell_id": "fireball", "targeting_mode": "tile"})
    assert handler.contexts == ["targeting"]

    bus.emit("target_confirmed", {"target": (1, 1)})
    assert handler.contexts == ["targeting", "game"]

    handler.contexts.clear()
    bus.emit("spell_cast_initiated", {"entity_id": 1, "spell_id": "fireball", "targeting_mode": "tile"})
    bus.emit("cancel_targeting", {"entity_id": 1})
    assert handler.contexts == ["targeting", "game"]


def test_no_cursor_modes_skip_drawing_without_error():
    bus = EventBus()
    overlay = TargetingOverlay(bus)
    bus.emit("spell_cast_initiated", {"entity_id": 1, "spell_id": "heal", "targeting_mode": "self"})

    class DummySurface:
        pass

    overlay.draw(DummySurface())  # must not raise, and is a documented no-op for "self"


def test_draw_without_cursor_position_is_a_noop():
    bus = EventBus()
    overlay = TargetingOverlay(bus)
    bus.emit("spell_cast_initiated", {"entity_id": 1, "spell_id": "fireball", "targeting_mode": "tile"})

    class DummySurface:
        pass

    overlay.draw(DummySurface())  # no cursor tile set yet — must not raise
