"""``data/shops/*.json`` schema validator (component doc
``11-npc-dialog-shop-content.md`` §5.2).

No ``jsonschema`` dependency — see ``engine/core/schemas/__init__.py``/
``dialog_schema.py`` for why every validator in this package is a small
hand-rolled function instead, and for the same filename-naming deviation
from the component doc's literal ``shop.schema.json`` guess (§4), flagged
in this component's PR.
"""

from __future__ import annotations

from typing import Any

REQUIRED_PRICE_FORMULA_KEYS = ("buy", "sell")


class ShopSchemaError(ValueError):
    """Raised by :func:`validate_shop_config` for a malformed shop
    definition. The message names the offending field/path."""


def _fail(path: str, message: str) -> None:
    raise ShopSchemaError(f"{path}: {message}")


def _validate_restock_quantity(value: Any, path: str) -> None:
    if not isinstance(value, list) or len(value) != 2:
        _fail(path, "'restock_quantity' must be a 2-element [min, max] array")
    lo, hi = value
    if not isinstance(lo, int) or isinstance(lo, bool) or not isinstance(hi, int) or isinstance(hi, bool):
        _fail(path, "'restock_quantity' entries must be integers")
    if lo < 0 or hi < lo:
        _fail(path, f"'restock_quantity' must satisfy 0 <= min <= max, got {value!r}")


def _validate_stock_entry(entry: Any, path: str) -> None:
    if not isinstance(entry, dict):
        _fail(path, f"stock_table entry must be an object, got {type(entry).__name__}")
    if not isinstance(entry.get("item_id"), str) or not entry["item_id"]:
        _fail(path, "stock_table entry requires a non-empty string 'item_id'")
    weight = entry.get("weight")
    if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight < 0:
        _fail(path, "stock_table entry requires a non-negative numeric 'weight'")
    _validate_restock_quantity(entry.get("restock_quantity"), f"{path}.restock_quantity")


def validate_shop_config(config: Any) -> None:
    """Validate one ``data/shops/*.json`` file's top-level shape (doc
    §5.2): ``{id, starting_gold, restock_interval_floors, price_formula,
    stock_table}``. Raises :class:`ShopSchemaError` on the first problem
    found."""
    if not isinstance(config, dict):
        _fail("$", f"shop config must be an object, got {type(config).__name__}")

    if not isinstance(config.get("id"), str) or not config["id"]:
        _fail("$", "shop config requires a non-empty string 'id'")

    starting_gold = config.get("starting_gold")
    if not isinstance(starting_gold, (int, float)) or isinstance(starting_gold, bool) or starting_gold < 0:
        _fail("$", "shop config requires a non-negative numeric 'starting_gold'")

    restock = config.get("restock_interval_floors")
    if restock is not None and (
        not isinstance(restock, (int, float)) or isinstance(restock, bool) or restock < 0
    ):
        _fail("$", "'restock_interval_floors', if present, must be a non-negative number")

    price_formula = config.get("price_formula")
    if not isinstance(price_formula, dict):
        _fail("$.price_formula", "'price_formula' must be an object")
    for key in REQUIRED_PRICE_FORMULA_KEYS:
        if not isinstance(price_formula.get(key), str) or not price_formula[key]:
            _fail("$.price_formula", f"'price_formula.{key}' must be a non-empty string")

    stock_table = config.get("stock_table")
    if not isinstance(stock_table, list) or not stock_table:
        _fail("$.stock_table", "'stock_table' must be a non-empty array")
    for index, entry in enumerate(stock_table):
        _validate_stock_entry(entry, f"$.stock_table[{index}]")
