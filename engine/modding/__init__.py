"""Modding & archive system (12-modding-archive-system.md).

- :mod:`engine.modding.archive` — opens ``.pak``/``.pkd`` files (both are
  zip archives, read identically regardless of extension) and exposes their
  contents as a :class:`engine.core.registry.ContentSource`.
- :mod:`engine.modding.load_order` — parses ``mods/load_order.txt`` and
  resolves the final ordered source list (base archives -> mod archives ->
  loose files) the ``DataRegistry`` loads from, per CONTRACTS.md §4.

Load-time only: nothing here subscribes to or emits events (CONTRACTS.md §2
rule 10 — the Data Registry, and by extension this package, is the only
disk I/O path for content, exercised once at startup).
"""
