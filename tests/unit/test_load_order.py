"""Unit tests for engine/modding/load_order.py (12-modding-archive-system.md
§8, §9)."""

from __future__ import annotations

import zipfile
from pathlib import Path

from engine.core.registry import DataRegistry, DirectorySource, DuplicateIdError
from engine.modding.archive import ScopedSource
from engine.modding.load_order import parse_load_order, resolve_source_list


def _write_json(directory: Path, relative_path: str, payload: str) -> None:
    full_path = directory / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(payload, encoding="utf-8")


def _build_pak(path: Path, files: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, mode="w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


# ---------------------------------------------------------------------------
# parse_load_order
# ---------------------------------------------------------------------------


def test_missing_load_order_file_returns_empty_list(tmp_path):
    assert parse_load_order(tmp_path / "does_not_exist.txt", tmp_path / "mods") == []


def test_empty_load_order_file_returns_empty_list(tmp_path):
    load_order_path = tmp_path / "load_order.txt"
    load_order_path.write_text("# just a comment\n\n")
    assert parse_load_order(load_order_path, tmp_path / "mods") == []


def test_parse_load_order_skips_blanks_and_comments_and_orders_top_to_bottom(tmp_path):
    load_order_path = tmp_path / "load_order.txt"
    load_order_path.write_text(
        "# header comment\n"
        "\n"
        "balance_tweaks.pak\n"
        "   \n"
        "# another comment\n"
        "new_monsters.pak\n"
    )
    mods_dir = tmp_path / "mods"
    result = parse_load_order(load_order_path, mods_dir)
    assert result == [mods_dir / "balance_tweaks.pak", mods_dir / "new_monsters.pak"]


# ---------------------------------------------------------------------------
# resolve_source_list
# ---------------------------------------------------------------------------


def test_resolve_source_list_with_nothing_present_is_just_loose_dirs(tmp_path):
    loose_dir = tmp_path / "loose"
    loose_dir.mkdir()
    tiers = resolve_source_list(base_archive_dir=None, mods_dir=tmp_path / "mods", loose_dirs=[loose_dir])
    assert len(tiers) == 1
    assert isinstance(tiers[0][0], DirectorySource)
    assert tiers[0][0].root == loose_dir


def test_resolve_source_list_order_matches_contracts_priority(tmp_path):
    base_dir = tmp_path / "base_archives"
    _build_pak(base_dir / "base.pak", {"data/entities/goblin.json": '{"id": "goblin", "hp": 5}'})

    mods_dir = tmp_path / "mods"
    _build_pak(mods_dir / "mod_a.pak", {"data/entities/orc.json": '{"id": "orc"}'})
    (mods_dir / "load_order.txt").write_text("mod_a.pak\n")

    loose_dir = tmp_path / "loose"
    loose_dir.mkdir()

    tiers = resolve_source_list(base_archive_dir=base_dir, mods_dir=mods_dir, loose_dirs=[loose_dir])

    # base archives tier, then one tier per load_order.txt line, then one
    # tier per loose dir -- three tiers total here.
    assert len(tiers) == 3
    assert isinstance(tiers[0][0], ScopedSource)
    assert isinstance(tiers[1][0], ScopedSource)
    assert isinstance(tiers[2][0], DirectorySource)


def test_unpacked_mod_folder_resolves_as_loose_directory_tier(tmp_path):
    mods_dir = tmp_path / "mods"
    _write_json(mods_dir / "dev_mod" / "data", "entities/troll.json", '{"id": "troll"}')
    (mods_dir / "load_order.txt").write_text("dev_mod\n")

    tiers = resolve_source_list(base_archive_dir=None, mods_dir=mods_dir, loose_dirs=[])
    assert len(tiers) == 1
    (source,) = tiers[0]
    assert isinstance(source, DirectorySource)
    assert source.root == mods_dir / "dev_mod" / "data"
    assert "entities/troll.json" in source.list_files()


def test_unresolvable_load_order_entry_is_skipped_not_fatal(tmp_path):
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    (mods_dir / "load_order.txt").write_text("does_not_exist_anywhere\n")

    # Absence = zero cost -- must not raise.
    tiers = resolve_source_list(base_archive_dir=None, mods_dir=mods_dir, loose_dirs=[])
    assert tiers == []


def test_base_archives_share_one_priority_tier_and_are_sorted(tmp_path):
    base_dir = tmp_path / "base_archives"
    _build_pak(base_dir / "a_base.pak", {"data/entities/goblin.json": '{"id": "goblin"}'})
    _build_pak(base_dir / "b_base.pak", {"data/entities/orc.json": '{"id": "orc"}'})

    tiers = resolve_source_list(base_archive_dir=base_dir, mods_dir=tmp_path / "mods", loose_dirs=[])
    assert len(tiers) == 1
    assert len(tiers[0]) == 2


# ---------------------------------------------------------------------------
# resolve_source_list feeding DataRegistry.load_sources -- DoD bullets
# ---------------------------------------------------------------------------


def test_same_id_different_priority_loose_wins_even_when_archive_listed_last(tmp_path):
    base_dir = tmp_path / "base_archives"
    _build_pak(base_dir / "base.pak", {"data/entities/goblin.json": '{"id": "goblin", "hp": 5}'})

    mods_dir = tmp_path / "mods"
    _build_pak(mods_dir / "mod_a.pak", {"data/entities/goblin.json": '{"id": "goblin", "hp": 50}'})
    (mods_dir / "load_order.txt").write_text("mod_a.pak\n")

    loose_dir = tmp_path / "loose"
    _write_json(loose_dir, "entities/goblin.json", '{"id": "goblin", "hp": 999}')

    tiers = resolve_source_list(base_archive_dir=base_dir, mods_dir=mods_dir, loose_dirs=[loose_dir])

    registry = DataRegistry()
    registry.load_sources(tiers)
    assert registry.get("entities", "goblin")["hp"] == 999


def test_same_id_same_priority_across_two_base_archives_raises(tmp_path):
    base_dir = tmp_path / "base_archives"
    _build_pak(base_dir / "a.pak", {"data/entities/goblin.json": '{"id": "goblin", "hp": 1}'})
    _build_pak(base_dir / "b.pak", {"data/entities/goblin.json": '{"id": "goblin", "hp": 2}'})

    tiers = resolve_source_list(base_archive_dir=base_dir, mods_dir=tmp_path / "mods", loose_dirs=[])

    registry = DataRegistry()
    try:
        registry.load_sources(tiers)
    except DuplicateIdError as exc:
        assert "goblin" in str(exc)
    else:
        raise AssertionError("expected DuplicateIdError for two base archives at the same priority")
