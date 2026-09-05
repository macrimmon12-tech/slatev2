"""``Project`` — a working folder the editor owns
(``docs/components/13-editor-core-authoring.md`` §1.1/§2.2/§5.1).

A project is loose files, fully git-trackable, **never** an archive —
archives (`.pak`/`.pkd`) are a pure export artifact written by
:meth:`Project.pack`, never something the editor edits in place.

Soft dependency on ``12-modding-archive-system.md`` (doc §2.1): that
component hadn't merged when this one was built, so archive unpack/pack
below is stubbed per CONTRACTS.md §8 — real ``engine.modding.archive`` is
imported lazily and used automatically the moment it exists, with no call
site changes required here.
"""

from __future__ import annotations

import datetime
import json
import zipfile
from pathlib import Path

#: Directories created under ``<project>/data/`` on New Project, mirroring
#: CONTRACTS.md §1 exactly (namespaces that live under a shared parent
#: directory, e.g. ``entities/npcs``, are listed as their own entry so the
#: subdirectory exists even before any content is authored there).
DATA_SUBDIRS = [
    "entities",
    "entities/npcs",
    "items",
    "items/legendary",
    "items/sets",
    "spells",
    "effects",
    "affixes",
    "campaigns",
    "maps",
    "maps/vaults",
    "config",
    "dialogs",
    "shops",
    "scripts",
]

#: Directories created under ``<project>/assets/`` on New Project.
ASSET_SUBDIRS = ["sprites", "sfx", "music"]

_BLANK_MAP_ID = "untitled"
_BLANK_MAP_WIDTH = 40
_BLANK_MAP_HEIGHT = 25

_PROJECT_SCHEMA_VERSION = 1

_DEFAULT_THEME_PATH = ("sprites", "theme.json")

#: The 16 named wall-variant slots (``07-input-renderer-audio.md`` §2.3/
#: §5.1) — seeded empty (no sprite assigned) so New Project ships "a
#: default tileset" as a real, structurally-complete 16-slot theme file
#: rather than a single generic wall slot, even before any sprite exists
#: (absence = zero cost: the renderer's colored-rect fallback covers every
#: unassigned slot until art lands).
_WALL_VARIANT_SLOTS = [f"wall_{i}" for i in range(16)]


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _blank_map_payload() -> dict:
    """A small fixed-size map, all floor tiles, no entities (doc §1.1).

    Tile shape mirrors ``engine.systems.worldgen.tilemap_to_dict``'s output
    field-for-field (``x``/``y``/``walkable``/``room_id``/``sprite_ref``/
    ``is_lit_room``) so a handcrafted map round-trips through the same
    JSON shape a procedurally generated one would — without this module
    ever importing that (gameplay-owned) code, per doc §1's "editor never
    imports engine gameplay systems" boundary. A handcrafted blank map
    deliberately has no ``room_id`` on any tile — Map Editor's own
    validation (§2.4) is what's responsible for surfacing that, not this
    constructor silently inventing room data.
    """
    tiles = [
        {
            "x": x,
            "y": y,
            "walkable": True,
            "room_id": None,
            "sprite_ref": None,
            "is_lit_room": False,
        }
        for y in range(_BLANK_MAP_HEIGHT)
        for x in range(_BLANK_MAP_WIDTH)
    ]
    return {
        "id": _BLANK_MAP_ID,
        "display_name": "Untitled",
        "width": _BLANK_MAP_WIDTH,
        "height": _BLANK_MAP_HEIGHT,
        "tiles": tiles,
        "rooms": {},
        "stairs_down": None,
        "stairs_up": None,
        "entities": [],
        "transitions": [],
    }


