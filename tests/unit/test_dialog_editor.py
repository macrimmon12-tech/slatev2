"""Unit tests for the Dialog Editor
(``docs/components/14-editor-visual-quest-dialog.md`` §1.2/§4/§8).

Per CONTRACTS.md §8 (soft dependency handling — `11-npc-dialog-shop-content.md`
hasn't merged yet): these tests load an equivalent fixture under this
component's own ``tests/fixtures/dialog_editor/`` — the exact example
content from `11`'s doc §5.1 — rather than importing anything from `11`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from editor.modes import EditorContext, SpriteCache
from editor.modes.dialog_editor import DialogEditorMode
from editor.project import Project

FIXTURE_DIALOG = Path(__file__).resolve().parents[1] / "fixtures" / "dialog_editor" / "shopkeeper_mira_dialog.json"


def _make_project_with_dialog(tmp_path: Path) -> Project:
    project = Project.new(tmp_path / "proj")
    dest = project.data_path("dialogs", "shopkeeper_mira_dialog.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE_DIALOG, dest)
    return project


def _make_mode(project: Project) -> DialogEditorMode:
    context = EditorContext(get_project=lambda: project, navigate_to=lambda *a, **k: None, sprite_cache=SpriteCache())
    return DialogEditorMode(context)


def test_load_reads_nodes_and_start_node(tmp_path: Path) -> None:
    project = _make_project_with_dialog(tmp_path)
    mode = _make_mode(project)

    mode.load("shopkeeper_mira_dialog")

    assert mode.data["start_node"] == "greet"
    assert set(mode.node_ids()) == {"greet", "lore_1", "lore_2", "end"}


def test_edges_distinguish_goto_from_action(tmp_path: Path) -> None:
    project = _make_project_with_dialog(tmp_path)
    mode = _make_mode(project)
    mode.load("shopkeeper_mira_dialog")

    edges = mode.edges()

    goto_edges = [e for e in edges if e["kind"] == "goto"]
    action_edges = [e for e in edges if e["kind"] == "action"]
    assert {"from": "greet", "choice_index": 1, "kind": "goto", "to": "lore_1"} in goto_edges
    assert any(e["from"] == "greet" and e["action"] == "open_shop" for e in action_edges)


def test_auto_layout_positions_every_node_on_first_load(tmp_path: Path) -> None:
    project = _make_project_with_dialog(tmp_path)
    mode = _make_mode(project)

    mode.load("shopkeeper_mira_dialog")

    assert set(mode.layout.keys()) == {"greet", "lore_1", "lore_2", "end"}
    for pos in mode.layout.values():
        assert isinstance(pos["x"], int) and isinstance(pos["y"], int)


def test_layout_sidecar_is_a_sibling_file_not_inside_dialog_json(tmp_path: Path) -> None:
    project = _make_project_with_dialog(tmp_path)
    mode = _make_mode(project)
    mode.load("shopkeeper_mira_dialog")

    mode.move_node_layout("greet", 500, 500)
    mode.save()

    layout_path = project.data_path("dialogs", "shopkeeper_mira_dialog.layout.json")
    assert layout_path.exists()
    payload = json.loads(layout_path.read_text())
    assert payload["node_positions"]["greet"] == {"x": 500, "y": 500}
    # The dialog JSON itself never gains an undocumented field.
    dialog_payload = json.loads(project.data_path("dialogs", "shopkeeper_mira_dialog.json").read_text())
    assert "layout" not in dialog_payload
    assert "_editor_layout" not in dialog_payload


def test_losing_layout_file_is_harmless_and_re_auto_lays_out(tmp_path: Path) -> None:
    project = _make_project_with_dialog(tmp_path)
    mode = _make_mode(project)
    mode.load("shopkeeper_mira_dialog")
    mode.move_node_layout("greet", 999, 999)
    mode.save()

    layout_path = project.data_path("dialogs", "shopkeeper_mira_dialog.layout.json")
    layout_path.unlink()

    reloaded = _make_mode(project)
    reloaded.load("shopkeeper_mira_dialog")  # must not raise
    assert set(reloaded.layout.keys()) == {"greet", "lore_1", "lore_2", "end"}


def test_add_choice_edit_save_preserves_every_untouched_field_and_key_order(tmp_path: Path) -> None:
    project = _make_project_with_dialog(tmp_path)
    mode = _make_mode(project)
    original_text = FIXTURE_DIALOG.read_text()
    original = json.loads(original_text)
    mode.load("shopkeeper_mira_dialog")

    mode.add_choice("greet", text="Got any rumors?", goto="lore_1")
    saved_path = mode.save()

    new = json.loads(saved_path.read_text())

    assert new["id"] == original["id"]
    assert new["start_node"] == original["start_node"]
    assert list(new["nodes"].keys()) == list(original["nodes"].keys())

    for node_id in original["nodes"]:
        if node_id == "greet":
            continue
        # byte-for-byte reproduction (incl. key order) of every untouched node
        assert json.dumps(new["nodes"][node_id], indent=2) == json.dumps(original["nodes"][node_id], indent=2)

    original_choices = original["nodes"]["greet"]["choices"]
    new_choices = new["nodes"]["greet"]["choices"]
    assert new_choices[: len(original_choices)] == original_choices
    assert len(new_choices) == len(original_choices) + 1
    assert new_choices[-1] == {"text": "Got any rumors?", "goto": "lore_1"}
    assert new["nodes"]["greet"]["text"] == original["nodes"]["greet"]["text"]


def test_set_node_text_only_touches_that_field(tmp_path: Path) -> None:
    project = _make_project_with_dialog(tmp_path)
    mode = _make_mode(project)
    original = json.loads(FIXTURE_DIALOG.read_text())
    mode.load("shopkeeper_mira_dialog")

    mode.set_node_text("end", "Farewell, and safe travels.")
    saved_path = mode.save()
    new = json.loads(saved_path.read_text())

    assert new["nodes"]["end"]["text"] == "Farewell, and safe travels."
    assert new["nodes"]["end"]["choices"] == original["nodes"]["end"]["choices"]
    for node_id in ("greet", "lore_1", "lore_2"):
        assert new["nodes"][node_id] == original["nodes"][node_id]


def test_set_choice_condition_edits_plain_text_without_validating_grammar(tmp_path: Path) -> None:
    project = _make_project_with_dialog(tmp_path)
    mode = _make_mode(project)
    mode.load("shopkeeper_mira_dialog")

    mode.set_choice_condition("lore_1", 0, "not campaign.mira_lore_told and stat.charisma > 3")

    assert mode.get_node("lore_1")["choices"][0]["condition"] == "not campaign.mira_lore_told and stat.charisma > 3"


def test_remove_and_reorder_choices(tmp_path: Path) -> None:
    project = _make_project_with_dialog(tmp_path)
    mode = _make_mode(project)
    mode.load("shopkeeper_mira_dialog")

    mode.reorder_choices("greet", [2, 0, 1])
    texts = [c["text"] for c in mode.get_node("greet")["choices"]]
    assert texts == ["Just passing through.", "Show me your wares.", "Tell me about this place."]

    mode.remove_choice("greet", 0)
    assert len(mode.get_node("greet")["choices"]) == 2


def test_set_start_node(tmp_path: Path) -> None:
    project = _make_project_with_dialog(tmp_path)
    mode = _make_mode(project)
    mode.load("shopkeeper_mira_dialog")

    mode.set_start_node("lore_1")

    assert mode.data["start_node"] == "lore_1"


# -- context link receiving half (doc §3) -------------------------------------


def test_open_new_seeds_a_blank_valid_dialog(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "proj")
    mode = _make_mode(project)

    mode.open_new("guard_captain_dialog")

    assert mode.dialog_id == "guard_captain_dialog"
    assert mode.data["id"] == "guard_captain_dialog"
    assert mode.data["start_node"] in mode.data["nodes"]
    assert mode.dirty is True


def test_receive_context_with_new_dialog_id_calls_open_new(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "proj")
    mode = _make_mode(project)

    mode.receive_context({"new_dialog_id": "guard_captain_dialog"})

    assert mode.dialog_id == "guard_captain_dialog"


def test_receive_context_without_the_key_is_a_noop(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "proj")
    mode = _make_mode(project)

    mode.receive_context({"something_else": "x"})

    assert mode.dialog_id is None


def test_open_new_then_save_writes_a_new_file(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "proj")
    mode = _make_mode(project)
    mode.open_new("guard_captain_dialog")

    saved_path = mode.save()

    assert saved_path == project.data_path("dialogs", "guard_captain_dialog.json")
    assert saved_path.exists()
    assert mode.dirty is False


def test_has_visible_save_control_and_tooltips(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "proj")
    mode = _make_mode(project)
    assert mode.has_visible_save_control() is True
    assert mode.has_tooltip("condition_field")
