"""Unit tests for the Visual Quest Editor
(``docs/components/14-editor-visual-quest-dialog.md`` §2/§8).

Per CONTRACTS.md §8 (soft dependency handling): these tests source the
palette from this component's own fixture
(``tests/fixtures/quest_editor/snippets.json`` — a superset of `10`'s real
example file, adding a ``condition`` snippet so the round-trip test can
exercise 3 distinct categories per doc §7), never a not-yet-merged module.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from editor.modes import EditorContext, SpriteCache
from editor.modes.quest_editor import (
    CustomBlock,
    QuestEditorMode,
    QuestNode,
    _tokenize_params,
    extract_body,
    generate_lua,
    instantiate_template,
    parse_lua,
)
from editor.project import Project

FIXTURE_SNIPPETS = Path(__file__).resolve().parents[1] / "fixtures" / "quest_editor" / "snippets.json"


def _snippets_config() -> dict:
    return json.loads(FIXTURE_SNIPPETS.read_text())


def _snippets_by_type() -> dict[str, dict]:
    return {f"{s['category']}:{s['id']}": s for s in _snippets_config()["snippets"]}


def _make_project(tmp_path: Path) -> Project:
    return Project.new(tmp_path / "proj")


def _make_mode(tmp_path: Path) -> QuestEditorMode:
    project = _make_project(tmp_path)
    context = EditorContext(get_project=lambda: project, navigate_to=lambda *a, **k: None, sprite_cache=SpriteCache())
    return QuestEditorMode(context, snippets_path=FIXTURE_SNIPPETS)


# -- marker parsing (doc §2.2/§8) --------------------------------------------


def test_well_formed_node_block_parses_to_correct_fields() -> None:
    snippet = _snippets_config()["snippets"][0]  # trigger_on_enter_room
    text = instantiate_template(snippet, "n1", 42, 99, {"room_tag": "throne"})

    blocks = parse_lua(text, _snippets_by_type())

    assert len(blocks) == 1
    node = blocks[0]
    assert isinstance(node, QuestNode)
    assert node.node_id == "n1"
    assert node.type == "trigger:trigger_on_enter_room"
    assert node.x == 42
    assert node.y == 99
    assert node.params["room_tag"] == "throne"
    assert node.raw_body == extract_body(text)


def test_missing_endnode_degrades_to_one_custom_block_for_rest_of_file() -> None:
    text = (
        '-- @node id="n1" type="trigger:trigger_on_enter_room" x=0 y=0\n'
        '-- @params room_tag="x"\n'
        "engine.subscribe(\"player_moved\", function() end)\n"
    )
    blocks = parse_lua(text, _snippets_by_type())
    assert len(blocks) == 1
    assert isinstance(blocks[0], CustomBlock)
    assert blocks[0].raw_text == text.rstrip("\n") or blocks[0].raw_text.startswith("-- @node")


def test_non_numeric_xy_degrades_to_one_custom_block() -> None:
    text = '-- @node id="n1" type="trigger:trigger_on_enter_room" x=abc y=0\nbody\n-- @endnode'
    blocks = parse_lua(text, _snippets_by_type())
    assert len(blocks) == 1
    assert isinstance(blocks[0], CustomBlock)
    assert "x=abc" in blocks[0].raw_text


def test_parser_never_raises_on_a_hand_mangled_file() -> None:
    mangled = '-- @node id="n1"\ngarbage\n-- @endnode\nmore garbage with no markers at all'
    blocks = parse_lua(mangled, _snippets_by_type())
    assert all(isinstance(b, CustomBlock) for b in blocks)


def test_stray_text_outside_any_marker_becomes_its_own_custom_block() -> None:
    snippet = _snippets_config()["snippets"][0]
    node_block = instantiate_template(snippet, "n1", 0, 0, {"room_tag": "x"})
    text = "-- a preamble header\nlocal VERSION = 1\n\n" + node_block
    blocks = parse_lua(text, _snippets_by_type())
    assert len(blocks) == 2
    assert isinstance(blocks[0], CustomBlock)
    assert "a preamble header" in blocks[0].raw_text
    assert isinstance(blocks[1], QuestNode)


def test_pure_whitespace_gaps_between_recognized_blocks_are_not_custom_blocks() -> None:
    """A freshly generated 2-node file's canonical single-blank-line
    separator must not itself become a spurious Custom Block on reload
    (doc §7 step 2: reload must show exactly the generated nodes)."""
    snippets = _snippets_config()["snippets"]
    block_a = instantiate_template(snippets[0], "n1", 0, 0, {"room_tag": "x"})
    block_b = instantiate_template(snippets[2], "n2", 100, 0, {"item_id": "potion", "quantity": 1})
    text = block_a + "\n\n" + block_b + "\n"
    blocks = parse_lua(text, _snippets_by_type())
    assert [type(b).__name__ for b in blocks] == ["QuestNode", "QuestNode"]


# -- @params tokenizing (doc §2.2 step 3/§8) ---------------------------------


def test_tokenize_params_quoted_string_with_embedded_spaces() -> None:
    params = _tokenize_params('room_tag="throne room"')
    assert params == {"room_tag": "throne room"}


def test_tokenize_params_numbers_and_booleans() -> None:
    params = _tokenize_params("quantity=3 ratio=1.5 negative=-2 flag=true other=false")
    assert params == {"quantity": 3, "ratio": 1.5, "negative": -2, "flag": True, "other": False}


def test_tokenize_params_empty_line_is_empty_dict() -> None:
    assert _tokenize_params("") == {}


# -- template instantiation determinism (doc §8) -----------------------------


def test_instantiate_template_is_deterministic() -> None:
    snippet = _snippets_config()["snippets"][0]
    first = instantiate_template(snippet, "n1", 10, 20, {"room_tag": "vault"})
    second = instantiate_template(snippet, "n1", 10, 20, {"room_tag": "vault"})
    assert first == second


def test_instantiate_template_boolean_param_renders_lowercase_lua_literal() -> None:
    snippet = {
        "id": "s",
        "category": "condition",
        "lua_template": '-- @node id="{id}" type="condition:s" x={x} y={y}\n-- @params flag={flag}\nlocal v = {flag}\n-- @endnode',
    }
    text = instantiate_template(snippet, "n1", 0, 0, {"flag": True})
    assert "flag=true" in text
    assert "local v = true" in text


# -- generation ordering (doc §2.3/§8) ----------------------------------------


def test_generation_ordering_is_stored_order_not_position() -> None:
    snippets = _snippets_by_type()
    node_a = QuestNode(node_id="a", type="trigger:trigger_on_enter_room", x=500, y=500, params={"room_tag": "x"})
    node_b = QuestNode(
        node_id="b", type="action:action_grant_item", x=0, y=0, params={"item_id": "potion", "quantity": 1}
    )
    text = generate_lua([node_a, node_b], snippets)
    assert text.index('id="a"') < text.index('id="b"')

    # Layout-only reordering of x/y (a canvas drag) must not reorder blocks.
    node_a.x, node_a.y = 0, 0
    node_b.x, node_b.y = 500, 500
    text_after_drag = generate_lua([node_a, node_b], snippets)
    assert text_after_drag.index('id="a"') < text_after_drag.index('id="b"')


def test_generate_lua_joins_blocks_with_single_blank_line() -> None:
    snippets = _snippets_by_type()
    node_a = QuestNode(node_id="a", type="trigger:trigger_on_enter_room", x=0, y=0, params={"room_tag": "x"})
    node_b = QuestNode(
        node_id="b", type="action:action_grant_item", x=0, y=0, params={"item_id": "potion", "quantity": 1}
    )
    text = generate_lua([node_a, node_b], snippets)
    assert "-- @endnode\n\n-- @node" in text


def test_custom_block_is_emitted_verbatim_regardless_of_position() -> None:
    custom = CustomBlock(raw_text="-- a hand-written line\nlocal x = 1")
    node = QuestNode(node_id="a", type="trigger:trigger_on_enter_room", x=0, y=0, params={"room_tag": "x"})
    text = generate_lua([custom, node], _snippets_by_type())
    assert text.startswith("-- a hand-written line\nlocal x = 1\n\n-- @node")


# -- recognition vs. demotion (doc §2.2 step 5/§8) ---------------------------


def test_unknown_type_demotes_to_custom_block_with_full_original_bytes() -> None:
    snippet = _snippets_config()["snippets"][0]
    text = instantiate_template(snippet, "n1", 0, 0, {"room_tag": "x"}).replace(
        'type="trigger:trigger_on_enter_room"', 'type="trigger:no_such_snippet"'
    )
    blocks = parse_lua(text, _snippets_by_type())
    assert len(blocks) == 1
    block = blocks[0]
    assert isinstance(block, CustomBlock)
    assert block.raw_text == text
    assert block.node_id == "n1"


def test_hand_diverged_body_demotes_to_custom_block_not_silently_reverted() -> None:
    """doc §2.2/§8: a *valid* type whose body a human hand-tweaked so it no
    longer matches what type+params would regenerate must demote, not
    snap back to the template's version and not stay an editable node."""
    snippet = _snippets_config()["snippets"][1]  # condition_has_item
    good = instantiate_template(snippet, "n2", 0, 0, {"item_id": "potion", "quantity": 1})
    diverged = good.replace(
        "local has_it = engine.has_item(actor_id, \"potion\", 1)",
        "local has_it = engine.has_item(actor_id, \"potion\", 1)\nlocal extra_line = true",
    )
    blocks = parse_lua(diverged, _snippets_by_type())
    assert len(blocks) == 1
    assert isinstance(blocks[0], CustomBlock)
    assert blocks[0].raw_text == diverged


