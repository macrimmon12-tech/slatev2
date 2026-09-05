"""Unit tests for engine.ui.ui_runtime — see
docs/components/08-ui-runtime.md §8/§9 for the Definition of Done and Test
Plan bullets these correspond to.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.core.events import EventBus
from engine.core.schemas.ui_skin_schema import WidgetTreeError, validate_widget_tree
from engine.ui.ui_runtime import UIRuntime, resolve_rect

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SAMPLES = FIXTURES / "widget_tree_samples"
UI_SKIN_PATH = Path(__file__).resolve().parents[2] / "data" / "config" / "ui_skin.json"


def _load(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text())


class FakeRegistry:
    """Stands in for engine.core.registry.DataRegistry (already merged,
    but a lightweight fake keeps these tests independent of disk state) —
    same shape used by tests/unit/test_progression.py."""

    def __init__(self, configs: dict[str, dict]) -> None:
        self._configs = configs

    def get(self, namespace: str, id: str):
        if namespace == "configs":
            return self._configs.get(id)
        return None

    def all(self, namespace: str) -> dict[str, dict]:
        return {}


# ---------------------------------------------------------------------------
# resolve_rect
# ---------------------------------------------------------------------------


def test_resolve_rect_center_both_axes():
    spec = {"rect": {"x": "center", "y": "center", "w": 200, "h": 100}}
    assert resolve_rect(spec, (0, 0, 800, 600)) == (300, 250, 200, 100)


def test_resolve_rect_numeric_top_left_default_anchor():
    spec = {"rect": {"x": 10, "y": 20, "w": 50, "h": 30}}
    assert resolve_rect(spec, (100, 100, 800, 600)) == (110, 120, 50, 30)


def test_resolve_rect_anchor_bottom_right():
    spec = {"rect": {"x": 10, "y": 5, "w": 40, "h": 20}, "anchor": "bottom_right"}
    assert resolve_rect(spec, (0, 0, 800, 600)) == (800 - 10 - 40, 600 - 5 - 20, 40, 20)


def test_resolve_rect_missing_wh_fills_parent():
    spec = {"rect": {"x": 0, "y": 0}}
    assert resolve_rect(spec, (0, 0, 400, 300)) == (0, 0, 400, 300)


def test_resolve_rect_flat_xywh_fields():
    spec = {"x": 5, "y": 5, "w": 10, "h": 10}
    assert resolve_rect(spec, (0, 0, 100, 100)) == (5, 5, 10, 10)


# ---------------------------------------------------------------------------
# Widget-tree parser: all six node types, nested children, visible_if
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_name",
    [
        "panel_center.json",
        "label.json",
        "bar.json",
        "button.json",
        "list.json",
        "grid.json",
        "nested_visible_if.json",
    ],
)
def test_widget_tree_fixtures_validate(fixture_name):
    validate_widget_tree(_load(fixture_name))


def test_validate_widget_tree_rejects_unknown_type():
    with pytest.raises(WidgetTreeError):
        validate_widget_tree({"type": "NotAWidget"})


def test_validate_widget_tree_rejects_children_on_non_container():
    with pytest.raises(WidgetTreeError):
        validate_widget_tree({"type": "Label", "text": "x", "children": []})


def test_validate_widget_tree_requires_items_key_on_list():
    with pytest.raises(WidgetTreeError):
        validate_widget_tree({"type": "List"})


def test_validate_widget_tree_requires_value_key_on_bar():
    with pytest.raises(WidgetTreeError):
        validate_widget_tree({"type": "Bar"})


def test_create_panel_accepts_each_node_type_without_error():
    bus = EventBus()
    runtime = UIRuntime(bus, None)
    for fixture_name in ("panel_center.json", "label.json", "bar.json", "button.json", "list.json", "grid.json"):
        runtime.create_panel(fixture_name, _load(fixture_name))


def test_visible_if_hides_node_until_data_matches():
    bus = EventBus()
    runtime = UIRuntime(bus, None)
    tree = _load("nested_visible_if.json")
    runtime.create_panel("cond_panel", tree, data={"show_label": False})

    label_widget = runtime._panels["cond_panel"].root.children[0]
    interactive_before = list(runtime._iter_interactive(runtime._panels["cond_panel"].root, {"show_label": False}))
    assert interactive_before == []  # nothing interactive either way here; sanity that traversal doesn't crash

    from engine.ui.ui_runtime import _is_visible

    assert _is_visible(label_widget.spec, {"show_label": False}) is False
    assert _is_visible(label_widget.spec, {"show_label": True}) is True


# ---------------------------------------------------------------------------
# create_panel / update_panel / destroy_panel
# ---------------------------------------------------------------------------


def test_update_panel_rebinds_data_without_rebuilding_widgets():
    bus = EventBus()
    runtime = UIRuntime(bus, None)
    runtime.create_panel("hp_bar", _load("bar.json"), data={"hp": 10, "max_hp": 100})

    root_before = runtime._panels["hp_bar"].root
    runtime.update_panel("hp_bar", {"hp": 55})

    root_after = runtime._panels["hp_bar"].root
    assert root_after is root_before  # widget identity stable across update
    assert runtime._panels["hp_bar"].data["hp"] == 55
    assert runtime._panels["hp_bar"].data["max_hp"] == 100  # merged, not replaced


def test_update_panel_on_unknown_panel_is_a_noop():
    bus = EventBus()
    runtime = UIRuntime(bus, None)
    runtime.update_panel("does_not_exist", {"hp": 1})  # must not raise


def test_destroy_panel_removes_it_and_is_idempotent():
    bus = EventBus()
    runtime = UIRuntime(bus, None)
    runtime.create_panel("btn1", _load("button.json"))
    assert "btn1" in runtime._panels

    runtime.destroy_panel("btn1")
    assert "btn1" not in runtime._panels
    assert "btn1" not in runtime._stack

    runtime.destroy_panel("btn1")  # no-op, must not raise
    runtime.destroy_panel("never_created")  # no-op, must not raise


def test_create_panel_stacks_in_creation_order():
    bus = EventBus()
    runtime = UIRuntime(bus, None)
    runtime.create_panel("a", _load("button.json"))
    runtime.create_panel("b", _load("button.json"))
    assert runtime._stack == ["a", "b"]


# ---------------------------------------------------------------------------
# main_menu / save_select: pure ui_skin.json trees, zero special-cased code
# ---------------------------------------------------------------------------


def _ui_skin_screens() -> dict:
    return json.loads(UI_SKIN_PATH.read_text())["screens"]


def test_main_menu_new_game_button_emits_documented_event():
    bus = EventBus()
    runtime = UIRuntime(bus, None)
    runtime.create_panel("main_menu", _ui_skin_screens()["main_menu"])

    received = []
    bus.subscribe("new_game_selected", lambda payload: received.append(payload))

    assert runtime.handle_ui_input("confirm") is True
    assert received == [{"campaign_id": None}]


def test_main_menu_quit_button_emits_documented_event():
    bus = EventBus()
    runtime = UIRuntime(bus, None)
    runtime.create_panel("main_menu", _ui_skin_screens()["main_menu"])

    received = []
    bus.subscribe("quit_selected", lambda payload: received.append(payload))

    runtime.handle_ui_input("down")  # New Game -> Load Game
    runtime.handle_ui_input("down")  # Load Game -> Quit
    assert runtime.handle_ui_input("confirm") is True
    assert received == [{}]


def test_save_select_built_purely_from_json_emits_save_slot_selected():
    bus = EventBus()
    runtime = UIRuntime(bus, None)
    runtime.create_panel(
        "save_select",
        _ui_skin_screens()["save_select"],
        data={"save_slots": [{"name": "Slot 1", "slot_id": "slot_1"}]},
    )

    received = []
    bus.subscribe("save_slot_selected", lambda payload: received.append(payload))

    assert runtime.handle_ui_input("confirm") is True
    assert received == [{"slot": "slot_1"}]


# ---------------------------------------------------------------------------
# show_panel routing
# ---------------------------------------------------------------------------


def test_show_panel_routes_to_already_registered_tree_and_brings_it_forward():
    bus = EventBus()
    runtime = UIRuntime(bus, None)
    runtime.create_panel("a", _load("button.json"))
    runtime.create_panel("b", _load("panel_center.json"))
    assert runtime._stack == ["a", "b"]

    bus.emit("show_panel", {"panel_id": "a", "data": {"extra": 1}})

    assert runtime._stack == ["b", "a"]  # "a" brought to front
    assert runtime._panels["a"].data["extra"] == 1


def test_show_panel_noops_silently_when_nothing_registered():
    bus = EventBus()
    runtime = UIRuntime(bus, None)

    bus.emit("show_panel", {"panel_id": "ghost_panel"})  # must not raise

    assert "ghost_panel" not in runtime._panels
    assert runtime._stack == []


def test_show_panel_falls_back_to_ui_skin_template_when_not_yet_created():
    bus = EventBus()
    registry = FakeRegistry({"ui_skin": {"schema_version": 1, "screens": _ui_skin_screens()}})
    runtime = UIRuntime(bus, registry)

    assert "save_select" not in runtime._panels  # not created yet
    bus.emit("show_panel", {"panel_id": "save_select", "data": {"save_slots": []}})

    assert "save_select" in runtime._panels
    assert runtime._stack == ["save_select"]


# ---------------------------------------------------------------------------
# on_click on the real event bus via handle_ui_input
# ---------------------------------------------------------------------------


def test_on_click_emits_configured_event_and_payload_via_confirm_action():
    bus = EventBus()
    runtime = UIRuntime(bus, None)
    runtime.create_panel("btn1", _load("button.json"))

    received = []
    bus.subscribe("test_button_clicked", lambda payload: received.append(payload))

    assert runtime.handle_ui_input("confirm") is True
    assert received == [{"foo": "bar"}]


def test_list_item_on_click_interpolates_item_fields():
    bus = EventBus()
    runtime = UIRuntime(bus, None)
    runtime.create_panel("list1", _load("list.json"), data={"items": [{"name": "Sword", "id": "sword_1"}]})

    received = []
    bus.subscribe("item_clicked", lambda payload: received.append(payload))

    assert runtime.handle_ui_input("confirm") is True
    assert received == [{"id": "sword_1"}]


def test_handle_ui_input_returns_false_when_no_panels():
    bus = EventBus()
    runtime = UIRuntime(bus, None)
    assert runtime.handle_ui_input("confirm") is False


def test_handle_ui_input_up_down_moves_focus_between_buttons():
    bus = EventBus()
    runtime = UIRuntime(bus, None)
    runtime.create_panel("main_menu", _ui_skin_screens()["main_menu"])

    received = []
    bus.subscribe("show_panel", lambda payload: received.append(payload))

    runtime.handle_ui_input("down")  # focus moves to Load Game
    assert runtime.handle_ui_input("confirm") is True
    assert received == [{"panel_id": "save_select"}]


# ---------------------------------------------------------------------------
# Minimal fixture round-trip: raw JSON -> create_panel -> draw(), no error
# ---------------------------------------------------------------------------


def test_minimal_fixture_round_trips_through_create_panel_and_draw():
    tree = json.loads((FIXTURES / "ui_skin_minimal.json").read_text())
    bus = EventBus()
    runtime = UIRuntime(bus, None)
    runtime.create_panel("minimal", tree)

    class DummySurface:
        pass

    runtime.draw(DummySurface())  # must not raise, pygame or no pygame
