"""Unit tests for Data Editor (docs/components/13-editor-core-authoring.md
§2.4/§5.2/§7/§8)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from editor.modes import EditorContext, SpriteCache
from editor.modes.data_editor import DataEditorMode
from editor.project import Project

FIXTURE_PROJECT = Path(__file__).resolve().parents[1] / "fixtures" / "editor_projects" / "sample_project"


def _make_project(tmp_path: Path) -> Project:
    dest = tmp_path / "proj"
    shutil.copytree(FIXTURE_PROJECT, dest)
    return Project(dest)


def _make_context(project: Project) -> EditorContext:
    return EditorContext(get_project=lambda: project, navigate_to=lambda *a, **k: None, sprite_cache=SpriteCache())


def test_list_content_types_has_at_least_three_distinct_types(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    mode = DataEditorMode(_make_context(project))

    content_types = mode.list_content_types()

    assert {"monster", "item", "spell"} <= set(content_types)
    assert len(content_types) >= 3


def test_new_record_fills_defaults_from_schema_with_no_python_branching(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    mode = DataEditorMode(_make_context(project))

    record = mode.new_record("monster")

    assert record["stats"]["hp"] == 10
    assert record["stats"]["vision_range"] == 6
    assert record["ai"]["behavior"] == "chaser"
    assert record["loot_table"]["entries"] == []


def test_set_and_get_field_value_dotted_path_primary_authoring_action(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    mode = DataEditorMode(_make_context(project))
    mode.select_content_type("monster")

    mode.set_field_value("id", "new_monster")
    mode.set_field_value("stats.hp", 42)

    assert mode.get_field_value("id") == "new_monster"
    assert mode.get_field_value("stats.hp") == 42
    assert mode.dirty is True


def test_json_preview_reflects_form_edits_in_real_time(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    mode = DataEditorMode(_make_context(project))
    mode.select_content_type("monster")
    mode.set_field_value("id", "preview_check")

    preview = mode.json_preview()

    assert json.loads(preview)["id"] == "preview_check"


def test_effect_array_sub_editor_reused_across_two_content_types(tmp_path: Path) -> None:
    """Doc §2.4/§8: the effect-array sub-editor must be reused identically
    across at least two content-type forms."""
    project = _make_project(tmp_path)
    mode = DataEditorMode(_make_context(project))

    for content_type, key in (("spell", "effects"), ("item", "on_use_effects")):
        mode.select_content_type(content_type)
        mode.effect_array_add_row(key)
        mode.effect_array_set_cell(key, 0, "type", "burn")
        mode.effect_array_set_cell(key, 0, "value", "5")

        rows = mode.get_field_value(key)
        assert rows == [{"type": "burn", "value": "5"}]

        mode.effect_array_remove_row(key, 0)
        assert mode.get_field_value(key) == []


def test_effect_array_column_headers_are_visible_and_labeled(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    mode = DataEditorMode(_make_context(project))
    schema = mode.load_schema("spell")
    effects_field = next(f for f in schema["fields"] if f["key"] == "effects")

    headers = mode.effect_array_column_headers(effects_field)

    assert headers == ["Type", "Value"]


def test_resolve_options_literal_list(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    mode = DataEditorMode(_make_context(project))
    schema = mode.load_schema("monster")
    ai_field = next(f for f in schema["fields"] if f["key"] == "ai.behavior")

    options = mode.resolve_options(ai_field)

    assert options == ["chaser", "ambusher", "patroller", "coward"]


def test_resolve_options_from_registry_namespace(tmp_path: Path) -> None:
    """New content type's dropdown needs zero new Python code -- the
    dropdown's options are read live from the registry namespace (doc
    §5.2)."""
    project = _make_project(tmp_path)
    mode = DataEditorMode(_make_context(project))

    options = mode.resolve_options({"options_from_namespace": "items"})

    assert "sword" in options


def test_id_picker_or_typed_accepts_typed_input_not_only_picker_selection(tmp_path: Path) -> None:
    """Doc §7: ID fields must accept typed input for an ID that doesn't
    exist yet, in addition to picking from an existing list."""
    project = _make_project(tmp_path)
    mode = DataEditorMode(_make_context(project))
    mode.select_content_type("monster")

    # "brand_new_item_id" is not in the items namespace -- a typed value is
    # still accepted (this widget never restricts to the options list).
    mode.effect_array_add_row("loot_table.entries")
    mode.effect_array_set_cell("loot_table.entries", 0, "item_id", "brand_new_item_id")

    assert mode.get_field_value("loot_table.entries")[0]["item_id"] == "brand_new_item_id"


def test_save_current_record_writes_file_named_by_id(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    mode = DataEditorMode(_make_context(project))
    mode.select_content_type("monster")
    mode.set_field_value("id", "kobold")

    dest = mode.save_current_record()

    assert dest == project.data_path("entities", "kobold.json")
    saved = json.loads(dest.read_text())
    assert saved["id"] == "kobold"
    assert mode.dirty is False


def test_save_current_record_without_id_raises(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    mode = DataEditorMode(_make_context(project))
    mode.select_content_type("monster")
    mode.set_field_value("id", "")

    with pytest.raises(ValueError):
        mode.save_current_record()


def test_has_visible_save_control_and_tooltips(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    mode = DataEditorMode(_make_context(project))
    assert mode.has_visible_save_control() is True
    assert mode.has_tooltip("id_field_typed_input")
