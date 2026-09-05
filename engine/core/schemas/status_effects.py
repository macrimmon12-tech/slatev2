"""Hand-rolled schema validator for status effect definitions — entries in
the ``effects`` DataRegistry namespace (``data/effects/``) that carry
``on_apply``/``on_tick`` lists (03-spells-status.md §5.2), as opposed to
other content 01 may store in the same shared namespace.

Owned by ``03-spells-status.md``. See CONTRACTS.md §9 for why this module
exists (every new JSON schema ships a validator here plus a round-trip
test).
"""

from __future__ import annotations

_VALID_OPERATIONS = frozenset({"add", "multiply"})


class StatusEffectSchemaError(ValueError):
    """Raised when a status effect definition fails validation."""


def _require_str(data: dict, field: str, source: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise StatusEffectSchemaError(
            f"{source}: status field {field!r} must be a non-empty string, got {value!r}"
        )
    return value


def _require_effect_list(data: dict, field: str, source: str) -> list[dict]:
    value = data.get(field, [])
    if not isinstance(value, list):
        raise StatusEffectSchemaError(
            f"{source}: status field {field!r} must be a list, got {value!r}"
        )
    for i, entry in enumerate(value):
        if not isinstance(entry, dict) or not isinstance(entry.get("type"), str):
            raise StatusEffectSchemaError(
                f"{source}: {field}[{i}] must be an effect dict with a string 'type' field, got {entry!r}"
            )
    return value


def validate_status_effect(data: dict, source: str = "<unknown>") -> None:
    """Validate a status effect definition dict. Raises
    ``StatusEffectSchemaError`` naming ``source`` on the first problem
    found."""
    if not isinstance(data, dict):
        raise StatusEffectSchemaError(f"{source}: status definition must be a JSON object")

    _require_str(data, "id", source)
    _require_str(data, "display_name", source)

    _require_effect_list(data, "on_apply", source)
    _require_effect_list(data, "on_tick", source)

    stat_modifiers = data.get("stat_modifiers", [])
    if not isinstance(stat_modifiers, list):
        raise StatusEffectSchemaError(f"{source}: status field 'stat_modifiers' must be a list")
    for i, entry in enumerate(stat_modifiers):
        if not isinstance(entry, dict):
            raise StatusEffectSchemaError(
                f"{source}: stat_modifiers[{i}] must be an object, got {entry!r}"
            )
        stat = entry.get("stat")
        operation = entry.get("operation")
        value = entry.get("value")
        if not isinstance(stat, str) or not stat:
            raise StatusEffectSchemaError(
                f"{source}: stat_modifiers[{i}].stat must be a non-empty string"
            )
        if operation not in _VALID_OPERATIONS:
            raise StatusEffectSchemaError(
                f"{source}: stat_modifiers[{i}].operation must be one of "
                f"{sorted(_VALID_OPERATIONS)}, got {operation!r}"
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise StatusEffectSchemaError(
                f"{source}: stat_modifiers[{i}].value must be a number, got {value!r}"
            )
