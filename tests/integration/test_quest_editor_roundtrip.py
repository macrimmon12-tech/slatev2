"""Integration test for the Visual Quest Editor's round-trip fidelity —
the centerpiece requirement of `14-editor-visual-quest-dialog.md` (doc
§7/§8), plus its Dialog Editor counterpart and the mode-registration seam
(CONTRACTS.md §7.3: drives the real entry points end-to-end, not mocks of
them — real file writes/reads on disk, a real ``QuestEditorMode``/
``DialogEditorMode`` object, sourced from real, loaded fixtures).

Soft-dependency note (CONTRACTS.md §8): `10-lua-scripting-layer.md` has
merged, but its shipped ``data/scripts/snippets.json`` only has 2 example
snippets (trigger + action); this test's round-trip needs 3 distinct
categories per doc §7, so it sources the palette from this component's own
fixture (``tests/fixtures/quest_editor/snippets.json``, a superset adding
one ``condition`` snippet, validated against `10`'s own schema in
``tests/unit/test_quest_editor.py``) rather than waiting on `10` to add a
third example. `11-npc-dialog-shop-content.md` has not merged; the Dialog
Editor case below uses an equivalent fixture
(``tests/fixtures/dialog_editor/shopkeeper_mira_dialog.json`` — the exact
example content from that doc's own §5.1) per CONTRACTS.md §8's stub-and-
extend allowance, and a local structural check standing in for `11`'s own
(not-yet-existing) dialog schema validator.
"""

from __future__ import annotations

import json
from pathlib import Path

from editor.editor import Editor
from editor.modes.dialog_editor import DialogEditorMode
from editor.modes.quest_editor import QuestEditorMode, register_modes
from editor.project import Project

FIXTURE_SNIPPETS = Path(__file__).resolve().parents[1] / "fixtures" / "quest_editor" / "snippets.json"
FIXTURE_DIALOG = Path(__file__).resolve().parents[1] / "fixtures" / "dialog_editor" / "shopkeeper_mira_dialog.json"


def _validate_dialog_shape(data: dict) -> None:
    """A local structural check standing in for `11-npc-dialog-shop-content.md`'s
    own (not-yet-existing) dialog-schema validator (doc §5.1 field
    vocabulary, read-only reference — this component does not own or
    redefine that schema). Replace with `11`'s real validator once it
    merges, per CONTRACTS.md §8."""
    assert isinstance(data.get("id"), str) and data["id"]
    assert isinstance(data.get("start_node"), str)
    nodes = data.get("nodes")
    assert isinstance(nodes, dict) and data["start_node"] in nodes
    for node in nodes.values():
        assert isinstance(node.get("text"), str)
        for choice in node.get("choices", []):
            assert isinstance(choice.get("text"), str)
            assert ("goto" in choice) != ("action" in choice) or ("goto" not in choice and "action" not in choice)


def test_registration_seam_both_modes_slot_in_with_zero_editor_py_changes() -> None:
    """CONTRACTS.md §7.4 / doc §3/§8: both modes register into 13's real,
    documented mode system with no edits to ``editor.py`` beyond the
    ``register_mode`` call itself (proven already generically by 13's own
    dummy-mode test; this asserts it holds for *this component's real*
    modes specifically)."""
    editor = Editor()

    quest_mode, dialog_mode = register_modes(editor)

    assert isinstance(quest_mode, QuestEditorMode)
    assert isinstance(dialog_mode, DialogEditorMode)
    assert ("quest_editor", "Quest") in editor.tab_list
    assert ("dialog_editor", "Dialog") in editor.tab_list

    assert editor.handle_hotkey("F5") is True
    assert editor.active_mode_id == "quest_editor"
    assert editor.handle_hotkey("F6") is True
    assert editor.active_mode_id == "dialog_editor"

    # Both real mode objects satisfy update/draw against a dummy surface
    # without raising (they're registered into the shell's real
    # switch/update/draw cycle, not mocked stand-ins).
    editor.update(1 / 60)
    editor.draw()


