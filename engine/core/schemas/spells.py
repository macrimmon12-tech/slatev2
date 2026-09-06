"""Hand-rolled schema validator for spell definitions (``data/spells/``,
the ``spells`` DataRegistry namespace).

Owned by ``03-spells-status.md``. See that doc §5.1 for the canonical shape
this validates and CONTRACTS.md §9 for why this module exists at all
(every new JSON schema ships a validator here plus a round-trip test).

``targeting_mode`` is a small closed set core to ``SpellSystem``'s state
machine, not an additive dispatch table like effect types — per the doc, a
typo'd/unknown mode is a content bug worth failing loudly on at load time,
not silently skipping. Effect *dicts* inside ``effects``/``backfire_effects``
are only shape-checked here (each needs a ``type`` string); the effect
*type* vocabulary itself is 01's dispatch table, not something this
validator polices.
"""

from __future__ import annotations

from typing import Any

VALID_TARGETING_MODES = frozenset(
    {"self", "aoe_self", "single_enemy", "targeted_tile", "aoe_targeted"}
)
_AOE_MODES = frozenset({"aoe_self", "aoe_targeted"})


class SpellSchemaError(ValueError):
    """Raised when a spell definition fails validation."""


def _require_str(data: dict, field: str, source: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise SpellSchemaError(
            f"{source}: spell field {field!r} must be a non-empty string, got {value!r}"
        )
    return value


def _require_number_or_formula(data: dict, field: str, source: str, default: Any = None) -> Any:
    value = data.get(field, default)
    if value is None:
        raise SpellSchemaError(f"{source}: spell field {field!r} is required")
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SpellSchemaError(
            f"{source}: spell field {field!r} must be a number or formula string, got {value!r}"
        )
    return value


def _require_effect_list(data: dict, field: str, source: str) -> list[dict]:
    value = data.get(field, [])
    if not isinstance(value, list):
        raise SpellSchemaError(f"{source}: spell field {field!r} must be a list, got {value!r}")
    for i, entry in enumerate(value):
        if not isinstance(entry, dict) or not isinstance(entry.get("type"), str):
            raise SpellSchemaError(
                f"{source}: {field}[{i}] must be an effect dict with a string 'type' field, got {entry!r}"
            )
    return value


def validate_spell(data: dict, source: str = "<unknown>") -> None:
    """Validate a spell definition dict. Raises ``SpellSchemaError`` with a
    message naming ``source`` (typically the file path) on the first
    problem found."""
    if not isinstance(data, dict):
        raise SpellSchemaError(f"{source}: spell definition must be a JSON object")

    _require_str(data, "id", source)
    _require_str(data, "display_name", source)

    targeting_mode = _require_str(data, "targeting_mode", source)
    if targeting_mode not in VALID_TARGETING_MODES:
        raise SpellSchemaError(
            f"{source}: unknown targeting_mode {targeting_mode!r}; "
            f"must be one of {sorted(VALID_TARGETING_MODES)}"
        )

    _require_number_or_formula(data, "mp_cost", source)

    int_requirement = data.get("int_requirement", 0)
    if isinstance(int_requirement, bool) or not isinstance(int_requirement, (int, float)):
        raise SpellSchemaError(
            f"{source}: spell field 'int_requirement' must be a number, got {int_requirement!r}"
        )

    if "fail_chance_formula" in data and not isinstance(data["fail_chance_formula"], str):
        raise SpellSchemaError(f"{source}: spell field 'fail_chance_formula' must be a string")

    aoe_radius = data.get("aoe_radius", 0)
    if isinstance(aoe_radius, bool) or not isinstance(aoe_radius, (int, float)):
        raise SpellSchemaError(f"{source}: spell field 'aoe_radius' must be a number")
    if targeting_mode in _AOE_MODES and aoe_radius <= 0:
        raise SpellSchemaError(
            f"{source}: targeting_mode {targeting_mode!r} requires aoe_radius > 0, got {aoe_radius!r}"
        )

    _require_effect_list(data, "backfire_effects", source)
    _require_effect_list(data, "effects", source)
