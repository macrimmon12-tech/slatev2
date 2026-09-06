"""Round-trip/validation tests for the ``<id>.layout.json`` sidecar schema
(``docs/components/14-editor-visual-quest-dialog.md`` §5.1, CONTRACTS.md
§9's "every new JSON schema ships a validator" rule)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from editor.modes import EditorContext, SpriteCache
from editor.modes.dialog_editor import DialogEditorMode
from editor.project import Project
from engine.core.schemas.dialog_layout_schema import DialogLayoutSchemaError, validate_dialog_layout

FIXTURE_DIALOG = Path(__file__).resolve().parents[1] / "fixtures" / "dialog_editor" / "shopkeeper_mira_dialog.json"


def test_valid_layout_passes() -> None:
    validate_dialog_layout(
        {
            "schema_version": 1,
            "node_positions": {
                "greet": {"x": 80, "y": 120},
                "lore_1": {"x": 340, "y": 120},
            },
        }
    )


def test_missing_schema_version_fails() -> None:
    with pytest.raises(DialogLayoutSchemaError):
        validate_dialog_layout({"node_positions": {}})


def test_non_object_node_positions_fails() -> None:
    with pytest.raises(DialogLayoutSchemaError):
        validate_dialog_layout({"schema_version": 1, "node_positions": []})


def test_non_integer_position_fails() -> None:
    with pytest.raises(DialogLayoutSchemaError):
        validate_dialog_layout({"schema_version": 1, "node_positions": {"greet": {"x": 1.5, "y": 0}}})


def test_missing_axis_fails() -> None:
    with pytest.raises(DialogLayoutSchemaError):
        validate_dialog_layout({"schema_version": 1, "node_positions": {"greet": {"x": 1}}})


def test_dialog_editor_actually_written_layout_file_validates(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "proj")
    dest = project.data_path("dialogs", "shopkeeper_mira_dialog.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE_DIALOG, dest)
    context = EditorContext(get_project=lambda: project, navigate_to=lambda *a, **k: None, sprite_cache=SpriteCache())
    mode = DialogEditorMode(context)
    mode.load("shopkeeper_mira_dialog")

    mode.save()

    layout_path = project.data_path("dialogs", "shopkeeper_mira_dialog.layout.json")
    validate_dialog_layout(json.loads(layout_path.read_text()))