def test_deleted_snippet_demotes_previously_recognized_node() -> None:
    snippet = _snippets_config()["snippets"][0]
    text = instantiate_template(snippet, "n1", 0, 0, {"room_tag": "x"})
    blocks = parse_lua(text, snippets_by_type={})  # snippet no longer loaded
    assert len(blocks) == 1
    assert isinstance(blocks[0], CustomBlock)


def test_extract_body_matches_what_parse_lua_captures() -> None:
    snippet = _snippets_config()["snippets"][0]
    full_text = instantiate_template(snippet, "n1", 0, 0, {"room_tag": "x"})
    body_from_extract = extract_body(full_text)
    blocks = parse_lua(full_text, _snippets_by_type())
    assert isinstance(blocks[0], QuestNode)
    assert blocks[0].raw_body == body_from_extract


# -- palette (doc §7's DoD #1: no code change for a new snippet) ------------


def test_fixture_snippets_validate_against_10s_real_schema() -> None:
    """This component's own fixture (a superset of `10`'s real example
    file, adding a `condition` snippet) must still be valid
    ``snippets.json`` per `10-lua-scripting-layer.md`'s own validator."""
    from engine.core.schemas.snippets_schema import validate_snippets_config

    validate_snippets_config(_snippets_config())


def test_palette_groups_snippets_by_category(tmp_path: Path) -> None:
    mode = _make_mode(tmp_path)
    palette = mode.palette()
    assert {s["id"] for s in palette["trigger"]} == {"trigger_on_enter_room"}
    assert {s["id"] for s in palette["condition"]} == {"condition_has_item"}
    assert {s["id"] for s in palette["action"]} == {"action_grant_item"}