def test_quest_editor_round_trip_generate_hand_edit_reparse_reregenerate(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "proj")
    quest_path = project.data_path("scripts", "test_quest.lua")

    def make_mode() -> QuestEditorMode:
        editor = Editor()
        mode, _ = register_modes(editor)
        # Point this real mode instance at this component's own fixture
        # snippets file rather than the project's (empty) one, per the
        # soft-dependency note above.
        mode._snippets_path_override = FIXTURE_SNIPPETS  # noqa: SLF001 — same module, integration-test setup
        return mode

    # -- step 1: generate a quest visually from 3 distinct categories -----
    mode = make_mode()
    trigger_id = mode.add_node("trigger_on_enter_room", x=0, y=0, node_id="n_trigger")
    condition_id = mode.add_node("condition_has_item", x=260, y=0, node_id="n_condition")
    action_id = mode.add_node("action_grant_item", x=520, y=0, node_id="n_action")
    mode.set_param(trigger_id, "room_tag", "vault_entrance")
    mode.set_param(condition_id, "item_id", "vault_key")
    mode.set_param(action_id, "item_id", "gold_coin")
    mode.set_param(action_id, "quantity", 50)

    saved_path = mode.save(quest_path)
    assert saved_path == quest_path
    assert quest_path.exists()  # a real file write, not an in-memory handoff

    # -- step 2: reload off disk, confirm all 3 render as real nodes -------
    reload_mode = make_mode()
    reload_mode.load(quest_path)  # a real file read
    kinds_after_first_reload = {n["id"]: n["kind"] for n in reload_mode.nodes()}
    assert kinds_after_first_reload == {trigger_id: "node", condition_id: "node", action_id: "node"}

    # -- step 3: hand-edit the file on disk: inject an unrecognized snippet
    with open(quest_path, "a") as fh:
        fh.write("\n-- a scripter's hand-written addition, no markers at all\nlocal quest_version = 2\n")
    hand_edited_text = quest_path.read_text()

    # -- step 4: reload; assert Custom Block + all 3 originals intact ------
    after_hand_edit = make_mode()
    after_hand_edit.load(quest_path)
    nodes_after_hand_edit = after_hand_edit.nodes()
    kinds = {n["id"]: n["kind"] for n in nodes_after_hand_edit if n["kind"] == "node"}
    assert kinds == {trigger_id: "node", condition_id: "node", action_id: "node"}

    custom_blocks = [n for n in nodes_after_hand_edit if n["kind"] == "custom_block"]
    assert len(custom_blocks) == 1
    assert "a scripter's hand-written addition" in custom_blocks[0]["raw_text"]
    assert "local quest_version = 2" in custom_blocks[0]["raw_text"]

    # -- step 5: save again untouched -> byte-identical to step 3's file ---
    after_hand_edit.save(quest_path)
    assert quest_path.read_text() == hand_edited_text

    # -- step 6: edit one generated node's params via the inspector --------
    after_hand_edit.set_param(action_id, "quantity", 99)
    after_hand_edit.save(quest_path)

    final_mode = make_mode()
    final_mode.load(quest_path)
    final_nodes = {n["id"]: n for n in final_mode.nodes()}

    assert final_nodes[action_id]["kind"] == "node"
    assert final_nodes[action_id]["params"]["quantity"] == 99
    # the Custom Block survives untouched
    final_custom = [n for n in final_nodes.values() if n["kind"] == "custom_block"]
    assert len(final_custom) == 1
    assert "local quest_version = 2" in final_custom[0]["raw_text"]
    # the other 2 generated nodes are unaffected
    assert final_nodes[trigger_id]["params"]["room_tag"] == "vault_entrance"
    assert final_nodes[condition_id]["params"]["item_id"] == "vault_key"


def test_hand_diverged_node_body_demotes_to_custom_block_on_reload(tmp_path: Path) -> None:
    """doc §7/§8's explicit non-"totally unrecognized" case: a node whose
    body has been hand-edited to diverge from what its own declared
    type+params would regenerate demotes on next load."""
    project = Project.new(tmp_path / "proj")
    quest_path = project.data_path("scripts", "test_quest2.lua")

    editor = Editor()
    mode, _ = register_modes(editor)
    mode._snippets_path_override = FIXTURE_SNIPPETS  # noqa: SLF001

    node_id = mode.add_node("action_grant_item", x=0, y=0, node_id="n_action")
    mode.set_param(node_id, "item_id", "torch")
    mode.set_param(node_id, "quantity", 3)
    mode.save(quest_path)

    # Hand-tweak the body of the generated node in place, without touching
    # its @params line — the declared type+params no longer regenerate
    # what's on disk.
    text = quest_path.read_text()
    diverged = text.replace(
        'engine.grant_item(actor_id, "torch", 3)',
        'engine.grant_item(actor_id, "torch", 3)\nengine.log_message("bonus torch!", "info")',
    )
    assert diverged != text
    quest_path.write_text(diverged)

    reload_mode = QuestEditorMode(mode._context, snippets_path=FIXTURE_SNIPPETS)  # noqa: SLF001
    reload_mode.load(quest_path)
    nodes = reload_mode.nodes()
    assert len(nodes) == 1
    assert nodes[0]["kind"] == "custom_block"
    assert "bonus torch!" in nodes[0]["raw_text"]


def test_dialog_editor_real_fixture_round_trip_and_shape_still_valid(tmp_path: Path) -> None:
    """doc §8's second integration case: load `11`'s real example fixture
    dialog (an equivalent copy under this component's own fixtures, per
    CONTRACTS.md §8), make one edit (add a choice), save through the real
    ``DialogEditorMode`` save path, and assert the resulting JSON is still
    valid against the documented dialog schema shape — not just this
    component's own opinion of what it wrote."""
    project = Project.new(tmp_path / "proj")
    dest = project.data_path("dialogs", "shopkeeper_mira_dialog.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(FIXTURE_DIALOG.read_text())

    editor = Editor()
    editor.project = project  # open_project's real effect, without a .pak fixture
    _, dialog_mode = register_modes(editor)

    dialog_mode.load("shopkeeper_mira_dialog")
    dialog_mode.add_choice("greet", text="Got any rumors?", goto="lore_1")
    saved_path = dialog_mode.save()  # a real file write

    assert saved_path == dest
    saved = json.loads(dest.read_text())  # a real file read back
    _validate_dialog_shape(saved)
    assert saved["nodes"]["greet"]["choices"][-1] == {"text": "Got any rumors?", "goto": "lore_1"}
