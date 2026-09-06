"""Unit tests for Sprite Editor (docs/components/13-editor-core-authoring.md
§2.4/§7/§8)."""

from __future__ import annotations

from pathlib import Path

import pygame
import pytest

from editor.modes import EditorContext, SpriteCache
from editor.modes.sprite_editor import SpriteEditorMode
from editor.project import Project


def _make_context(project: Project, sprite_cache: SpriteCache | None = None) -> EditorContext:
    return EditorContext(
        get_project=lambda: project,
        navigate_to=lambda *a, **k: None,
        sprite_cache=sprite_cache or SpriteCache(),
    )


def test_paint_pixel_primary_authoring_action(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = SpriteEditorMode(_make_context(project))
    mode.new_sprite(8, 8)

    mode.paint_pixel(2, 2, (255, 0, 0, 255))

    assert mode.get_pixel(2, 2) == (255, 0, 0, 255)
    assert mode.dirty is True


def test_square_brush_paints_a_square_footprint(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = SpriteEditorMode(_make_context(project))
    mode.new_sprite(8, 8)
    mode.set_brush(size=3, shape="square")

    mode.paint_pixel(4, 4, (10, 20, 30, 255))

    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            assert mode.get_pixel(4 + dx, 4 + dy) == (10, 20, 30, 255)
    assert mode.get_pixel(4 - 2, 4) is None


def test_round_brush_excludes_square_corners(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = SpriteEditorMode(_make_context(project))
    mode.new_sprite(16, 16)
    mode.set_brush(size=5, shape="round")

    mode.paint_pixel(8, 8, (1, 2, 3, 255))

    # Center is always painted.
    assert mode.get_pixel(8, 8) is not None
    # A round brush paints a strict subset of the equivalent square brush.
    mode2 = SpriteEditorMode(_make_context(project))
    mode2.new_sprite(16, 16)
    mode2.set_brush(size=5, shape="square")
    mode2.paint_pixel(8, 8, (1, 2, 3, 255))
    round_pixels = {pos for pos, color in mode.frames[0].items() if color}
    square_pixels = {pos for pos, color in mode2.frames[0].items() if color}
    assert round_pixels < square_pixels


def test_mirror_horizontal_paints_both_sides(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = SpriteEditorMode(_make_context(project))
    mode.new_sprite(10, 10)
    mode.set_mirror("horizontal")

    mode.paint_pixel(1, 5, (5, 5, 5, 255))

    assert mode.get_pixel(1, 5) == (5, 5, 5, 255)
    assert mode.get_pixel(10 - 1 - 1, 5) == (5, 5, 5, 255)


def test_mirror_vertical_paints_both_sides(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = SpriteEditorMode(_make_context(project))
    mode.new_sprite(10, 10)
    mode.set_mirror("vertical")

    mode.paint_pixel(5, 1, (9, 9, 9, 255))

    assert mode.get_pixel(5, 1) == (9, 9, 9, 255)
    assert mode.get_pixel(5, 10 - 1 - 1) == (9, 9, 9, 255)


def test_invalid_brush_shape_and_mirror_mode_rejected(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = SpriteEditorMode(_make_context(project))
    with pytest.raises(ValueError):
        mode.set_brush(size=1, shape="triangle")
    with pytest.raises(ValueError):
        mode.set_mirror("diagonal")


def test_sheet_mode_frames_and_onion_skin(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = SpriteEditorMode(_make_context(project))
    mode.new_sprite(4, 4)
    mode.paint_pixel(0, 0, (1, 1, 1, 255))

    second_frame = mode.add_frame()
    assert mode.frame_count == 2
    assert second_frame == 1

    mode.set_onion_skin(True)
    onion = mode.onion_skin_frame()
    assert onion is mode.frames[0]
    assert onion[(0, 0)] == (1, 1, 1, 255)


def test_save_writes_png_and_invalidates_sprite_cache(tmp_path: Path) -> None:
    """Doc §7: saving a sprite must invalidate any cached preview shown
    elsewhere without requiring an app restart."""
    project = Project.new(tmp_path / "p")
    cache = SpriteCache()
    dest = project.assets_path("sprites", "goblin.png")
    cache.put(dest, pygame.Surface((1, 1)))
    assert cache.get(dest) is not None

    mode = SpriteEditorMode(_make_context(project, cache))
    mode.new_sprite(4, 4)
    mode.paint_pixel(0, 0, (200, 50, 50, 255))

    saved_path = mode.save("sprites/goblin.png")

    assert saved_path == dest
    assert dest.exists()
    assert cache.get(dest) is None  # invalidated
    assert mode.dirty is False


def test_load_sprite_round_trips_a_saved_png(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = SpriteEditorMode(_make_context(project))
    mode.new_sprite(4, 4)
    mode.paint_pixel(1, 1, (11, 22, 33, 255))
    mode.save("sprites/roundtrip.png")

    mode2 = SpriteEditorMode(_make_context(project))
    mode2.load_sprite("sprites/roundtrip.png")

    assert mode2.width == 4
    assert mode2.height == 4
    assert mode2.get_pixel(1, 1) == (11, 22, 33, 255)


def test_receive_context_loads_sprite_from_navigation_payload(tmp_path: Path) -> None:
    """Doc §2.7 context link: Map Editor's palette right-click hands the
    asset path here."""
    project = Project.new(tmp_path / "p")
    mode = SpriteEditorMode(_make_context(project))
    mode.new_sprite(4, 4)
    mode.paint_pixel(0, 0, (7, 7, 7, 255))
    mode.save("sprites/from_map_editor.png")

    fresh_mode = SpriteEditorMode(_make_context(project))
    fresh_mode.receive_context({"asset_path": "sprites/from_map_editor.png"})

    assert fresh_mode.get_pixel(0, 0) == (7, 7, 7, 255)


def test_has_visible_save_control_and_tooltips(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = SpriteEditorMode(_make_context(project))
    assert mode.has_visible_save_control() is True
    assert mode.has_tooltip("mirror_tool")
    assert mode.has_tooltip("brush_size")
