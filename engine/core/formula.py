"""Formula evaluation — dice notation and stat-driven arithmetic.

The **only** place in the codebase allowed to touch ``simpleeval`` directly
(CONTRACTS.md §6); every system evaluating a data-driven numeric field
(damage rolls, scaling formulas, anything an item/spell/effect JSON
supplies as a string instead of a hardcoded number — CONTRACTS.md §2 rule
8) imports :func:`roll_dice`/:func:`eval_formula` from here rather than
hand-parsing or calling ``simpleeval``/``eval`` itself.

``eval_formula``'s ``context`` keys are conventionally the entity's derived
stat names in UPPERCASE (``INT``, ``STR``, ``DEX``, ...) — see
CONTRACTS.md §6. Any system introducing a new formula field documents the
context keys it supplies in its own component doc.
"""

from __future__ import annotations

import ast
import random
import re

from simpleeval import EvalWithCompoundTypes

_DICE_RE = re.compile(r"^\s*(\d*)\s*d\s*(\d+)\s*([+-]\s*\d+)?\s*$", re.IGNORECASE)

# Deliberately small: just enough for balance-authoring formulas
# ("0.4 - (INT * 0.03)", "min(DEX, 20) * 1.5") without exposing anything
# that touches randomness, I/O, or object internals from a formula string.
_ALLOWED_FUNCTIONS = {"abs": abs, "round": round, "min": min, "max": max}


def roll_dice(expr: str, rng: random.Random | None = None) -> int:
    """Evaluate a dice expression: ``"3d6"``, ``"1d8+2"``, ``"d20"``,
    ``"2d4-1"``. Raises ``ValueError`` if ``expr`` isn't valid dice
    notation.

    ``rng`` defaults to a fresh ``random.Random()`` per call if omitted;
    pass a shared ``random.Random`` for deterministic/seeded rolls (tests,
    replay, etc).
    """
    match = _DICE_RE.match(expr)
    if not match:
        raise ValueError(f"Invalid dice expression: {expr!r}")

    count_str, sides_str, modifier_str = match.groups()
    count = int(count_str) if count_str else 1
    sides = int(sides_str)
    modifier = int(modifier_str.replace(" ", "")) if modifier_str else 0

    if count <= 0:
        raise ValueError(f"Invalid dice expression (count must be positive): {expr!r}")
    if sides <= 0:
        raise ValueError(f"Invalid dice expression (sides must be positive): {expr!r}")

    roller = rng if rng is not None else random.Random()
    total = sum(roller.randint(1, sides) for _ in range(count))
    return total + modifier


class _SafeFormulaEval(EvalWithCompoundTypes):
    """A ``simpleeval`` evaluator with attribute access hard-disabled.

    simpleeval already refuses ``_``/``__``-prefixed names, but a formula
    like ``"INT.bit_length()"`` would otherwise be allowed through as a
    non-underscore attribute access. Formula fields only ever need plain
    arithmetic over the supplied stat context, so attribute access of any
    kind is removed entirely rather than merely underscore-filtered.
    """

    def __init__(self, names: dict[str, float]) -> None:
        super().__init__(names=names, functions=dict(_ALLOWED_FUNCTIONS))
        self.nodes.pop(ast.Attribute, None)


def eval_formula(expr: str, context: dict[str, float]) -> float:
    """Evaluate an arithmetic formula string against ``context`` (stat name
    -> value). Raises ``ValueError`` for anything unsafe or malformed:
    unknown names, attribute access, import attempts, or a result that
    isn't a plain number.
    """
    evaluator = _SafeFormulaEval(names=dict(context))
    try:
        result = evaluator.eval(expr)
    except Exception as exc:  # simpleeval raises its own exception hierarchy
        raise ValueError(f"Invalid formula expression {expr!r}: {exc}") from exc

    if isinstance(result, bool) or not isinstance(result, (int, float)):
        raise ValueError(
            f"Formula {expr!r} did not evaluate to a number (got {result!r})"
        )
    return float(result)