def test_a_new_snippet_added_to_the_fixture_file_appears_with_no_code_change(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    live_snippets_path = project.data_path("scripts", "snippets.json")
    config = _snippets_config()
    live_snippets_path.write_text(json.dumps(config))
    context = EditorContext(get_project=lambda: project, navigate_to=lambda *a, **k: None, sprite_cache=SpriteCache())
    mode = QuestEditorMode(context)  # reads from the *project's* snippets.json, no override

    assert "reward" not in {s["id"] for cat in mode.palette().values() for s in cat}

    config["snippets"].append(
        {
            "id": "reward_grant_xp",
            "category": "reward",
            "label": "Grant XP",
            "description": "Grants XP to the triggering entity.",
            "params": [{"name": "amount", "type": "number", "default": 10}],
            "lua_template": (
                '-- @node id="{id}" type="reward:reward_grant_xp" x={x} y={y}\n'
                "-- @params amount={amount}\n"
                "engine.grant_xp(actor_id, {amount})\n"
                "-- @endnode"
            ),
        }
    )
    live_snippets_path.write_text(json.dumps(config))

    palette = mode.palette()  # same mode instance, no code change
    assert {s["id"] for s in palette["reward"]} == {"reward_grant_xp"}


# -- node/param mutation (doc §7 DoD #2/#6) -----------------------------------


def test_add_node_fills_defaults_from_snippet(tmp_path: Path) -> None:
    mode = _make_mode(tmp_path)
    node_id = mode.add_node("action_grant_item", x=10, y=20)
    node = next(n for n in mode.nodes() if n["id"] == node_id)
    assert node["params"] == {"item_id": "", "quantity": 1}
    assert mode.dirty is True


def test_set_param_updates_regenerated_body(tmp_path: Path) -> None:
    mode = _make_mode(tmp_path)
    node_id = mode.add_node("action_grant_item", x=0, y=0)
    mode.set_param(node_id, "item_id", "healing_potion")
    mode.set_param(node_id, "quantity", 5)
    text = mode.generate_lua()
    assert 'engine.grant_item(actor_id, "healing_potion", 5)' in text


def test_move_node_does_not_reorder_generation(tmp_path: Path) -> None:
    mode = _make_mode(tmp_path)
    first = mode.add_node("trigger_on_enter_room", x=0, y=0)
    second = mode.add_node("action_grant_item", x=0, y=0)
    mode.move_node(first, x=999, y=999)
    text = mode.generate_lua()
    assert text.index(f'id="{first}"') < text.index(f'id="{second}"')


def test_add_node_unknown_snippet_raises(tmp_path: Path) -> None:
    mode = _make_mode(tmp_path)
    with pytest.raises(ValueError):
        mode.add_node("does_not_exist")


def test_undo_reverts_last_mutation(tmp_path: Path) -> None:
    mode = _make_mode(tmp_path)
    node_id = mode.add_node("action_grant_item", x=0, y=0)
    mode.set_param(node_id, "item_id", "potion")
    assert mode.undo() is True
    node = next(n for n in mode.nodes() if n["id"] == node_id)
    assert node["params"]["item_id"] == ""
    assert mode.undo() is True  # undoes add_node itself
    assert mode.nodes() == []
    assert mode.undo() is False


# -- edge inference (doc §2.4 — cosmetic only) --------------------------------


def test_edge_inference_links_trigger_to_action_it_calls(tmp_path: Path) -> None:
    mode = _make_mode(tmp_path)
    trigger_id = mode.add_node("trigger_on_enter_room", x=0, y=0)
    action_id = mode.add_node("action_grant_item", x=0, y=0)
    # A hand-authored trigger body that textually calls the action's
    # engine.* function -- simulate by editing raw_body via a param that
    # embeds it (grant_item's own template body already contains the call,
    # so linking the two via a param demonstrating the heuristic):
    node = next(b for b in mode._graph if getattr(b, "node_id", None) == trigger_id)
    node.raw_body += '\nengine.grant_item(actor_id, "x", 1)'
    edges = mode.edges()
    assert (trigger_id, action_id) in edges


def test_edge_inference_returns_empty_when_no_call_match(tmp_path: Path) -> None:
    mode = _make_mode(tmp_path)
    mode.add_node("trigger_on_enter_room", x=0, y=0)
    mode.add_node("action_grant_item", x=0, y=0)
    assert mode.edges() == []


# -- save/load through the real mode entry points -----------------------------


def test_save_and_load_round_trip_through_real_entry_points(tmp_path: Path) -> None:
    mode = _make_mode(tmp_path)
    node_id = mode.add_node("action_grant_item", x=0, y=0)
    mode.set_param(node_id, "item_id", "sword")

    dest = tmp_path / "quest.lua"
    mode.save(dest)
    assert mode.dirty is False

    reloaded = _make_mode(tmp_path)
    reloaded.load(dest)
    node = next(n for n in reloaded.nodes() if n["id"] == node_id)
    assert node["params"]["item_id"] == "sword"


def test_view_script_text_matches_generate_lua(tmp_path: Path) -> None:
    mode = _make_mode(tmp_path)
    mode.add_node("action_grant_item", x=0, y=0)
    assert mode.view_script_text() == mode.generate_lua()


def test_save_hand_edited_text_bypasses_generation(tmp_path: Path) -> None:
    mode = _make_mode(tmp_path)
    dest = tmp_path / "quest.lua"
    mode.save_hand_edited_text("-- totally custom\nlocal x = 1\n", path=dest)
    assert dest.read_text() == "-- totally custom\nlocal x = 1\n"
    assert mode.dirty is False


def test_has_visible_save_control_and_tooltips(tmp_path: Path) -> None:
    mode = _make_mode(tmp_path)
    assert mode.has_visible_save_control() is True
    assert mode.has_tooltip("view_edit_script")
