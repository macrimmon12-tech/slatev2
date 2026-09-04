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
files always winning. Archive reading isn't implemented yet (that's
component 12); this module accepts an already-resolved list of loose-file
directories standing in for "mods" for now, in ascending priority order,
loaded after the base ``data_root`` and before nothing (there is no
separate "loose override" tier yet since every source here is already a
loose directory) — when archives land, ``data_root`` keeps meaning "loose
files" and stays the highest-priority source by construction: it is always
loaded last.

Same ``id`` at the *same* priority (e.g. two files within one source root)
is a load-time error naming both conflicting paths. Same ``id`` at
*different* priority is a silent override, by design — CONTRACTS.md §4
explicitly says not to warn here, it would get noisy under normal modding
use.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

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
        # Tracks (namespace, id) -> (priority, source_path) for duplicate
        # detection across reload() calls.
        self._sources: dict[str, dict[str, tuple[int, Path]]] = {
            ns: {} for ns in NAMESPACE_TABLE
        }
        self._data_root: Path | None = None
        self._mod_load_order: list[Path] = []

    def load(self, data_root: Path, mod_load_order: list[Path] | None = None) -> None:
        """Load every namespace from ``data_root`` (priority 0, the game's
        base loose content) plus each directory in ``mod_load_order`` (in
        list order, each at successively higher priority than the base and
        than earlier entries in the list — later entries win on conflict).
        """
        self._data_root = Path(data_root)
        self._mod_load_order = [Path(p) for p in (mod_load_order or [])]
        self._reload_from_sources()

    def reload(self) -> None:
        """Re-scan disk using the source roots passed to the last
        :meth:`load` call. Dev/editor use only."""
        if self._data_root is None:
            raise RuntimeError("DataRegistry.reload() called before load()")
        self._reload_from_sources()

    def _reload_from_sources(self) -> None:
        assert self._data_root is not None
        self._data = {ns: {} for ns in NAMESPACE_TABLE}
        self._sources = {ns: {} for ns in NAMESPACE_TABLE}

        source_roots = [self._data_root, *self._mod_load_order]
        for priority, source_root in enumerate(source_roots):
            for namespace, spec in NAMESPACE_TABLE.items():
                self._load_namespace_from_root(namespace, spec, source_root, priority)

    def _load_namespace_from_root(
        self,
        namespace: str,
        spec: NamespaceSpec,
        source_root: Path,
        priority: int,
    ) -> None:
        namespace_dir = source_root / spec.directory
        if not namespace_dir.is_dir():
            return  # absence = zero cost — a mod needn't provide every namespace

        excluded_dirs = {namespace_dir / excl for excl in spec.excludes}

        for json_path in sorted(namespace_dir.rglob("*.json")):
            if any(
                excluded == json_path or excluded in json_path.parents
                for excluded in excluded_dirs
            ):
                continue
            self._load_file(namespace, spec, json_path, priority)

    def _load_file(
        self,
        namespace: str,
        spec: NamespaceSpec,
        json_path: Path,
        priority: int,
    ) -> None:
        with json_path.open("r", encoding="utf-8") as fh:
            try:
                payload = json.load(fh)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Malformed JSON in {json_path}: {exc}") from exc

        if not isinstance(payload, dict):
            raise ValueError(
                f"Content file {json_path} must contain a JSON object, got {type(payload).__name__}"
            )

        if spec.key_field is None:
            key = json_path.stem
        else:
            key = payload.get(spec.key_field)
            if not isinstance(key, str) or not key:
                raise ValueError(
                    f"Content file {json_path} is missing a non-empty string "
                    f"{spec.key_field!r} field (namespace {namespace!r})"
                )

        sources = self._sources[namespace]
        existing = sources.get(key)
        if existing is not None:
            existing_priority, existing_path = existing
            if existing_priority == priority:
                raise DuplicateIdError(
                    f"Duplicate id {key!r} in namespace {namespace!r} at the same "
                    f"load priority: {existing_path} and {json_path}"
                )
            if priority < existing_priority:
                # Lower priority than what's already loaded — the existing
                # (higher-priority) entry silently wins, per CONTRACTS.md §4.
                return
            # Higher priority than what's loaded — silently override, no
            # warning (deliberate, CONTRACTS.md §4).

        sources[key] = (priority, json_path)
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
