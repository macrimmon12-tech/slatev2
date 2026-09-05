"""``data/dialogs/<id>.layout.json`` schema validator (component doc
``14-editor-visual-quest-dialog.md`` §5.1). This is this component's one
new JSON schema — everything else it touches (`10`'s ``snippets.json``,
`11`'s dialog-graph JSON) is owned and schema-validated elsewhere.

Editor-only sidecar: never loaded by the Data Registry (the ``dialogs``
namespace's loader keys only on files matching `11`'s dialog schema, so a
``.layout.json`` sibling is never returned by ``registry.get("dialogs",
...)``) and never referenced by `11`'s Lua or the game runtime — losing
this file is harmless (the editor just re-auto-lays-out on next open).

No ``jsonschema`` dependency — same hand-rolled precedent
``snippets_schema.py``/``ui_skin_schema.py`` set for sibling components
(CONTRACTS.md §9's "equivalent lightweight validator" allowance).
"""

from __future__ import annotations

from typing import Any


class DialogLayoutSchemaError(ValueError):
    """Raised by :func:`validate_dialog_layout` for a malformed
    ``<id>.layout.json``. The message names the offending field/path."""


def _fail(path: str, message: str) -> None:
    raise DialogLayoutSchemaError(f"{path}: {message}")


def validate_dialog_layout(config: Any) -> None:
    """Validate ``{schema_version, node_positions}`` (doc §5.1): every
    entry in ``node_positions`` is ``{"x": int, "y": int}``."""
    if not isinstance(config, dict):
        _fail("$", f"layout config must be an object, got {type(config).__name__}")

    if not isinstance(config.get("schema_version"), int):
        _fail("$", "layout config requires an integer 'schema_version'")

    positions = config.get("node_positions")
    if not isinstance(positions, dict):
        _fail("$.node_positions", "'node_positions' must be an object")

    for node_id, position in positions.items():
        path = f"$.node_positions[{node_id!r}]"
        if not isinstance(position, dict):
            _fail(path, f"position must be an object, got {type(position).__name__}")
        for axis in ("x", "y"):
            if not isinstance(position.get(axis), int):
                _fail(path, f"'{axis}' must be an integer")
