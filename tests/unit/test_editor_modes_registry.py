"""Unit tests for the application shell's mode registry
(``docs/components/13-editor-core-authoring.md`` §2.2/§2.5/§8)."""

from __future__ import annotations

from typing import Any

from editor.editor import Editor
from editor.modes import SpriteCache, compute_dropdown_rect, table_column_headers


class _DummyMode:
    """A brand-new, external mode implementing the ``EditorMode`` protocol
    structurally — this is exactly the shape `14-editor-visual-quest-dialog.md`
    will register (doc §2.5), registered here with zero changes to
    ``editor.py`` beyond the ``register_mode`` call itself."""

    def __init__(self) -> None:
        self.activated = False
        self.deactivated = False
        self.events: list[Any] = []
        self.received_context: dict | None = None

    def handle_event(self, event: Any) -> None:
        self.events.append(event)

    def update(self, dt: float) -> None:
        pass

    def draw(self, surface: Any) -> None:
        pass

    def on_activate(self) -> None:
        self.activated = True

    def on_deactivate(self) -> None:
        self.deactivated = True

    def receive_context(self, context: dict) -> None:
        self.received_context = context


def test_builtin_six_tabs_are_registered_in_order() -> None:
    editor = Editor()
    ids = [mode_id for mode_id, _label in editor.tab_list]
    assert ids == [
        "map_editor",
        "sprite_editor",
        "data_editor",
        "skin_editor",
        "sprite_manager",
        "audio_tab",
    ]


def test_editor_boots_with_map_editor_active() -> None:
    editor = Editor()
    assert editor.active_mode_id == "map_editor"


def test_register_a_brand_new_dummy_mode_makes_it_switchable_and_listed() -> None:
    editor = Editor()
    dummy = _DummyMode()

    editor.register_mode("quest_editor", dummy, "Quest", hotkey="F5")

    assert ("quest_editor", "Quest") in editor.tab_list
    editor.switch_mode("quest_editor")
    assert editor.active_mode_id == "quest_editor"
    assert dummy.activated is True


def test_switching_modes_deactivates_the_previous_one() -> None:
    editor = Editor()
    dummy = _DummyMode()
    editor.register_mode("quest_editor", dummy, "Quest")

    editor.switch_mode("quest_editor")
    editor.switch_mode("map_editor")

    assert dummy.deactivated is True
    assert editor.active_mode_id == "map_editor"


def test_switch_mode_to_unknown_id_is_a_noop() -> None:
    editor = Editor()
    editor.switch_mode("does_not_exist")
    assert editor.active_mode_id == "map_editor"


def test_hotkey_switches_mode() -> None:
    editor = Editor()
    assert editor.handle_hotkey("F3") is True
    assert editor.active_mode_id == "data_editor"
    assert editor.handle_hotkey("F99") is False


def test_navigate_to_delivers_context_via_receive_context() -> None:
    editor = Editor()
    dummy = _DummyMode()
    editor.register_mode("quest_editor", dummy, "Quest")

    editor.navigate_to("quest_editor", {"asset_path": "sprites/x.png"})

    assert editor.active_mode_id == "quest_editor"
    assert dummy.received_context == {"asset_path": "sprites/x.png"}


def test_handle_event_update_draw_delegate_to_active_mode() -> None:
    editor = Editor()
    dummy = _DummyMode()
    editor.register_mode("quest_editor", dummy, "Quest")
    editor.switch_mode("quest_editor")

    editor.handle_event("fake-event")
    editor.update(0.016)

    assert dummy.events == ["fake-event"]


def test_dropdown_rect_stays_below_button_when_there_is_room() -> None:
    rect = compute_dropdown_rect((10, 10, 100, 20), popup_size=(80, 40), screen_size=(800, 600))
    assert rect == (10, 30, 80, 40)


def test_dropdown_rect_flips_above_button_to_avoid_bottom_clipping() -> None:
    rect = compute_dropdown_rect((10, 580, 100, 20), popup_size=(80, 40), screen_size=(800, 600))
    x, y, w, h = rect
    assert y + h <= 600
    assert y < 580  # flipped above the button


def test_dropdown_rect_flips_left_to_avoid_right_edge_clipping() -> None:
    rect = compute_dropdown_rect((750, 10, 40, 20), popup_size=(100, 40), screen_size=(800, 600))
    x, y, w, h = rect
    assert x + w <= 800


def test_table_column_headers_are_labeled() -> None:
    headers = table_column_headers([{"key": "type", "label": "Type"}, {"key": "value", "label": "Value"}])
    assert headers == ["Type", "Value"]


def test_archive_support_available_now_that_component_12_has_merged() -> None:
    """Soft dependency on 12-modding-archive-system.md (doc §2.1): now that
    it has merged, the editor UI's "Open Archive..." should be enabled."""
    editor = Editor()
    assert editor.archive_support_available() is True


def test_sprite_cache_invalidate_bumps_version_and_clears() -> None:
    cache = SpriteCache()
    cache.put("a.png", object())
    assert cache.get("a.png") is not None

    cache.invalidate("a.png")

    assert cache.get("a.png") is None
    assert cache.version("a.png") == 2  # one bump from put(), one from invalidate()