class Project:
    """A project rooted at ``root`` — loose files mirroring CONTRACTS.md
    §1's ``data/``/``assets/`` layout (doc §5.1)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- construction -------------------------------------------------------

    @classmethod
    def new(cls, root: Path) -> "Project":
        """Create a fresh project folder: the minimal directory skeleton, a
        blank ``data/maps/untitled.json``, and ``project.json``. Callers
        (the Editor shell) are expected to switch straight to Map Editor
        with the blank map loaded immediately after this returns — no
        intermediate setup wizard (doc §1.1)."""
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        for sub in DATA_SUBDIRS:
            (root / "data" / sub).mkdir(parents=True, exist_ok=True)
        for sub in ASSET_SUBDIRS:
            (root / "assets" / sub).mkdir(parents=True, exist_ok=True)

        project = cls(root)
        project_json = {
            "schema_version": _PROJECT_SCHEMA_VERSION,
            "name": root.name,
            "created": _now_iso(),
        }
        (root / "project.json").write_text(json.dumps(project_json, indent=2))

        blank_map_path = project.data_path("maps", f"{_BLANK_MAP_ID}.json")
        blank_map_path.write_text(json.dumps(_blank_map_payload(), indent=2))

        theme_path = project.assets_path(*_DEFAULT_THEME_PATH)
        theme_path.parent.mkdir(parents=True, exist_ok=True)
        theme_payload = {
            "schema_version": 1,
            "wall_variants": {slot: None for slot in _WALL_VARIANT_SLOTS},
        }
        theme_path.write_text(json.dumps(theme_payload, indent=2))

        return project

    @classmethod
    def open(cls, root_or_archive: Path) -> "Project":
        """Open an existing project folder directly, or unpack a
        ``.pak``/``.pkd`` archive into a fresh project folder first (doc
        §1.1). Archive unpacking needs ``12-modding-archive-system.md``'s
        reader; if that component hasn't merged yet, this raises
        :class:`ArchiveSupportUnavailable` rather than silently no-oping —
        the editor UI is expected to disable the "Open Archive..." menu
        item entirely in that case (doc §2.1) so this path shouldn't
        normally be reached, but a direct caller (e.g. a script) still
        gets a clear, documented error instead of a stack trace from a
        missing module.
        """
        path = Path(root_or_archive)
        if path.is_dir():
            return cls(path)

        if path.suffix in (".pak", ".pkd"):
            try:
                from engine.modding.archive import ArchiveSource  # type: ignore
            except ImportError as exc:
                raise ArchiveSupportUnavailable(
                    "Archive unpacking requires 12-modding-archive-system.md, "
                    "which hasn't merged yet; open a loose project folder "
                    "instead (see docs/components/13-editor-core-authoring.md §2.1)."
                ) from exc

            dest = path.with_suffix("")
            ArchiveSource(path).unpack_to(dest)  # type: ignore[attr-defined]
            return cls(dest)

        raise FileNotFoundError(f"Not a project folder or a .pak/.pkd archive: {path}")

    # -- path helpers -------------------------------------------------------

    def data_path(self, *parts: str) -> Path:
        return self.root / "data" / Path(*parts)

    def assets_path(self, *parts: str) -> Path:
        return self.root / "assets" / Path(*parts)

    # -- packing --------------------------------------------------------------

    def validate(self) -> list[str]:
        """Structural schema checks over the project's data (doc §1.1):
        every JSON file under ``data/`` must at least parse, and every
        namespaced record (everything except ``config/``) must carry a
        non-empty ``id`` field, matching the same key convention
        ``engine.core.registry.DataRegistry`` enforces at load time.
        Returns a list of human-readable warning strings; **never raises**
        and never blocks :meth:`pack` — packing with warnings is allowed,
        Map Editor's own validation panel (§2.4) is the non-blocking
        surface these warnings are meant to feed."""
        warnings: list[str] = []
        data_dir = self.root / "data"
        if not data_dir.is_dir():
            return warnings

        for json_path in sorted(data_dir.rglob("*.json")):
            try:
                payload = json.loads(json_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                warnings.append(f"{json_path.relative_to(self.root)}: malformed JSON ({exc})")
                continue

            if not isinstance(payload, dict):
                warnings.append(
                    f"{json_path.relative_to(self.root)}: expected a JSON object, "
                    f"got {type(payload).__name__}"
                )
                continue

            is_config = json_path.is_relative_to(data_dir / "config")
            if not is_config and not payload.get("id"):
                warnings.append(
                    f"{json_path.relative_to(self.root)}: missing a non-empty 'id' field"
                )

        return warnings

    def pack(self, dist_dir: Path) -> tuple[Path, Path]:
        """Validate, then write ``.pak``/``.pkd`` build artifacts into
        ``dist_dir`` — a **read-only** operation over the project folder
        (doc §1.1): never touches the loose source. Uses
        ``12-modding-archive-system.md``'s real writer if merged, otherwise
        a minimal local ``zipfile``-based stub (doc §2.1 — a ``.pak``/
        ``.pkd`` is just a renamed zip)."""
        dist_dir = Path(dist_dir)
        dist_dir.mkdir(parents=True, exist_ok=True)

        self.validate()  # warnings only — packing proceeds regardless.

        pak_path = dist_dir / f"{self.root.name}.pak"
        pkd_path = dist_dir / f"{self.root.name}.pkd"

        try:
            from engine.modding.archive import write_archive  # type: ignore

            write_archive(self.root, pak_path, pkd_path)  # type: ignore[misc]
        except ImportError:
            self._stub_write_zip(pak_path)
            self._stub_write_zip(pkd_path)

        return pak_path, pkd_path

    def _stub_write_zip(self, dest: Path) -> None:
        """Minimal local zip writer standing in for
        ``12-modding-archive-system.md``'s real archive writer (doc §2.1).
        Replace-on-merge, per CONTRACTS.md §8: delete this once ``12``
        lands and the integration test (§9) still passes against the real
        writer."""
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(self.root.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(self.root))


class ArchiveSupportUnavailable(RuntimeError):
    """Raised by :meth:`Project.open` for a ``.pak``/``.pkd`` path when
    ``12-modding-archive-system.md`` hasn't merged yet (doc §2.1)."""
