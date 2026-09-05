"""Unit tests for Sprite Manager (docs/components/13-editor-core-authoring.md
§2.6/§8)."""

from __future__ import annotations

import json
from pathlib import Path

from editor.modes import EditorContext, SpriteCache
from editor.modes.sprite_manager import WALL_VARIANT_SLOTS, SpriteManagerMode
from editor.project import Project


def _make_context(project: Project, sprite_cache: SpriteCache | None = None) -> EditorContext:
    return EditorContext(
        get_project=lambda: project,
        navigate_to=lambda *a, **k: None,
        sprite_cache=sprite_cache or SpriteCache(),
    )


def test_scan_gaps_finds_missing_sprite_ref(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    project.data_path("entities").mkdir(parents=True, exist_ok=True)
    entity_path = project.data_path("entities", "goblin.json")
    entity_path.write_text(json.dumps({"id": "goblin", "sprite_ref": "sprites/goblin.png"}))

    mode = SpriteManagerMode(_make_context(project))
    gaps = mode.scan_gaps()

    assert len(gaps) == 1
    assert gaps[0].sprite_ref == "sprites/goblin.png"
    assert gaps[0].field_path == "sprite_ref"
    assert gaps[0].json_path == entity_path


def test_scan_gaps_ignores_sprite_ref_that_exists(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    project.data_path("entities").mkdir(parents=True, exist_ok=True)
    project.data_path("entities", "goblin.json").write_text(
        json.dumps({"id": "goblin", "sprite_ref": "sprites/goblin.png"})
    )
    project.assets_path("sprites", "goblin.png").write_bytes(b"fake-png")

    mode = SpriteManagerMode(_make_context(project))
    assert mode.scan_gaps() == []


def test_import_and_link_back_writes_sprite_ref_onto_source_json(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    project.data_path("entities").mkdir(parents=True, exist_ok=True)
    entity_path = project.data_path("entities", "goblin.json")
    entity_path.write_text(json.dumps({"id": "goblin"}))

    source_png = tmp_path / "incoming.png"
    source_png.write_bytes(b"fake-png-bytes")

    cache = SpriteCache()
    mode = SpriteManagerMode(_make_context(project, cache))
    dest = mode.import_and_link(source_png, "sprites/goblin.png", entity_path, "sprite_ref")

    assert dest.read_bytes() == b"fake-png-bytes"
    saved = json.loads(entity_path.read_text())
    assert saved["sprite_ref"] == "sprites/goblin.png"
    assert cache.version(dest) >= 1


def test_all_16_wall_variant_slots_present_and_independently_assignable(tmp_path: Path) -> None:
    """Doc §2.6/§8: exactly the 16 named wall_0..wall_15 slots, and
    assigning one must not affect any other."""
    project = Project.new(tmp_path / "p")
    mode = SpriteManagerMode(_make_context(project))

    assert WALL_VARIANT_SLOTS == [f"wall_{i}" for i in range(16)]

    theme = mode.load_theme("sprites/theme.json")
    for i in range(16):
        assert mode.get_wall_variant(theme, i) is None

    mode.set_wall_variant(theme, 3, "tiles/wall_3.png")

    assert mode.get_wall_variant(theme, 3) == "tiles/wall_3.png"
    for i in range(16):
        if i != 3:
            assert mode.get_wall_variant(theme, i) is None

    dest = mode.save_theme(theme, "sprites/theme.json")
    saved = json.loads(dest.read_text())
    assert saved["wall_variants"]["wall_3"] == "tiles/wall_3.png"
    assert saved["wall_variants"]["wall_0"] is None
