"""``InteractableComponent`` unit tests (component doc
``11-npc-dialog-shop-content.md`` §8/§9).
"""

from __future__ import annotations

import ast
import inspect

from engine.core import save
from engine.systems import interaction
from engine.systems.interaction import InteractableComponent


def test_registered_in_component_registry():
    assert save._COMPONENT_REGISTRY["InteractableComponent"] is InteractableComponent


def test_round_trip_with_nested_lists_and_booleans():
    original = InteractableComponent(
        data={
            "kind": "npc",
            "dialog_id": "shopkeeper_mira_dialog",
            "flags": [True, False, "extra"],
            "nested": {"a": [1, 2, {"b": True}]},
        }
    )

    round_tripped = InteractableComponent.from_dict(original.to_dict())

    assert round_tripped == original
    # Proves the round trip goes through plain dict/list, not the same
    # Python object reference.
    assert round_tripped.data is not original.data


def test_empty_data_round_trips():
    original = InteractableComponent(data={})
    assert InteractableComponent.from_dict(original.to_dict()) == original


def test_source_never_branches_on_data_contents():
    """Definition of Done (component doc §8): a lint check proving Python
    code in this module never branches on ``.data``'s contents -- only Lua
    (``npc_interaction.lua``) reads/branches on ``data["kind"]`` or any
    other key.

    Parses the module's real AST (not a substring scan, so prose in the
    docstring mentioning "kind" as an example doesn't false-positive) and
    asserts two things: the ``InteractableComponent`` class defines no
    methods of its own (``to_dict``/``from_dict`` are injected by
    ``@component``, not hand-written here -- component doc §7's whole
    point is that this class has *no* behavior beyond the bare dataclass
    field), and the module contains no conditional/comparison logic
    anywhere at all -- there is nothing left in this file that *could*
    branch on ``.data``'s contents.
    """
    source = inspect.getsource(interaction)
    tree = ast.parse(source)

    class_def = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "InteractableComponent"
    )
    methods = [node for node in class_def.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert methods == [], f"InteractableComponent must have no hand-written methods, found: {methods}"

    branching_nodes = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.If, ast.IfExp, ast.Compare, ast.Match))
    ]
    assert branching_nodes == [], "engine/systems/interaction.py must contain no conditional logic"
