"""``.pak``/``.pkd`` archive reading (12-modding-archive-system.md §2).

A ``.pak`` or ``.pkd`` file is a renamed zip archive, read **identically**
regardless of extension — the extension is a purely cosmetic signal ("data
pack" vs. "asset pack") with zero functional branching anywhere in this
module. A mod archive's internal layout mirrors the base game's own
``data/``/``assets/`` tree exactly (CONTRACTS.md §1, this component's doc
§5.2): e.g. ``data/entities/monsters/foo.json``, ``assets/sprites/foo.png``.

This module never reads content JSON itself and never touches the event
bus — it is a generic byte-level file reader. Interpreting archive bytes as
namespaced registry content (scoping to the ``data/`` subtree, JSON
parsing, id-keying) is :mod:`engine.core.registry`'s and
:mod:`engine.modding.load_order`'s job, not this one's — see
``ScopedSource`` below and ``load_order.resolve_source_list``.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from engine.core.registry import ContentSource

logger = logging.getLogger(__name__)


class ArchiveSource:
    """:class:`~engine.core.registry.ContentSource` backed by a ``.pak``/
    ``.pkd`` zip archive. Paths are archive-relative (e.g.
    ``"data/entities/monsters/foo.json"``), exactly as stored in the zip —
    no ``data/``-prefix stripping here, see ``ScopedSource`` for that.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._zip = zipfile.ZipFile(self.path, mode="r")
        # Directory entries (zip infolist entries ending in "/") aren't
        # real files — exclude them so list_files()/exists() only ever
        # name readable content, matching DirectorySource's rglob-of-files
        # behavior.
        self._files = {
            info.filename for info in self._zip.infolist() if not info.is_dir()
        }

    def list_files(self) -> list[str]:
        return sorted(self._files)

    def read(self, relative_path: str) -> bytes:
        return self._zip.read(relative_path)

    def exists(self, relative_path: str) -> bool:
        return relative_path in self._files

    def close(self) -> None:
        self._zip.close()

    def __repr__(self) -> str:  # pragma: no cover - debug convenience
        return f"ArchiveSource({self.path})"


def open_archive(path: Path) -> ArchiveSource:
    """Opens a ``.pak`` or ``.pkd`` file — extension-agnostic, both are
    zip archives read the exact same way."""
    return ArchiveSource(path)


def write_archive(archive_path: Path, source_dir: Path) -> None:
    """Packs ``source_dir`` into a ``.pak``/``.pkd`` zip archive at
    ``archive_path``, preserving ``source_dir``-relative paths.

    Minimal helper for 13-editor-core-authoring.md's Project Manager ("Pack
    Project"), which owns the primary archive-writing logic and UI — this
    is just the zipfile mechanics, provided here since ``13`` soft-depends
    on this component's read path and a matching write helper is a small,
    reasonable addition per this component's doc §1 (out-of-scope section).
    """
    archive_path = Path(archive_path)
    source_dir = Path(source_dir)
    with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(source_dir.rglob("*")):
            if file_path.is_file():
                arcname = str(file_path.relative_to(source_dir)).replace("\\", "/")
                zf.write(file_path, arcname=arcname)


class ScopedSource:
    """:class:`~engine.core.registry.ContentSource` adapter that scopes
    another source to files under a fixed prefix, stripping that prefix
    from ``list_files()``/``read()``/``exists()`` paths.

    Archive internals mirror the base game's ``data/``+``assets/`` tree
    (this component's doc §5.2), but a loose ``data_root`` already points
    directly *at* the data tree with no extra ``data/`` segment (see
    ``engine/main.py``'s ``DEFAULT_DATA_ROOT``). Scoping an
    :class:`ArchiveSource` to the ``"data"`` prefix makes it look, from the
    registry's perspective, exactly like a loose directory rooted at the
    archive's ``data/`` subtree — so the same namespace-to-directory table
    (CONTRACTS.md §4) applies uniformly regardless of source.
    """

    def __init__(self, inner: ContentSource, prefix: str) -> None:
        self._inner = inner
        self._prefix = prefix.strip("/") + "/"

    def list_files(self) -> list[str]:
        return [
            relative_path[len(self._prefix) :]
            for relative_path in self._inner.list_files()
            if relative_path.startswith(self._prefix)
        ]

    def read(self, relative_path: str) -> bytes:
        return self._inner.read(self._prefix + relative_path)

    def exists(self, relative_path: str) -> bool:
        return self._inner.exists(self._prefix + relative_path)

    def __repr__(self) -> str:  # pragma: no cover - debug convenience
        return f"ScopedSource({self._inner!r}, prefix={self._prefix!r})"
