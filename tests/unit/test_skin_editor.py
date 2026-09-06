"""Unit tests for Skin Editor (docs/components/13-editor-core-authoring.md
§2.4/§7/§8/§10)."""

from __future__ import annotations

import json
from pathlib import Path

import pygame
import pytest

from editor.modes import EditorContext, SpriteCache
from editor.modes.skin_editor import SkinEditorMode
from editor.project import Project
from engine.core.schemas.ui_skin_schema import WidgetTreeError


def _make_context(project: Project) -> EditorContext:
    return EditorContext(get_project=lambda: project, navigate_to=lambda *a, **k: None, sprite_cache=SpriteCache())


def test_new_screen_and_add_node_primary_authoring_action(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = SkinEditorMode(_make_context(project))

    mode.new_screen("inventory")
    mode.add_node(
        (),
        {"type": "Label", "text": "Inventory", "rect": {"x": 0, "y": 0, "w": 100, "h": 20}},
    )

    assert mode.current_tree["children"][0]["text"] == "Inventory"
    assert mode.dirty is True


def test_set_field_and_get_node_by_path(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = SkinEditorMode(_make_context(project))
    mode.new_screen("inventory")
    mode.add_node((), {"type": "Label", "text": "A", "rect": {"x": 0, "y": 0, "w": 10, "h": 10}})

    mode.set_field((0,), "text", "B")

    assert mode.get_node((0,))["text"] == "B"


def test_remove_node(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = SkinEditorMode(_make_context(project))
    mode.new_screen("inventory")
    mode.add_node((), {"type": "Label", "text": "A", "rect": {"x": 0, "y": 0, "w": 10, "h": 10}})

    mode.remove_node((0,))

    assert mode.current_tree["children"] == []


def test_validate_matches_the_exact_widget_tree_schema(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = SkinEditorMode(_make_context(project))
    mode.new_screen("inventory")
    mode.add_node((), {"type": "NotARealType"})

    with pytest.raises(WidgetTreeError):
        mode.validate()


def test_render_preview_uses_the_real_ui_runtime_draw_path(tmp_path: Path) -> None:
    """Doc §2.4's one deliberate exception: preview renders via the real
    engine.ui.ui_runtime path, not a reimplementation."""
    project = Project.new(tmp_path / "p")
    mode = SkinEditorMode(_make_context(project))
    mode.new_screen("inventory")
    mode.add_node((), {"type": "Label", "text": "Preview Me", "rect": {"x": 0, "y": 0, "w": 100, "h": 20}})

    surface = pygame.Surface((200, 200))
    mode.render_preview(surface)  # must not raise


def test_save_screen_writes_into_project_ui_skin_json(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = SkinEditorMode(_make_context(project))
    mode.new_screen("inventory")

    dest = mode.save_screen()

    assert dest == project.data_path("config", "ui_skin.json")
    saved = json.loads(dest.read_text())
    assert saved["screens"]["inventory"]["type"] == "Panel"
    assert mode.dirty is False


def test_save_screen_preserves_other_already_saved_screens(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = SkinEditorMode(_make_context(project))
    mode.new_screen("inventory")
    mode.save_screen()

    mode2 = SkinEditorMode(_make_context(project))
    mode2.new_screen("spellbook")
    mode2.save_screen()

    saved = json.loads(project.data_path("config", "ui_skin.json").read_text())
    assert set(saved["screens"].keys()) == {"inventory", "spellbook"}


def test_color_picker_get_set_and_save_ui_config(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = SkinEditorMode(_make_context(project))

    assert mode.get_color("panel_bg") == (30, 30, 40)
    mode.set_color("hp_red", (255, 0, 0))

    dest = mode.save_ui_config()

    saved = json.loads(dest.read_text())
    assert saved["colors"]["hp_red"] == [255, 0, 0]


def test_has_visible_save_control_and_tooltips(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = SkinEditorMode(_make_context(project))
    assert mode.has_visible_save_control() is True
    assert mode.has_tooltip("preview_pane")
