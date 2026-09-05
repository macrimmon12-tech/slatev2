"""Data Registry — the only disk I/O path for game content
(CONTRACTS.md §4, §2 rule 10). Loaded once at startup; zero disk I/O during
gameplay. Nothing outside this module and ``engine/modding/`` reads content
JSON off disk directly.

Namespace -> source directory table is data (a dict), not a chain of
``if``/``elif`` branches, specifically so
``12-modding-archive-system.md`` can extend the list of source roots this
registry scans *per namespace* without touching the scanning logic here —
that component adds archive-backed source roots to the list this module
already walks; it does not need to change how namespaces map to
directories.

Load priority (CONTRACTS.md §4): base archives -> mod archives (order from
``mods/load_order.txt``, top = lowest priority) -> loose files, with loose
files always winning.

Same ``id`` at the *same* priority (e.g. two files within one source root)
is a load-time error naming both conflicting paths. Same ``id`` at
*different* priority is a silent override, by design — CONTRACTS.md §4
explicitly says not to warn here, it would get noisy under normal modding
use.

--- 12-modding-archive-system.md retrofit note ---
This module used to walk bare ``Path`` directories directly (``rglob``,
``Path.open``) with no seam for anything else. ``12-modding-archive-system.md``
extends it here, per that component's Open Questions §10 default ("if
genuinely no seam exists ... implement the minimal non-breaking extension
yourself, e.g. wrap Path behind a thin ContentSource-compatible adapter"):

- :class:`ContentSource` — the minimal read interface
  (``list_files``/``read``/``exists``) a source root must support, whether
  backed by a loose directory (:class:`DirectorySource`, below) or an
  archive (``engine.modding.archive.ArchiveSource``, wrapped by
  ``engine.modding.load_order.ScopedSource``/``resolve_source_list``).
- :class:`DirectorySource` — wraps a bare ``Path`` so the existing
  loose-directory code path (``load()``) needs no behavior change; it is
  used internally by ``load()`` exactly where a bare ``Path`` was walked
  before, so every existing caller/test of ``load()`` keeps its exact prior
  signature and outcome.
- :meth:`DataRegistry.load_sources` — a new, purely additive entry point
  (``load()`` is untouched) that accepts a fully pre-resolved, ordered list
  of priority *tiers* (``list[list[ContentSource]]``, lowest priority
  first, each inner list sharing one priority slot). This is the seam
  ``engine.modding.load_order.resolve_source_list()`` targets, and what
  ``engine/main.py``'s ``Application.boot()`` now calls in place of a bare
  ``load()`` so archive/mod resolution is actually wired into the real boot
  path rather than merely available.

See that component's PR description for the full rationale.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class ContentSource(Protocol):
    """Minimal read interface a registry source root must support,
    whether backed by a loose directory or an archive. Paths are always
    relative to the source's own data root (matching how ``data_root``
    already points directly at the ``data/`` tree with no extra ``data/``
    segment of its own) — every downstream JSON parsing path is unaware of
    where the bytes actually came from."""

    def list_files(self) -> list[str]:
        """All file paths under this source, relative to its root."""
        ...

    def read(self, relative_path: str) -> bytes:
        """Raw bytes of one file, addressed by a path from ``list_files``."""
        ...

    def exists(self, relative_path: str) -> bool:
        ...


class DirectorySource:
    """:class:`ContentSource` adapter over a loose directory on disk."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def list_files(self) -> list[str]:
        if not self.root.is_dir():
            return []  # absence = zero cost (CONTRACTS.md §2 rule 7)
        return [
            str(path.relative_to(self.root)).replace("\\", "/")
            for path in self.root.rglob("*")
            if path.is_file()
        ]

    def read(self, relative_path: str) -> bytes:
        return (self.root / relative_path).read_bytes()

    def exists(self, relative_path: str) -> bool:
        return (self.root / relative_path).is_file()

    def __repr__(self) -> str:  # pragma: no cover - debug convenience
        return f"DirectorySource({self.root})"

# (relative directory under a source root, excluded sub-paths, key field).
# key field is "id" for every namespace except "configs", which keys by
# filename stem (see NamespaceSpec docstring / CONTRACTS.md §4).


@dataclass(frozen=True)
class NamespaceSpec:
    directory: str
    excludes: tuple[str, ...]
    key_field: str | None  # None means "key by filename stem" (configs only)


NAMESPACE_TABLE: dict[str, NamespaceSpec] = {
    "entities": NamespaceSpec("entities", ("npcs",), "id"),
    "items": NamespaceSpec("items", ("legendary", "sets"), "id"),
    "spells": NamespaceSpec("spells", (), "id"),
    "effects": NamespaceSpec("effects", (), "id"),
    "affixes": NamespaceSpec("affixes", (), "id"),
    "campaigns": NamespaceSpec("campaigns", (), "id"),
    "vaults": NamespaceSpec("maps/vaults", (), "id"),
    "legendaries": NamespaceSpec("items/legendary", (), "id"),
    "sets": NamespaceSpec("items/sets", (), "id"),
    "maps": NamespaceSpec("maps", ("vaults",), "id"),
    "configs": NamespaceSpec("config", (), None),
    "npcs": NamespaceSpec("entities/npcs", (), "id"),
    "dialogs": NamespaceSpec("dialogs", (), "id"),
    "shops": NamespaceSpec("shops", (), "id"),
}


class DuplicateIdError(ValueError):
    """Raised when the same namespace ID appears twice at the same load
    priority. Message names both conflicting file paths."""


