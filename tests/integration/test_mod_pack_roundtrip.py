"""§4 of `15-integration-verification.md`: pack the vertical-slice content
via the real editor into `.pak`/`.pkd`, point a clean run at the packed
archive only (no loose files) and confirm it plays identically, then layer
a loose-file override and a second additive mod archive on top per
CONTRACTS.md §4's priority chain.

`12-modding-archive-system.md`'s own `tests/integration/test_archive_pipeline.py`
already proves the priority-chain plumbing itself against synthetic zip
fixtures built directly with ``zipfile``. This test instead drives the
*editor's* real `Editor.new_project`/`DataEditorMode`/`pack_project` entry
points end-to-end (13's own precedent: `tests/integration/
test_editor_smoke.py`), then boots the real `engine.main.Application`
against the packed output, per this component doc's own instruction to
exercise the editor itself as part of this gate, not just the archive
reader in isolation.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import pytest

from engine.main import Application
from engine.modding.archive import write_archive
from engine.systems import campaign as campaign_module
from engine.systems import combat as combat_module
from engine.systems import worldgen as worldgen_module


@pytest.fixture(scope="module", autouse=True)
def _pygame_headless():
    pygame.init()
    yield
    pygame.quit()


def _reset():
    worldgen_module.reset_module_state()
    campaign_module.set_data_registry(None)
    combat_module.set_data_registry(None)


def _author_vertical_slice_project(project_root: Path):
    from editor.editor import Editor
    from editor.modes.data_editor import DataEditorMode

    editor = Editor()
    editor.new_project(project_root)

    data_mode = editor.mode("data_editor")
    assert isinstance(data_mode, DataEditorMode)

    data_mode.select_content_type("monster")
    data_mode.set_field_value("id", "pack_test_goblin")
    data_mode.set_field_value("display_name", "Pack Test Goblin")
    data_mode.set_field_value("stats.hp", 12)
    data_mode.set_field_value("ai.behavior", "chaser")
    data_mode.save_current_record()

    data_mode.new_record_for_editing("item")
    data_mode.set_field_value("id", "pack_test_dagger")
    data_mode.set_field_value("display_name", "Pack Test Dagger")
    data_mode.save_current_record()

    return editor


def test_packed_archive_alone_plays_identically_to_the_loose_project(tmp_path):
    project_root = tmp_path / "vslice_project"
    editor = _author_vertical_slice_project(project_root)

    # Loose-project baseline: boot straight against the project's own
    # data/ folder, no archive involved at all.
    _reset()
    loose_app = Application(data_root=project_root / "data")
    loose_app.boot()
    loose_goblin = loose_app.registry.get("entities", "pack_test_goblin")
    assert loose_goblin is not None

    # Pack it via the real editor entry point.
    dist_dir = tmp_path / "dist"
    pak_path, pkd_path = editor.pack_project(dist_dir)
    assert pak_path.exists() and pkd_path.exists()

    # Point a clean run at *only* the packed archive -- an empty loose
    # data_root, no mods -- and confirm it plays identically.
    _reset()
    base_archive_dir = tmp_path / "base_archives"
    base_archive_dir.mkdir()
    (base_archive_dir / "base_content.pak").write_bytes(pak_path.read_bytes())

    empty_loose_dir = tmp_path / "empty_loose"
    empty_loose_dir.mkdir()

    packed_app = Application(data_root=empty_loose_dir, base_archive_dir=base_archive_dir)
    packed_app.boot()

    packed_goblin = packed_app.registry.get("entities", "pack_test_goblin")
    assert packed_goblin == loose_goblin
    assert packed_app.registry.get("items", "pack_test_dagger") is not None


def test_loose_override_wins_over_the_packed_archive(tmp_path):
    project_root = tmp_path / "vslice_project"
    editor = _author_vertical_slice_project(project_root)
    dist_dir = tmp_path / "dist"
    pak_path, _pkd_path = editor.pack_project(dist_dir)

    base_archive_dir = tmp_path / "base_archives"
    base_archive_dir.mkdir()
    (base_archive_dir / "base_content.pak").write_bytes(pak_path.read_bytes())

    # A loose-file override on top of the packed archive (a rebalance),
    # per §4's checklist -- must win per CONTRACTS.md §4's load-order
    # rules, without touching the archive itself.
    loose_override_dir = tmp_path / "loose_override"
    (loose_override_dir / "entities").mkdir(parents=True)
    (loose_override_dir / "entities" / "pack_test_goblin.json").write_text(
        '{"id": "pack_test_goblin", "display_name": "Rebalanced Goblin", '
        '"stats": {"hp": 999}, "ai": {"behavior": "chaser"}, "loot_table": {"entries": []}}'
    )

    _reset()
    app = Application(data_root=loose_override_dir, base_archive_dir=base_archive_dir)
    app.boot()

    goblin = app.registry.get("entities", "pack_test_goblin")
    assert goblin["display_name"] == "Rebalanced Goblin"
    assert goblin["stats"]["hp"] == 999
    # The archive itself is untouched -- re-reading it directly still shows
    # the original content.
    from engine.modding.archive import ArchiveSource

    archive = ArchiveSource(base_archive_dir / "base_content.pak")
    try:
        import json

        original = json.loads(archive.read("data/entities/pack_test_goblin.json"))
    finally:
        archive.close()
    assert original["display_name"] == "Pack Test Goblin"


def test_second_mod_archive_adds_a_new_item_additively(tmp_path):
    project_root = tmp_path / "vslice_project"
    editor = _author_vertical_slice_project(project_root)
    dist_dir = tmp_path / "dist"
    pak_path, _pkd_path = editor.pack_project(dist_dir)

    base_archive_dir = tmp_path / "base_archives"
    base_archive_dir.mkdir()
    (base_archive_dir / "base_content.pak").write_bytes(pak_path.read_bytes())

    # A second mod project, packed independently, adding one brand-new
    # item -- must be additive, no collision with the base archive.
    mod_project_root = tmp_path / "mod_project"
    from editor.editor import Editor
    from editor.modes.data_editor import DataEditorMode

    mod_editor = Editor()
    mod_editor.new_project(mod_project_root)
    mod_data_mode = mod_editor.mode("data_editor")
    assert isinstance(mod_data_mode, DataEditorMode)
    mod_data_mode.select_content_type("item")
    mod_data_mode.set_field_value("id", "mod_added_torch")
    mod_data_mode.set_field_value("display_name", "Mod-Added Torch")
    mod_data_mode.save_current_record()

    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    write_archive(mods_dir / "extra_content.pak", mod_project_root)
    (mods_dir / "load_order.txt").write_text("extra_content.pak\n")

    empty_loose_dir = tmp_path / "empty_loose_2"
    empty_loose_dir.mkdir()

    _reset()
    app = Application(data_root=empty_loose_dir, mods_dir=mods_dir, base_archive_dir=base_archive_dir)
    app.boot()

    # Additive: both the base archive's and the mod archive's content
    # coexist, no collision.
    assert app.registry.get("entities", "pack_test_goblin") is not None
    assert app.registry.get("items", "pack_test_dagger") is not None
    assert app.registry.get("items", "mod_added_torch") is not None
