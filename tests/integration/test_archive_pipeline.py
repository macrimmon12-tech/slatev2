"""The load-bearing integration test for 12-modding-archive-system.md
(this component's doc §1.1, §8, §9; CONTRACTS.md §7.3).

v1's ``DataRegistry._load_archive`` was a documented no-op stub for the
project's entire life -- the mod system's override semantics were built and
tested against loose directories, but nobody ever proved a real ``.pak``
file's contents actually reached the registry. This test is that proof: it
builds real zip fixtures on real disk via ``zipfile``, feeds them through
this component's real ``resolve_source_list`` seam into a real
``DataRegistry`` (no mocking of the registry, the archive reader, or the
filesystem), and asserts the full CONTRACTS.md §4 priority chain resolves
correctly with all three tiers present at once:

    base archives -> mod archives (load-order sequence) -> loose files

with loose files winning regardless of load-order position.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from engine.core.registry import DataRegistry
from engine.modding.load_order import resolve_source_list


def _write_json(directory: Path, relative_path: str, payload: str) -> None:
    full_path = directory / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(payload, encoding="utf-8")


def _build_pak(path: Path, files: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, mode="w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def test_full_archive_pipeline_resolves_contracts_priority_order(tmp_path):
    # 1. Base-content loose directory -- the game's own base data, always
    #    loose, lowest priority of the "loose files" tier.
    base_loose_dir = tmp_path / "data"
    _write_json(
        base_loose_dir,
        "entities/goblin.json",
        '{"id": "goblin", "name": "Goblin", "hp": 10}',
    )

    # 2. Base archive: an override of goblin.json (different stat) plus a
    #    brand-new orc.json -- additive content from an archive must load.
    base_archive_dir = tmp_path / "base_archives"
    _build_pak(
        base_archive_dir / "base_content.pak",
        {
            "data/entities/goblin.json": '{"id": "goblin", "name": "Goblin (base pak)", "hp": 15}',
            "data/entities/orc.json": '{"id": "orc", "name": "Orc", "hp": 20}',
        },
    )

    # 3. Two mod archives, both overriding goblin.json, registered in
    #    load_order.txt -- the *later* line must win between the two, and
    #    neither must beat the loose override added in step 4.
    mods_dir = tmp_path / "mods"
    _build_pak(
        mods_dir / "mod_early.pak",
        {"data/entities/goblin.json": '{"id": "goblin", "name": "Goblin (early mod)", "hp": 30}'},
    )
    _build_pak(
        mods_dir / "mod_late.pak",
        {"data/entities/goblin.json": '{"id": "goblin", "name": "Goblin (late mod)", "hp": 40}'},
    )
    (mods_dir / "load_order.txt").write_text(
        "# mod_late is listed after mod_early -> mod_late wins between the two\n"
        "mod_early.pak\n"
        "mod_late.pak\n"
    )

    # 4. A mod-dev loose folder override, positioned as "loose" per §4's
    #    priority tier -- must win over every archive regardless of
    #    load-order position.
    dev_loose_dir = tmp_path / "dev_loose_override"
    _write_json(
        dev_loose_dir,
        "entities/goblin.json",
        '{"id": "goblin", "name": "Goblin (dev loose override)", "hp": 999}',
    )

    # Real resolution seam -> real DataRegistry, no mocks anywhere.
    tiers = resolve_source_list(
        base_archive_dir=base_archive_dir,
        mods_dir=mods_dir,
        loose_dirs=[base_loose_dir, dev_loose_dir],
    )
    registry = DataRegistry()
    registry.load_sources(tiers)

    # The loose-file override wins over every archive, regardless of
    # load-order position.
    goblin = registry.get("entities", "goblin")
    assert goblin is not None
    assert goblin["name"] == "Goblin (dev loose override)"
    assert goblin["hp"] == 999

    # Additive content from inside the base .pak actually loaded.
    orc = registry.get("entities", "orc")
    assert orc is not None
    assert orc["name"] == "Orc"

    # The intermediate mod archive's override is provably not what won --
    # neither its own value nor the base pak's survived; only the later mod
    # archive would have won *among archives*, and even that lost to loose.
    assert goblin["hp"] not in (15, 30)


def test_mod_archive_ordering_is_respected_when_loose_override_is_absent(tmp_path):
    """Isolates the archive-vs-archive ordering claim from the prior test's
    "loose always wins" claim: with no loose override present, the later
    load_order.txt line must win between two mod archives, and the base
    archive must lose to both."""
    base_archive_dir = tmp_path / "base_archives"
    _build_pak(
        base_archive_dir / "base_content.pak",
        {"data/entities/goblin.json": '{"id": "goblin", "hp": 15}'},
    )

    mods_dir = tmp_path / "mods"
    _build_pak(mods_dir / "mod_early.pak", {"data/entities/goblin.json": '{"id": "goblin", "hp": 30}'})
    _build_pak(mods_dir / "mod_late.pak", {"data/entities/goblin.json": '{"id": "goblin", "hp": 40}'})
    (mods_dir / "load_order.txt").write_text("mod_early.pak\nmod_late.pak\n")

    base_loose_dir = tmp_path / "data"  # empty base data dir, no override
    base_loose_dir.mkdir()

    tiers = resolve_source_list(
        base_archive_dir=base_archive_dir,
        mods_dir=mods_dir,
        loose_dirs=[base_loose_dir],
    )
    registry = DataRegistry()
    registry.load_sources(tiers)

    assert registry.get("entities", "goblin")["hp"] == 40