class DataRegistry:
    """Singleton-shaped data registry. Construct one, call :meth:`load`
    once at startup, then only ever call :meth:`get`/:meth:`all` during
    gameplay. :meth:`reload` is a dev/editor convenience — CONTRACTS.md §4
    is explicit that it is never called mid-gameplay.
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict]] = {ns: {} for ns in NAMESPACE_TABLE}
        # Tracks (namespace, id) -> (priority, location) for duplicate
        # detection across reload() calls. ``location`` is a human-readable
        # string (not necessarily a filesystem Path — it may name a file
        # inside an archive) used only for error messages.
        self._sources: dict[str, dict[str, tuple[int, str]]] = {
            ns: {} for ns in NAMESPACE_TABLE
        }
        self._data_root: Path | None = None
        self._mod_load_order: list[Path] = []
        # Set by load_sources() instead of load(); see that method and the
        # module docstring's retrofit note.
        self._tiers_override: list[list[ContentSource]] | None = None

    def load(self, data_root: Path, mod_load_order: list[Path] | None = None) -> None:
        """Load every namespace from ``data_root`` (priority 0, the game's
        base loose content) plus each directory in ``mod_load_order`` (in
        list order, each at successively higher priority than the base and
        than earlier entries in the list — later entries win on conflict).

        Unchanged since before the 12-modding-archive-system.md retrofit —
        every existing caller/test keeps its exact behavior. See
        :meth:`load_sources` for the archive-aware entry point.
        """
        self._data_root = Path(data_root)
        self._mod_load_order = [Path(p) for p in (mod_load_order or [])]
        self._tiers_override = None
        self._reload_from_sources()

    def load_sources(self, tiers: list[list[ContentSource]]) -> None:
        """Load from a fully pre-resolved, ordered list of priority tiers
        (lowest priority first; each inner list shares one priority slot —
        a same-id collision within one tier raises :class:`DuplicateIdError`
        exactly like two files in one loose directory does via :meth:`load`).

        This is the seam ``engine.modding.load_order.resolve_source_list()``
        targets (12-modding-archive-system.md) and is purely additive:
        :meth:`load` keeps its exact prior two-argument shape and behavior
        for every existing caller. ``engine/main.py``'s ``Application.boot()``
        calls this instead of :meth:`load` so real archive/mod resolution is
        actually wired into the boot path.
        """
        self._data_root = None
        self._mod_load_order = []
        self._tiers_override = [list(tier) for tier in tiers]
        self._reload_from_sources()

    def reload(self) -> None:
        """Re-scan disk using the source roots passed to the last
        :meth:`load`/:meth:`load_sources` call. Dev/editor use only."""
        if self._data_root is None and self._tiers_override is None:
            raise RuntimeError(
                "DataRegistry.reload() called before load()/load_sources()"
            )
        self._reload_from_sources()

    def _reload_from_sources(self) -> None:
        self._data = {ns: {} for ns in NAMESPACE_TABLE}
        self._sources = {ns: {} for ns in NAMESPACE_TABLE}

        if self._tiers_override is not None:
            tiers = self._tiers_override
        else:
            assert self._data_root is not None
            tiers = [[DirectorySource(self._data_root)]]
            tiers.extend([DirectorySource(p)] for p in self._mod_load_order)

        for priority, tier in enumerate(tiers):
            for namespace, spec in NAMESPACE_TABLE.items():
                for source in tier:
                    self._load_namespace_from_root(namespace, spec, source, priority)

    def _load_namespace_from_root(
        self,
        namespace: str,
        spec: NamespaceSpec,
        source: ContentSource,
        priority: int,
    ) -> None:
        prefix = spec.directory.strip("/") + "/"
        excluded_prefixes = tuple(f"{prefix}{excl}/" for excl in spec.excludes)

        matching = sorted(
            relative_path
            for relative_path in source.list_files()
            if relative_path.startswith(prefix)
            and relative_path.endswith(".json")
            and not any(relative_path.startswith(excl) for excl in excluded_prefixes)
        )
        for relative_path in matching:
            self._load_file(namespace, spec, source, relative_path, priority)

    def _load_file(
        self,
        namespace: str,
        spec: NamespaceSpec,
        source: ContentSource,
        relative_path: str,
        priority: int,
    ) -> None:
        location = f"{relative_path} (from {source!r})"
        raw = source.read(relative_path)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed JSON in {location}: {exc}") from exc

        if not isinstance(payload, dict):
            raise ValueError(
                f"Content file {location} must contain a JSON object, got {type(payload).__name__}"
            )

        if spec.key_field is None:
            key = Path(relative_path).stem
        else:
            key = payload.get(spec.key_field)
            if not isinstance(key, str) or not key:
                raise ValueError(
                    f"Content file {location} is missing a non-empty string "
                    f"{spec.key_field!r} field (namespace {namespace!r})"
                )

        sources = self._sources[namespace]
        existing = sources.get(key)
        if existing is not None:
            existing_priority, existing_location = existing
            if existing_priority == priority:
                raise DuplicateIdError(
                    f"Duplicate id {key!r} in namespace {namespace!r} at the same "
                    f"load priority: {existing_location} and {location}"
                )
            if priority < existing_priority:
                # Lower priority than what's already loaded — the existing
                # (higher-priority) entry silently wins, per CONTRACTS.md §4.
                return
            # Higher priority than what's loaded — silently override, no
            # warning (deliberate, CONTRACTS.md §4).

        sources[key] = (priority, location)
        self._data[namespace][key] = payload

    def get(self, namespace: str, id: str) -> dict | None:
        self._require_namespace(namespace)
        return self._data[namespace].get(id)

    def all(self, namespace: str) -> dict[str, dict]:
        self._require_namespace(namespace)
        return dict(self._data[namespace])

    def _require_namespace(self, namespace: str) -> None:
        if namespace not in NAMESPACE_TABLE:
            raise KeyError(
                f"Unknown namespace {namespace!r}; valid namespaces are {sorted(NAMESPACE_TABLE)}"
            )
