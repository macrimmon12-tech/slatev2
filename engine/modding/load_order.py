"""``mods/load_order.txt`` parsing and final source-list resolution
(12-modding-archive-system.md §2, §5.1).

This module owns turning "a base archive directory, a mods directory, and a
list of loose directories" into the exact ordered list of
:class:`~engine.core.registry.ContentSource` *priority tiers*
``engine.core.registry.DataRegistry.load_sources`` consumes, matching
CONTRACTS.md §4 verbatim: base archives -> mod archives (order from
``mods/load_order.txt``, top = lowest priority) -> loose files, with loose
files always winning regardless of load-order position.

Priority tiers, not a flat source list: each entry in the returned list is
itself a list of one-or-more :class:`ContentSource` sharing *one* priority
slot. Every ``mods/load_order.txt`` line gets its own distinct tier (so two
mod archives can never collide on priority), but all base archives found
directly under ``base_archive_dir`` share a single tier — there is no
per-base-archive ordering file, so two base archives defining the same id
is a genuine same-priority collision and correctly raises
``DataRegistry.DuplicateIdError`` (this component's doc §8, the
same-priority-duplicate Definition of Done item) rather than silently
picking one.
"""

from __future__ import annotations

import logging
from pathlib import Path

from engine.core.registry import ContentSource, DirectorySource
from engine.modding.archive import ScopedSource, open_archive

logger = logging.getLogger(__name__)

_ARCHIVE_SUFFIXES = (".pak", ".pkd")

# Archive/mod-folder internal layout mirrors the base game's data/+assets/
# tree (CONTRACTS.md §1, this component's doc §5.2) — scope every
# archive-or-folder-backed mod source to its "data" subtree so it lines up
# with a loose data_root, which already points directly at the data tree.
_DATA_PREFIX = "data"

_warned_unresolvable_entries: set[Path] = set()


def parse_load_order(load_order_path: Path, mods_dir: Path) -> list[Path]:
    """Parses ``load_order_path`` (conventionally ``mods/load_order.txt``)
    into an ordered list of mod entry paths under ``mods_dir``, top of file
    = lowest priority (CONTRACTS.md §4). Blank lines and lines starting
    with ``#`` are ignored. A missing or empty file returns ``[]`` — no
    mods active is not an error (CONTRACTS.md §2 rule 7, absence = zero
    cost).

    Each returned path is ``mods_dir / <line>`` unresolved as to whether it
    names an archive file or an unpacked mod folder — that's
    :func:`resolve_source_list`'s job (or any other caller's), since a bare
    entry name is ambiguous without checking the filesystem.
    """
    load_order_path = Path(load_order_path)
    if not load_order_path.is_file():
        return []

    mods_dir = Path(mods_dir)
    entries: list[Path] = []
    for raw_line in load_order_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        entries.append(mods_dir / line)
    return entries


def resolve_source_list(
    base_archive_dir: Path | None,
    mods_dir: Path,
    loose_dirs: list[Path],
) -> list[list[ContentSource]]:
    """Builds the final ordered list of priority tiers (lowest first) that
    ``DataRegistry.load_sources()`` consumes, matching CONTRACTS.md §4
    exactly:

    1. **Base archives** — every ``.pak``/``.pkd`` file found directly
       under ``base_archive_dir`` (if given and it exists), all sharing one
       priority tier, sorted by filename for determinism. Omitted entirely
       if ``base_archive_dir`` is ``None`` or has no archives (absence =
       zero cost — the base game need not ship any).
    2. **Mod archives/folders** — each line of
       ``mods_dir / "load_order.txt"`` (via :func:`parse_load_order`),
       each its own priority tier, in file order (top = lowest priority).
       A line may name a ``.pak``/``.pkd`` file directly under
       ``mods_dir``, or a bare folder name under ``mods_dir/<name>/`` for
       mod development without packing (this component's doc §10 Open
       Questions default: support both). An unpacked mod folder mirrors
       the base layout the same as a packed archive, so it's read from
       ``mods_dir/<name>/data`` — see the module docstring's ``_DATA_PREFIX``
       note.
    3. **Loose files** — each directory in ``loose_dirs``, in list order,
       each its own priority tier, always highest priority overall. These
       are *not* prefix-scoped (unlike mod/base archives) — they're
       expected to already point directly at a data-tree-shaped directory,
       the same convention ``DataRegistry.load()``'s own ``data_root``
       argument uses today.
    """
    tiers: list[list[ContentSource]] = []

    if base_archive_dir is not None:
        base_archive_dir = Path(base_archive_dir)
        if base_archive_dir.is_dir():
            base_sources = [
                _scoped_archive(path)
                for path in sorted(base_archive_dir.iterdir())
                if path.is_file() and path.suffix.lower() in _ARCHIVE_SUFFIXES
            ]
            if base_sources:
                tiers.append(base_sources)

    mods_dir = Path(mods_dir)
    for mod_path in parse_load_order(mods_dir / "load_order.txt", mods_dir):
        source = _resolve_mod_entry(mod_path)
        if source is not None:
            tiers.append([source])

    for loose_dir in loose_dirs:
        tiers.append([DirectorySource(loose_dir)])

    return tiers


def _resolve_mod_entry(mod_path: Path) -> ContentSource | None:
    if mod_path.is_dir():
        # Unpacked mod-dev folder — same layout convention as a packed
        # archive (mirrors data/+assets/), so scope to its data/ subdir the
        # same way ScopedSource does for archives.
        return DirectorySource(mod_path / _DATA_PREFIX)
    if mod_path.is_file() and mod_path.suffix.lower() in _ARCHIVE_SUFFIXES:
        return _scoped_archive(mod_path)

    # Absence = zero cost (CONTRACTS.md §2 rule 7): a load_order.txt entry
    # that resolves to nothing on disk is skipped, not a hard failure — but
    # it's still worth a one-time log so a typo'd mod name isn't silently
    # invisible forever.
    if mod_path not in _warned_unresolvable_entries:
        logger.warning(
            "mods/load_order.txt entry %s does not resolve to a .pak/.pkd file "
            "or a folder under mods/ -- skipping",
            mod_path,
        )
        _warned_unresolvable_entries.add(mod_path)
    return None


def _scoped_archive(path: Path) -> ContentSource:
    return ScopedSource(open_archive(path), prefix=_DATA_PREFIX)
