"""Schema validator + round-trip tests for the dialog/shop JSON formats
(component doc ``11-npc-dialog-shop-content.md`` §5, CONTRACTS.md §9).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.core.schemas.dialog_schema import DialogSchemaError, validate_dialog_graph
from engine.core.schemas.shop_schema import ShopSchemaError, validate_shop_config
from engine.core.schemas.ui_skin_schema import validate_ui_skin_config

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DIALOGS_DIR = REPO_ROOT / "data" / "dialogs"
SHOPS_DIR = REPO_ROOT / "data" / "shops"


def test_ui_skin_dialog_and_shop_screens_are_valid_widget_trees():
    """This component's PR added "dialog"/"shop" screen entries to the
    shared ``data/config/ui_skin.json`` (component doc §4 -- 08 hadn't
    authored generic ones yet). Validates the *whole* file still passes
    08's own schema, not just these two new entries in isolation."""
    config = json.loads((REPO_ROOT / "data" / "config" / "ui_skin.json").read_text())
    validate_ui_skin_config(config)
    assert "dialog" in config["screens"]
    assert "shop" in config["screens"]


# -- round trip over every real fixture file under data/ ---------------------


@pytest.mark.parametrize("path", sorted(DIALOGS_DIR.glob("*.json")), ids=lambda p: p.name)
def test_every_dialog_fixture_validates(path: Path):
    validate_dialog_graph(json.loads(path.read_text()))


@pytest.mark.parametrize("path", sorted(SHOPS_DIR.glob("*.json")), ids=lambda p: p.name)
def test_every_shop_fixture_validates(path: Path):
    validate_shop_config(json.loads(path.read_text()))


def test_at_least_one_real_dialog_and_shop_fixture_exists():
    assert list(DIALOGS_DIR.glob("*.json"))
    assert list(SHOPS_DIR.glob("*.json"))


# -- dialog schema: malformed shapes -----------------------------------------


def _base_dialog() -> dict:
    return {
        "id": "d1",
        "start_node": "greet",
        "nodes": {"greet": {"text": "hi", "choices": []}},
    }


def test_dialog_requires_start_node_present_in_nodes():
    graph = _base_dialog()
    graph["start_node"] = "nowhere"
    with pytest.raises(DialogSchemaError):
        validate_dialog_graph(graph)


def test_dialog_node_requires_choices_key():
    graph = _base_dialog()
    del graph["nodes"]["greet"]["choices"]
    with pytest.raises(DialogSchemaError):
        validate_dialog_graph(graph)


def test_choice_must_have_exactly_one_of_goto_or_action():
    graph = _base_dialog()
    graph["nodes"]["greet"]["choices"] = [{"text": "x", "goto": "greet", "action": "close"}]
    with pytest.raises(DialogSchemaError):
        validate_dialog_graph(graph)

    graph["nodes"]["greet"]["choices"] = [{"text": "x"}]
    with pytest.raises(DialogSchemaError):
        validate_dialog_graph(graph)


def test_choice_goto_must_reference_known_node():
    graph = _base_dialog()
    graph["nodes"]["greet"]["choices"] = [{"text": "x", "goto": "nowhere"}]
    with pytest.raises(DialogSchemaError):
        validate_dialog_graph(graph)


def test_choice_open_shop_action_requires_shop_id():
    graph = _base_dialog()
    graph["nodes"]["greet"]["choices"] = [{"text": "x", "action": "open_shop"}]
    with pytest.raises(DialogSchemaError):
        validate_dialog_graph(graph)


def test_choice_unknown_action_rejected():
    graph = _base_dialog()
    graph["nodes"]["greet"]["choices"] = [{"text": "x", "action": "teleport"}]
    with pytest.raises(DialogSchemaError):
        validate_dialog_graph(graph)


def test_on_enter_directive_requires_exactly_one_key_and_value():
    graph = _base_dialog()
    graph["nodes"]["greet"]["on_enter"] = [{"value": True}]
    with pytest.raises(DialogSchemaError):
        validate_dialog_graph(graph)

    graph["nodes"]["greet"]["on_enter"] = [
        {"campaign_state": "x", "floor_state": "y", "value": True}
    ]
    with pytest.raises(DialogSchemaError):
        validate_dialog_graph(graph)


def test_valid_on_enter_directive_passes():
    graph = _base_dialog()
    graph["nodes"]["greet"]["on_enter"] = [{"campaign_state": "flag", "value": True}]
    validate_dialog_graph(graph)


# -- shop schema: malformed shapes -------------------------------------------


def _base_shop() -> dict:
    return {
        "id": "s1",
        "starting_gold": 100,
        "restock_interval_floors": 3,
        "price_formula": {"buy": "base_price * 1.1", "sell": "base_price * 0.5"},
        "stock_table": [
            {"item_id": "torch", "weight": 1, "restock_quantity": [1, 2]},
        ],
    }


def test_valid_shop_passes():
    validate_shop_config(_base_shop())


def test_shop_requires_price_formula_buy_and_sell():
    shop = _base_shop()
    del shop["price_formula"]["sell"]
    with pytest.raises(ShopSchemaError):
        validate_shop_config(shop)


def test_shop_stock_entry_requires_restock_quantity_range():
    shop = _base_shop()
    shop["stock_table"][0]["restock_quantity"] = [5, 1]
    with pytest.raises(ShopSchemaError):
        validate_shop_config(shop)


def test_shop_requires_non_empty_stock_table():
    shop = _base_shop()
    shop["stock_table"] = []
    with pytest.raises(ShopSchemaError):
        validate_shop_config(shop)


def test_shop_negative_starting_gold_rejected():
    shop = _base_shop()
    shop["starting_gold"] = -1
    with pytest.raises(ShopSchemaError):
        validate_shop_config(shop)
