"""Headless pygame smoke test for the editor shell
(``docs/components/13-editor-core-authoring.md`` §9 — CONTRACTS.md §7.3's
"built but not wired" gate applied to the editor's own internal pipeline).

Drives the editor's real entry points end-to-end: Project Manager, the
mode-switching shell across every registered mode, a real save in Data
Editor, and Pack Project — not mocks of any of them. ``SDL_VIDEODRIVER``
is already forced to ``dummy`` by ``tests/conftest.py`` before pygame is
ever imported.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pygame

from editor.editor import Editor
from editor.modes.data_editor import DataEditorMode
from editor.modes.map_editor import MapEditorMode


def test_editor_smoke_project_to_pack(tmp_path: Path) -> None:
    pygame.init()
    surface = pygame.Surface((800, 600))

    editor = Editor()

    # -- New Project lands in Map Editor with a loaded blank map ------------
    project_root = tmp_path / "smoke_project"
    editor.new_project(project_root)

    assert editor.active_mode_id == "map_editor"
    map_mode = editor.mode("map_editor")
    assert isinstance(map_mode, MapEditorMode)
    assert map_mode.current_map["id"] == "untitled"
    assert map_mode.current_map["width"] == 40
    assert map_mode.current_map["height"] == 25

    # -- switch through every registered mode, update+draw once each --------
    mode_ids = [mode_id for mode_id, _label in editor.tab_list]
    assert mode_ids == [
        "map_editor",
        "sprite_editor",
        "data_editor",
        "skin_editor",
        "sprite_manager",
        "audio_tab",
    ]

    for mode_id in mode_ids:
        editor.switch_mode(mode_id)
        assert editor.active_mode_id == mode_id
        mode = editor.mode(mode_id)
        mode.update(1 / 60)
        mode.draw(surface)  # must not raise against a real (dummy-driver) surface

    # -- one real save in Data Editor -----------------------------------------
    editor.switch_mode("data_editor")
    data_mode = editor.mode("data_editor")
    assert isinstance(data_mode, DataEditorMode)
    data_mode.select_content_type("monster")
    data_mode.set_field_value("id", "smoke_test_goblin")
    data_mode.set_field_value("display_name", "Smoke Test Goblin")
    saved_path = data_mode.save_current_record()

    expected_path = editor.project.data_path("entities", "smoke_test_goblin.json")
    assert saved_path == expected_path
    assert expected_path.exists()
    saved_record = json.loads(expected_path.read_text())
    assert saved_record["id"] == "smoke_test_goblin"

    # -- Pack Project -----------------------------------------------------------
    dist_dir = tmp_path / "dist"
    pak_path, pkd_path = editor.pack_project(dist_dir)

    assert pak_path.exists()
    assert pkd_path.exists()

    for archive_path in (pak_path, pkd_path):
        with zipfile.ZipFile(archive_path) as zf:
            assert zf.testzip() is None
            names = zf.namelist()
            assert any(name.endswith("smoke_test_goblin.json") for name in names)
            with zf.open(next(n for n in names if n.endswith("smoke_test_goblin.json"))) as fh:
                packed_record = json.loads(fh.read())
            assert packed_record["id"] == "smoke_test_goblin"
