"""Unit tests for Map Editor (docs/components/13-editor-core-authoring.md
§2.4/§7/§8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from editor.modes import EditorContext, SpriteCache
from editor.modes.map_editor import ROOM_ID_MISSING_WARNING, MapEditorMode
from editor.project import Project


def _make_context(project: Project | None, navigate_log: list | None = None) -> EditorContext:
    navigate_log = navigate_log if navigate_log is not None else []

    def navigate_to(mode_id: str, context: dict | None) -> None:
        navigate_log.append((mode_id, context))

    return EditorContext(get_project=lambda: project, navigate_to=navigate_to, sprite_cache=SpriteCache())


def test_paint_tile_primary_authoring_action(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = MapEditorMode(_make_context(project))

    mode.paint_tile(3, 4, walkable=False, sprite_ref="tiles/wall.png")

    tile = mode.get_tile(3, 4)
    assert tile is not None
    assert tile["walkable"] is False
    assert tile["sprite_ref"] == "tiles/wall.png"
    assert mode.dirty is True


def test_place_entity_with_known_content_id_does_not_navigate(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    project.data_path("entities").mkdir(parents=True, exist_ok=True)
    (project.data_path("entities", "goblin.json")).write_text(json.dumps({"id": "goblin"}))

    navigate_log: list = []
    mode = MapEditorMode(_make_context(project, navigate_log))

    mode.place_entity(1, 1, "entities", "goblin")

    assert navigate_log == []
    assert len(mode.current_map["entities"]) == 1


def test_place_entity_with_unknown_content_id_navigates_to_data_editor_prefilled(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    navigate_log: list = []
    mode = MapEditorMode(_make_context(project, navigate_log))

    mode.place_entity(2, 2, "entities", "brand_new_monster")

    assert len(navigate_log) == 1
    mode_id, context = navigate_log[0]
    assert mode_id == "data_editor"
    assert context["content_type"] == "monster"
    assert context["new_record"]["id"] == "brand_new_monster"
    # The entity is still placed on the map even though its definition
    # doesn't exist yet -- the context link is additive, not blocking.
    assert len(mode.current_map["entities"]) == 1


def test_validate_map_warns_no_stairs_and_no_completion_trigger(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = MapEditorMode(_make_context(project))
    mode.current_map["tiles"] = [{"x": 0, "y": 0, "walkable": True, "room_id": "r_00"}]

    warnings = mode.validate_map()

    assert "No stairs found on this map." in warnings
    assert "No completion trigger found on this map." in warnings


def test_validate_map_warns_duplicate_entity_instance_ids(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = MapEditorMode(_make_context(project))
    mode.current_map["entities"] = [
        {"instance_id": "dup", "namespace": "entities", "content_id": "goblin", "position": [0, 0]},
        {"instance_id": "dup", "namespace": "entities", "content_id": "goblin", "position": [1, 1]},
    ]

    warnings = mode.validate_map()

    assert any("dup" in w for w in warnings)


def test_validate_map_exact_text_room_id_warning_when_absent(tmp_path: Path) -> None:
    """Doc §2.4/§7's required exact-text warning -- verbatim, not a
    paraphrase, so it stays greppable/testable."""
    project = Project.new(tmp_path / "p")
    mode = MapEditorMode(_make_context(project))
    # New Project's blank map has no room_id on any tile by construction.
    mode.load_map("untitled")

    warnings = mode.validate_map()

    assert ROOM_ID_MISSING_WARNING in warnings
    assert ROOM_ID_MISSING_WARNING == "this map has no room_id data — noise attenuation will use radius only"


def test_validate_map_no_room_id_warning_once_room_id_present(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = MapEditorMode(_make_context(project))
    for tile in mode.current_map["tiles"]:
        tile["room_id"] = "r_00"

    warnings = mode.validate_map()

    assert ROOM_ID_MISSING_WARNING not in warnings


def test_save_map_writes_to_project_maps_directory(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = MapEditorMode(_make_context(project))
    mode.new_blank_map("floor2", width=5, height=5)

    dest = mode.save_map()

    assert dest == project.data_path("maps", "floor2.json")
    assert dest.exists()
    assert mode.dirty is False


def test_save_as_vault_writes_under_maps_vaults_using_same_toolset(tmp_path: Path) -> None:
    """Vaults are authored on the identical paint/place code path (doc
    §2.4) -- same in-memory map, different destination."""
    project = Project.new(tmp_path / "p")
    mode = MapEditorMode(_make_context(project))
    mode.new_blank_map("shrine", width=5, height=5)
    mode.paint_tile(0, 0, walkable=False)

    dest = mode.save_as_vault("shrine_vault")

    assert dest == project.data_path("maps", "vaults", "shrine_vault.json")
    saved = json.loads(dest.read_text())
    assert saved["id"] == "shrine_vault"
    assert saved["width"] == 5


def test_campaign_authoring_reorder_and_hub_flags(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = MapEditorMode(_make_context(project))

    mode.new_campaign("keep")
    mode.campaign_add_level({"id": "level_a", "depth": 1})
    mode.campaign_add_level({"id": "level_b", "depth": 2})
    mode.campaign_add_level({"id": "level_hub", "depth": 1, "is_hub": True})

    mode.campaign_move_level(2, 0)  # drag the hub to the front
    assert [lvl["id"] for lvl in mode.current_campaign["levels"]] == ["level_hub", "level_a", "level_b"]

    mode.campaign_set_hub(1, True)
    assert mode.current_campaign["levels"][1]["is_hub"] is True

    dest = mode.save_campaign()
    assert dest == project.data_path("campaigns", "keep.json")
    saved = json.loads(dest.read_text())
    assert saved["levels"][0]["id"] == "level_hub"


def test_on_palette_right_click_navigates_to_sprite_editor(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    navigate_log: list = []
    mode = MapEditorMode(_make_context(project, navigate_log))

    mode.on_palette_right_click("sprites/wall_tile.png")

    assert navigate_log == [("sprite_editor", {"asset_path": "sprites/wall_tile.png"})]


def test_has_visible_save_control_and_tooltips_present() -> None:
    mode = MapEditorMode(_make_context(None))
    assert mode.has_visible_save_control() is True
    assert mode.has_tooltip("save_button")
    assert mode.has_tooltip("save_as_vault_button")
