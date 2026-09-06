"""``data/dialogs/*.json`` schema validator (component doc
``11-npc-dialog-shop-content.md`` §5.1).

No ``jsonschema`` dependency — see ``engine/core/schemas/__init__.py`` for
why every validator in this package is a small hand-rolled function
instead (CONTRACTS.md §9's "equivalent lightweight validator" allowance).
This follows the exact naming precedent ``ui_skin_schema.py``/
``snippets_schema.py`` set for sibling components rather than adding a
literal ``engine/core/schemas/dialog.schema.json`` file with no runtime
validator behind it (that filename was this component's own doc's guess
at the eventual name, §4) — flagged in this component's PR, same as
10-lua-scripting-layer.md flagged the identical naming deviation for
``snippets.json``.
"""

from __future__ import annotations

from typing import Any

#: ``choices[].action`` fixed vocabulary (doc §5.1) — "extend additively if
#: a real content need surfaces, don't invent per-dialog custom actions."
VALID_CHOICE_ACTIONS = frozenset({"open_shop", "close"})

#: ``nodes[].on_enter[]`` directive kinds (doc §5.1).
VALID_ON_ENTER_KEYS = frozenset({"campaign_state", "floor_state"})


class DialogSchemaError(ValueError):
    """Raised by :func:`validate_dialog_graph` for a malformed dialog graph.
    The message names the offending field/path."""


def _fail(path: str, message: str) -> None:
    raise DialogSchemaError(f"{path}: {message}")


def _validate_on_enter(directive: Any, path: str) -> None:
    if not isinstance(directive, dict):
        _fail(path, f"on_enter directive must be an object, got {type(directive).__name__}")
    keys_present = [key for key in VALID_ON_ENTER_KEYS if key in directive]
    if len(keys_present) != 1:
        _fail(
            path,
            "on_enter directive must have exactly one of "
            f"{sorted(VALID_ON_ENTER_KEYS)}, got keys {sorted(directive)}",
        )
    if "value" not in directive:
        _fail(path, "on_enter directive requires a 'value' field")


def _validate_choice(choice: Any, path: str, node_ids: set[str]) -> None:
    if not isinstance(choice, dict):
        _fail(path, f"choice must be an object, got {type(choice).__name__}")
    if not isinstance(choice.get("text"), str) or not choice["text"]:
        _fail(path, "choice requires a non-empty string 'text'")

    has_goto = "goto" in choice
    has_action = "action" in choice
    if has_goto == has_action:
        _fail(path, "choice must have exactly one of 'goto' or 'action'")

    if has_goto:
        goto = choice["goto"]
        if not isinstance(goto, str) or not goto:
            _fail(path, "choice 'goto' must be a non-empty string")
        if node_ids and goto not in node_ids:
            _fail(path, f"choice 'goto' references unknown node {goto!r}")

    if has_action:
        action = choice["action"]
        if action not in VALID_CHOICE_ACTIONS:
            _fail(
                path,
                f"choice 'action' {action!r} is not one of {sorted(VALID_CHOICE_ACTIONS)}",
            )
        if action == "open_shop" and not choice.get("shop_id"):
            _fail(path, "choice action 'open_shop' requires a non-empty 'shop_id'")

    condition = choice.get("condition")
    if condition is not None and not isinstance(condition, str):
        _fail(path, "choice 'condition' must be a string")


def _validate_node(node: Any, path: str, node_ids: set[str]) -> None:
    if not isinstance(node, dict):
        _fail(path, f"node must be an object, got {type(node).__name__}")
    if not isinstance(node.get("text"), str) or not node["text"]:
        _fail(path, "node requires a non-empty string 'text'")

    on_enter = node.get("on_enter")
    if on_enter is not None:
        if not isinstance(on_enter, list):
            _fail(path, "'on_enter' must be an array")
        for index, directive in enumerate(on_enter):
            _validate_on_enter(directive, f"{path}.on_enter[{index}]")

    choices = node.get("choices")
    if choices is None:
        _fail(path, "node requires a 'choices' array (empty list = terminal node)")
    if not isinstance(choices, list):
        _fail(path, "'choices' must be an array")
    for index, choice in enumerate(choices):
        _validate_choice(choice, f"{path}.choices[{index}]", node_ids)


def validate_dialog_graph(config: Any) -> None:
    """Validate one ``data/dialogs/*.json`` file's top-level shape (doc
    §5.1): ``{id, start_node, nodes}``. Raises :class:`DialogSchemaError`
    on the first problem found."""
    if not isinstance(config, dict):
        _fail("$", f"dialog graph must be an object, got {type(config).__name__}")

    if not isinstance(config.get("id"), str) or not config["id"]:
        _fail("$", "dialog graph requires a non-empty string 'id'")

    nodes = config.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        _fail("$.nodes", "'nodes' must be a non-empty object")

    node_ids = set(nodes.keys())

    start_node = config.get("start_node")
    if not isinstance(start_node, str) or not start_node:
        _fail("$", "dialog graph requires a non-empty string 'start_node'")
    if start_node not in node_ids:
        _fail("$", f"'start_node' {start_node!r} is not a key of 'nodes'")

    for node_id, node in nodes.items():
        _validate_node(node, f"$.nodes.{node_id}", node_ids)
