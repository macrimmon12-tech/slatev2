"""Unit tests for ``editor/project.py`` (docs/components/
13-editor-core-authoring.md §2.2/§8)."""

from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

import pytest

from editor.project import Project


def test_new_creates_directory_skeleton_and_blank_map(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "myproj")

    assert (project.root / "data" / "maps").is_dir()
    assert (project.root / "data" / "entities" / "npcs").is_dir()
    assert (project.root / "assets" / "sprites").is_dir()
    assert (project.root / "assets" / "sfx").is_dir()
    assert (project.root / "assets" / "music").is_dir()

    project_json = json.loads((project.root / "project.json").read_text())
    assert project_json["schema_version"] == 1
    assert project_json["name"] == "myproj"

    blank_map = json.loads(project.data_path("maps", "untitled.json").read_text())
    assert blank_map["id"] == "untitled"
    assert blank_map["width"] == 40
    assert blank_map["height"] == 25
    assert blank_map["entities"] == []
    assert len(blank_map["tiles"]) == 40 * 25
    assert all(tile["walkable"] for tile in blank_map["tiles"])


def test_new_seeds_a_default_16_slot_tileset(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "myproj")
    theme = json.loads(project.assets_path("sprites", "theme.json").read_text())
    assert set(theme["wall_variants"].keys()) == {f"wall_{i}" for i in range(16)}


def test_open_loose_project_folder_directly(tmp_path: Path) -> None:
    created = Project.new(tmp_path / "myproj")
    opened = Project.open(created.root)
    assert opened.root == created.root


def test_open_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Project.open(tmp_path / "does_not_exist")


def test_open_malformed_archive_raises(tmp_path: Path) -> None:
    # 12-modding-archive-system.md has merged, so opening a .pak/.pkd now
    # goes through the real ArchiveSource — a not-actually-a-zip file
    # raises from that real reader (zipfile.BadZipFile), not the
    # pre-merge ArchiveSupportUnavailable stub error this test used to
    # exercise. Per CONTRACTS.md §8: replace the stubbed-dependency test
    # with one against the real thing once it merges, rather than keep
    # asserting a premise ("12 hasn't merged") that's no longer true.
    fake_pak = tmp_path / "mod.pak"
    fake_pak.write_bytes(b"not a real archive")
    with pytest.raises(Exception):
        Project.open(fake_pak)


def test_open_real_archive_round_trips_through_pack(tmp_path: Path) -> None:
    """Real integration with 12-modding-archive-system.md: pack a project
    to .pak, then open that .pak back up and confirm its content survived
    the round trip."""
    project = Project.new(tmp_path / "myproj")
    dist_dir = tmp_path / "dist"
    pak_path, _pkd_path = project.pack(dist_dir)

    reopened = Project.open(pak_path)
    blank_map = json.loads(reopened.data_path("maps", "untitled.json").read_text())
    assert blank_map["id"] == "untitled"


def test_data_path_and_assets_path_helpers(tmp_path: Path) -> None:
    project = Project(tmp_path / "root")
    assert project.data_path("entities", "goblin.json") == tmp_path / "root" / "data" / "entities" / "goblin.json"
    assert project.assets_path("sprites", "a.png") == tmp_path / "root" / "assets" / "sprites" / "a.png"


def test_validate_flags_malformed_json_and_missing_id(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "myproj")
    bad = project.data_path("entities", "broken.json")
    bad.write_text("{not valid json")
    no_id = project.data_path("items", "no_id.json")
    no_id.write_text(json.dumps({"display_name": "Nameless"}))

    warnings = project.validate()
    joined = "\n".join(warnings)
    assert "broken.json" in joined
    assert "no_id.json" in joined


def test_pack_writes_pak_and_pkd_without_touching_loose_source(tmp_path: Path) -> None:
    project = Project.new(tmp_path / "myproj")
    map_path = project.data_path("maps", "untitled.json")
    before_mtime = map_path.stat().st_mtime
    before_content = map_path.read_bytes()

    time.sleep(0.01)  # ensure a would-be mtime change is actually detectable
    dist_dir = tmp_path / "dist"
    pak_path, pkd_path = project.pack(dist_dir)

    assert pak_path.parent == dist_dir
    assert pkd_path.parent == dist_dir
    assert pak_path.exists()
    assert pkd_path.exists()

    after_mtime = map_path.stat().st_mtime
    after_content = map_path.read_bytes()
    assert before_mtime == after_mtime
    assert before_content == after_content

    for archive_path in (pak_path, pkd_path):
        with zipfile.ZipFile(archive_path) as zf:
            assert zf.testzip() is None
            names = zf.namelist()
            assert any(name.endswith("untitled.json") for name in names)
