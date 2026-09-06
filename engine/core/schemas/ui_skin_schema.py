"""Widget-tree JSON schema validator (``docs/components/08-ui-runtime.md``
§5.1/§5.2). This is the one shared format ``engine/ui/ui_runtime.py``
parses whether it comes from ``data/config/ui_skin.json``, a Python
``create_panel()`` call, or (per that doc's §1) a future Lua
``engine.create_panel()`` call — validating it in one place, independent
of ``UIRuntime`` itself, keeps that guarantee checkable without importing
the runtime.

No ``jsonschema`` dependency (not in ``pyproject.toml``, which is
foundation-owned) — see ``engine/core/schemas/__init__.py`` for why a
hand-rolled validator is used instead, per CONTRACTS.md §9's "equivalent
lightweight validator" allowance.
"""

from __future__ import annotations

from typing import Any

#: The six widget-tree node types this schema recognizes (doc §5.1).
WIDGET_TYPES = frozenset({"Panel", "Label", "Bar", "Button", "List", "Grid"})

#: Node types that may carry a ``children`` list (containers).
CONTAINER_TYPES = frozenset({"Panel", "List", "Grid"})

_VALID_ANCHORS = frozenset(
    {"top_left", "top_right", "bottom_left", "bottom_right", "center"}
)


class WidgetTreeError(ValueError):
    """Raised by :func:`validate_widget_tree`/:func:`validate_ui_skin_config`
    for a malformed widget tree or skin config. The message names the
    offending field/path so authoring mistakes (hand-edited JSON, a bad
    Lua ``create_panel`` call) are easy to trace."""


def _fail(path: str, message: str) -> None:
    raise WidgetTreeError(f"{path}: {message}")


def _check_rect_spec(node: dict[str, Any], path: str) -> None:
    """Every node must be positioned via either a nested ``rect`` dict or
    flat ``x``/``y``/``w``/``h`` fields (doc §5.1's "universal layout
    fields") — not required to have both, but ``rect``, when present, must
    itself be a dict."""
    rect = node.get("rect")
    if rect is not None and not isinstance(rect, dict):
        _fail(path, f"'rect' must be an object, got {type(rect).__name__}")

    anchor = node.get("anchor")
    if anchor is not None and anchor not in _VALID_ANCHORS:
        _fail(path, f"invalid anchor {anchor!r}; expected one of {sorted(_VALID_ANCHORS)}")

    visible_if = node.get("visible_if")
    if visible_if is not None:
        if not isinstance(visible_if, dict) or "data_key" not in visible_if or "equals" not in visible_if:
            _fail(
                path,
                "'visible_if' must be an object with 'data_key' and 'equals' fields",
            )


def validate_widget_tree(node: Any, path: str = "$") -> None:
    """Recursively validate a widget-tree node (and its ``children``).

    Raises :class:`WidgetTreeError` on the first problem found. Does not
    mutate ``node``. This is intentionally permissive about anything the
    doc doesn't constrain (extra/unknown keys, style names, event/payload
    shapes for ``on_click`` beyond "dict with an 'event' string") — the
    whole point of a data-driven format is that new fields can show up
    without every consumer needing a matching schema change; this only
    catches the structural mistakes that would otherwise crash
    ``UIRuntime`` deep inside a draw/update call instead of at authoring
    time.
    """
    if not isinstance(node, dict):
        _fail(path, f"widget node must be an object, got {type(node).__name__}")

    node_type = node.get("type")
    if node_type not in WIDGET_TYPES:
        _fail(path, f"unknown/missing widget type {node_type!r}; expected one of {sorted(WIDGET_TYPES)}")

    _check_rect_spec(node, path)

    on_click = node.get("on_click")
    if on_click is not None:
        if not isinstance(on_click, dict) or "event" not in on_click:
            _fail(path, "'on_click' must be an object with at least an 'event' field")

    if node_type == "Bar":
        if "value_key" not in node:
            _fail(path, "Bar node requires 'value_key'")

    if node_type in ("List", "Grid"):
        if "items_key" not in node:
            _fail(path, f"{node_type} node requires 'items_key'")
        item_template = node.get("item_template")
        if item_template is not None:
            validate_widget_tree(item_template, f"{path}.item_template")

    children = node.get("children")
    if children is not None:
        if node_type not in CONTAINER_TYPES:
            _fail(path, f"'{node_type}' nodes cannot have 'children' (only {sorted(CONTAINER_TYPES)} can)")
        if not isinstance(children, list):
            _fail(path, "'children' must be an array")
        for index, child in enumerate(children):
            validate_widget_tree(child, f"{path}.children[{index}]")


def validate_ui_skin_config(config: Any) -> None:
    """Validate the top-level ``data/config/ui_skin.json`` shape (doc
    §5.2): ``{"schema_version": int, "screens": {panel_id: <widget tree>}}``.
    """
    if not isinstance(config, dict):
        _fail("$", f"ui_skin config must be an object, got {type(config).__name__}")

    if "schema_version" not in config:
        _fail("$", "ui_skin config requires 'schema_version'")

    screens = config.get("screens")
    if not isinstance(screens, dict):
        _fail("$.screens", f"'screens' must be an object, got {type(screens).__name__}")

    for panel_id, tree in screens.items():
        validate_widget_tree(tree, f"$.screens.{panel_id}")
