import random

import pytest

from engine.core.formula import eval_formula, roll_dice


def test_roll_dice_basic_range():
    rng = random.Random(0)
    for _ in range(200):
        result = roll_dice("3d6", rng=rng)
        assert 3 <= result <= 18


def test_roll_dice_with_positive_modifier():
    rng = random.Random(0)
    for _ in range(200):
        result = roll_dice("1d8+2", rng=rng)
        assert 3 <= result <= 10


def test_roll_dice_with_negative_modifier():
    rng = random.Random(0)
    for _ in range(200):
        result = roll_dice("2d4-1", rng=rng)
        assert 1 <= result <= 7


def test_roll_dice_implicit_count_of_one():
    rng = random.Random(0)
    for _ in range(200):
        result = roll_dice("d20", rng=rng)
        assert 1 <= result <= 20


def test_roll_dice_is_deterministic_with_seeded_rng():
    a = roll_dice("3d6", rng=random.Random(42))
    b = roll_dice("3d6", rng=random.Random(42))
    assert a == b


def test_roll_dice_invalid_expression_raises():
    with pytest.raises(ValueError):
        roll_dice("not a dice expression")


def test_roll_dice_zero_sides_raises():
    with pytest.raises(ValueError):
        roll_dice("1d0")


def test_roll_dice_zero_count_raises():
    with pytest.raises(ValueError):
        roll_dice("0d6")


def test_eval_formula_simple_arithmetic():
    assert eval_formula("2 + 2", {}) == 4.0


def test_eval_formula_with_context():
    result = eval_formula("0.4 - (INT * 0.03)", {"INT": 10})
    assert result == pytest.approx(0.4 - 0.3)


def test_eval_formula_with_multiple_context_keys():
    result = eval_formula("STR * 2 + DEX", {"STR": 5, "DEX": 3})
    assert result == 13.0


def test_eval_formula_allows_whitelisted_functions():
    assert eval_formula("min(DEX, 20)", {"DEX": 30}) == 20.0
    assert eval_formula("max(DEX, 20)", {"DEX": 5}) == 20.0
    assert eval_formula("abs(-3)", {}) == 3.0
    assert eval_formula("round(3.6)", {}) == 4.0


def test_eval_formula_unknown_name_rejected():
    with pytest.raises(ValueError):
        eval_formula("UNKNOWN_STAT * 2", {"INT": 1})


def test_eval_formula_rejects_attribute_access():
    """No arbitrary attribute access — a formula reaching for an object's
    attributes (even an innocuous-looking one) must be rejected, not
    executed (component doc §2.6 / this component's Definition of Done)."""
    with pytest.raises(ValueError):
        eval_formula("INT.bit_length()", {"INT": 5})


def test_eval_formula_rejects_dunder_access():
    with pytest.raises(ValueError):
        eval_formula("INT.__class__", {"INT": 5})


def test_eval_formula_rejects_import_attempt():
    with pytest.raises(ValueError):
        eval_formula("__import__('os').system('echo hi')", {})


def test_eval_formula_rejects_non_numeric_result():
    with pytest.raises(ValueError):
        eval_formula("str(INT)", {"INT": 5})
