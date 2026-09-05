# tests/fixtures/archives/

Owned by `12-modding-archive-system.md`. Intentionally empty of committed
`.pak`/`.pkd` files — per that component's doc §6, archive fixtures are
built by the test itself via Python's `zipfile` module (into `tmp_path`,
not this directory), so they stay reviewable in a diff and don't bloat the
repo with binary blobs. See `tests/unit/test_archive.py`,
`tests/unit/test_load_order.py`, and
`tests/integration/test_archive_pipeline.py` for the fixture-building
helpers this convention uses.

This directory exists as the documented place a future need for a
*committed* archive fixture (if one ever arises) should go, per
CONTRACTS.md §1's file-ownership convention — not because anything here
needs one today.
