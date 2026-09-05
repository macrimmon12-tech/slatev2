"""Unit tests for engine/modding/archive.py (12-modding-archive-system.md
§8). Fixture archives are built at test time via ``zipfile`` rather than
committed as binary blobs, so they stay reviewable and don't bloat the
repo (this component's doc §6)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from engine.modding.archive import ArchiveSource, ScopedSource, open_archive, write_archive


def _build_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, mode="w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


FILES = {
    "data/entities/monsters/goblin.json": b'{"id": "goblin", "hp": 10}',
    "assets/sprites/goblin.png": b"not-really-a-png",
}


def test_pak_and_pkd_read_identically(tmp_path):
    pak_path = tmp_path / "mod.pak"
    pkd_path = tmp_path / "mod.pkd"
    _build_zip(pak_path, FILES)
    _build_zip(pkd_path, FILES)

    pak_source = open_archive(pak_path)
    pkd_source = open_archive(pkd_path)

    assert pak_source.list_files() == pkd_source.list_files() == sorted(FILES)
    for relative_path in FILES:
        assert pak_source.read(relative_path) == pkd_source.read(relative_path) == FILES[relative_path]
        assert pak_source.exists(relative_path)
        assert pkd_source.exists(relative_path)


def test_open_archive_returns_archive_source(tmp_path):
    path = tmp_path / "content.pak"
    _build_zip(path, FILES)
    source = open_archive(path)
    assert isinstance(source, ArchiveSource)


def test_exists_false_for_missing_file(tmp_path):
    path = tmp_path / "content.pak"
    _build_zip(path, FILES)
    source = open_archive(path)
    assert not source.exists("data/entities/monsters/does_not_exist.json")


def test_read_missing_file_raises(tmp_path):
    path = tmp_path / "content.pak"
    _build_zip(path, FILES)
    source = open_archive(path)
    with pytest.raises(KeyError):
        source.read("data/entities/monsters/does_not_exist.json")


def test_directory_entries_excluded_from_list_files(tmp_path):
    path = tmp_path / "content.pak"
    with zipfile.ZipFile(path, mode="w") as zf:
        zf.writestr("data/entities/", b"")  # explicit directory entry
        zf.writestr("data/entities/monsters/goblin.json", b'{"id": "goblin"}')
    source = open_archive(path)
    assert source.list_files() == ["data/entities/monsters/goblin.json"]


def test_write_archive_round_trips_through_open_archive(tmp_path):
    source_dir = tmp_path / "src"
    (source_dir / "data" / "entities").mkdir(parents=True)
    (source_dir / "data" / "entities" / "goblin.json").write_text('{"id": "goblin"}')

    archive_path = tmp_path / "packed.pak"
    write_archive(archive_path, source_dir)

    source = open_archive(archive_path)
    assert source.list_files() == ["data/entities/goblin.json"]
    assert source.read("data/entities/goblin.json") == b'{"id": "goblin"}'


def test_scoped_source_strips_prefix(tmp_path):
    path = tmp_path / "content.pak"
    _build_zip(path, FILES)
    inner = open_archive(path)
    scoped = ScopedSource(inner, prefix="data")

    assert scoped.list_files() == ["entities/monsters/goblin.json"]
    assert scoped.read("entities/monsters/goblin.json") == FILES["data/entities/monsters/goblin.json"]
    assert scoped.exists("entities/monsters/goblin.json")
    assert not scoped.exists("sprites/goblin.png")  # under assets/, not data/


def test_scoped_source_excludes_files_outside_prefix(tmp_path):
    path = tmp_path / "content.pak"
    _build_zip(path, FILES)
    scoped = ScopedSource(open_archive(path), prefix="data")
    assert "sprites/goblin.png" not in scoped.list_files()
