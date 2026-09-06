"""``data/scripts/snippets.json`` schema validator (component doc
``10-lua-scripting-layer.md`` §5.1). No ``jsonschema`` dependency — see
``engine/core/schemas/__init__.py`` for why every validator in this
package is a small hand-rolled function instead (CONTRACTS.md §9's
"equivalent lightweight validator" allowance); this follows the exact
precedent ``ui_skin_schema.py`` set for a sibling component, rather than
adding a raw ``.json`` JSON-Schema file with no runtime validator behind
it (a literal ``engine/core/schemas/snippets.schema.json`` filename was
this component's own doc's guess at the eventual filename — this
implementation follows the established naming convention instead, noted
in this component's PR).
"""

from __future__ import annotations

from typing import Any

#: The fixed node-category vocabulary 14's palette groups by (component
#: doc §5.1) — "do not add an eighth without updating this doc."
VALID_CATEGORIES = frozenset({
    "trigger", "condition", "action", "reward", "narrative", "ui", "world",
})

VALID_PARAM_TYPES = frozenset({"string", "number", "boolean"})


class SnippetsSchemaError(ValueError):
    """Raised by :func:`validate_snippets_config` for a malformed
    ``snippets.json``. The message names the offending field/path."""


def _fail(path: str, message: str) -> None:
    raise SnippetsSchemaError(f"{path}: {message}")


def _validate_param(param: Any, path: str) -> None:
    if not isinstance(param, dict):
        _fail(path, f"param must be an object, got {type(param).__name__}")
    name = param.get("name")
    if not isinstance(name, str) or not name:
        _fail(path, "param requires a non-empty string 'name'")
    param_type = param.get("type")
    if param_type not in VALID_PARAM_TYPES:
        _fail(path, f"param {name!r} has invalid type {param_type!r}; expected one of {sorted(VALID_PARAM_TYPES)}")
    if "default" not in param:
        _fail(path, f"param {name!r} requires a 'default' field")


def _validate_snippet(snippet: Any, path: str, categories: set[str]) -> None:
    if not isinstance(snippet, dict):
        _fail(path, f"snippet must be an object, got {type(snippet).__name__}")

    snippet_id = snippet.get("id")
    if not isinstance(snippet_id, str) or not snippet_id:
        _fail(path, "snippet requires a non-empty string 'id'")

    category = snippet.get("category")
    if category not in categories:
        _fail(path, f"snippet {snippet_id!r} has category {category!r}; expected one of {sorted(categories)}")

    for field_name in ("label", "description"):
        if not isinstance(snippet.get(field_name), str) or not snippet[field_name]:
            _fail(path, f"snippet {snippet_id!r} requires a non-empty string {field_name!r}")

    params = snippet.get("params")
    if not isinstance(params, list):
        _fail(path, f"snippet {snippet_id!r} requires a 'params' array")
    for index, param in enumerate(params):
        _validate_param(param, f"{path}.params[{index}]")

    lua_template = snippet.get("lua_template")
    if not isinstance(lua_template, str) or not lua_template:
        _fail(path, f"snippet {snippet_id!r} requires a non-empty string 'lua_template'")
    # This component only guarantees the @node/@endnode marker shape is
    # unambiguous to scan for (component doc §5.1) — a full parse of the
    # marker line is 14's job, not validated here.
    if "-- @node" not in lua_template or "-- @endnode" not in lua_template:
        _fail(
            path,
            f"snippet {snippet_id!r}'s lua_template must contain a "
            "'-- @node ... -- @endnode' marker pair",
        )


def validate_snippets_config(config: Any) -> None:
    """Validate the top-level ``data/scripts/snippets.json`` shape
    (component doc §5.1): ``{schema_version, categories, snippets}``."""
    if not isinstance(config, dict):
        _fail("$", f"snippets config must be an object, got {type(config).__name__}")

    if not isinstance(config.get("schema_version"), int):
        _fail("$", "snippets config requires an integer 'schema_version'")

    categories = config.get("categories")
    if not isinstance(categories, list) or not categories:
        _fail("$.categories", "'categories' must be a non-empty array")
    unknown = [c for c in categories if c not in VALID_CATEGORIES]
    if unknown:
        _fail(
            "$.categories",
            f"unknown categories {unknown!r}; the fixed vocabulary is {sorted(VALID_CATEGORIES)}",
        )

    snippets = config.get("snippets")
    if not isinstance(snippets, list):
        _fail("$.snippets", "'snippets' must be an array")

    seen_ids: set[str] = set()
    for index, snippet in enumerate(snippets):
        _validate_snippet(snippet, f"$.snippets[{index}]", set(categories))
        snippet_id = snippet.get("id")
        if snippet_id in seen_ids:
            _fail(f"$.snippets[{index}]", f"duplicate snippet id {snippet_id!r}")
        seen_ids.add(snippet_id)
