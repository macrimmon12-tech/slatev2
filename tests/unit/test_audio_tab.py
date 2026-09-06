"""Unit tests for the Audio tab (docs/components/13-editor-core-authoring.md
§2.6/§8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from editor.modes import EditorContext, SpriteCache
from editor.modes.audio_tab import AudioTabMode
from editor.project import Project


def _make_context(project: Project) -> EditorContext:
    return EditorContext(get_project=lambda: project, navigate_to=lambda *a, **k: None, sprite_cache=SpriteCache())


def test_import_file_organizes_into_sfx(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    source = tmp_path / "incoming.ogg"
    source.write_bytes(b"fake-audio")

    mode = AudioTabMode(_make_context(project))
    dest = mode.import_file(source, "sfx")

    assert dest == project.assets_path("sfx", "incoming.ogg")
    assert dest.read_bytes() == b"fake-audio"


def test_import_file_organizes_into_music_with_custom_name(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    source = tmp_path / "incoming.ogg"
    source.write_bytes(b"fake-music")

    mode = AudioTabMode(_make_context(project))
    dest = mode.import_file(source, "music", dest_name="theme.ogg")

    assert dest == project.assets_path("music", "theme.ogg")


def test_import_file_rejects_invalid_kind(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = AudioTabMode(_make_context(project))
    with pytest.raises(ValueError):
        mode.import_file(tmp_path / "x.ogg", "video")


def test_organize_creates_sub_folder(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = AudioTabMode(_make_context(project))

    folder = mode.organize("sfx", "monsters")

    assert folder == project.assets_path("sfx", "monsters")
    assert folder.is_dir()


def test_link_event_updates_audio_config(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = AudioTabMode(_make_context(project))

    dest = mode.link_event("damage_dealt", "sfx/hit_custom.ogg")

    saved = json.loads(dest.read_text())
    assert saved["event_sounds"]["damage_dealt"]["default"] == "sfx/hit_custom.ogg"


def test_has_visible_save_control_and_tooltips(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "p")
    mode = AudioTabMode(_make_context(project))
    assert mode.has_visible_save_control() is True
    assert mode.has_tooltip("import_button")
