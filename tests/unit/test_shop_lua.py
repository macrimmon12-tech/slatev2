"""``scripts/shop.lua`` unit tests (component doc
``11-npc-dialog-shop-content.md`` §8/§9): price-formula evaluation,
weighted-stock-roll determinism under a fixed RNG seed, restock-interval
counting, and a real buy/sell round trip through injected
``ApiContext.inventory_fns`` (04-inventory-items-loot.md isn't merged --
CONTRACTS.md §8 stubbing: mock the function boundary, not the real
module).
"""

from __future__ import annotations

import pytest

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.lua.lua_host import LuaHost
from engine.systems.ai import PlayerTagComponent

from tests.unit._npc_dialog_shop_lua_helpers import FixtureRegistry, copy_scripts_to

UI_SKIN_CONFIG = {
    "schema_version": 1,
    "screens": {
        "dialog": {"type": "Panel", "id": "dialog", "children": []},
        "shop": {"type": "Panel", "id": "shop", "children": []},
    },
}

SHOP_ID = "mira_general_store"
SHOP_FIXTURE = {
    "id": SHOP_ID,
    "starting_gold": 150,
    "restock_interval_floors": 3,
    "price_formula": {"buy": "base_price * 1.15", "sell": "base_price * 0.5"},
    "stock_table": [
        {"item_id": "healing_potion", "weight": 10, "restock_quantity": [3, 6]},
        {"item_id": "long_sword", "weight": 3, "restock_quantity": [1, 1]},
        {"item_id": "torch", "weight": 8, "restock_quantity": [4, 8]},
    ],
}
ITEMS_FIXTURE = {
    "healing_potion": {"id": "healing_potion", "base_price": 20},
    "long_sword": {"id": "long_sword", "base_price": 100},
    "torch": {"id": "torch", "base_price": 5},
}


class _FakeInventory:
    """Minimal fixture standing in for 04's InventorySystem (not merged) --
    a plain per-entity {item_id: quantity} ledger, wired into
    ApiContext.inventory_fns exactly as CONTRACTS.md §8 prescribes."""

    def __init__(self) -> None:
        self.ledger: dict[int, dict[str, float]] = {}
        self.grant_calls: list[tuple[int, str, float]] = []
        self.remove_calls: list[tuple[int, str, float]] = []

    def grant(self, actor_id, item_id, quantity, quantity2=None):
        self.grant_calls.append((actor_id, item_id, quantity))
        self.ledger.setdefault(actor_id, {})[item_id] = (
            self.ledger.setdefault(actor_id, {}).get(item_id, 0) + quantity
        )
        return f"{item_id}#1"

    def remove(self, actor_id, item_id, quantity):
        self.remove_calls.append((actor_id, item_id, quantity))
        have = self.ledger.get(actor_id, {}).get(item_id, 0)
        if have < quantity:
            return False
        self.ledger[actor_id][item_id] = have - quantity
        return True

    def has(self, actor_id, item_id, quantity):
        return self.ledger.get(actor_id, {}).get(item_id, 0) >= quantity

    def fns(self) -> dict:
        return {
            "grant_item": lambda entity_id, item_id, quantity, world, event_bus: self.grant(
                entity_id, item_id, quantity
            ),
            "remove_item": lambda entity_id, item_id, quantity, world, event_bus: self.remove(
                entity_id, item_id, quantity
            ),
            "has_item": lambda entity_id, item_id, quantity, world: self.has(entity_id, item_id, quantity),
        }


def _boot(tmp_path, monkeypatch, extra_registry=None, inventory=None):
    monkeypatch.chdir(tmp_path)
    copy_scripts_to(tmp_path)
    world = World()
    event_bus = EventBus()
    data = {
        "shops": {SHOP_ID: SHOP_FIXTURE},
        "items": ITEMS_FIXTURE,
        "configs": {"ui_skin": UI_SKIN_CONFIG},
    }
    if extra_registry:
        for namespace, entries in extra_registry.items():
            data.setdefault(namespace, {}).update(entries)
    registry = FixtureRegistry(data)

    inventory_fns = inventory.fns() if inventory is not None else None
    host = LuaHost(world, event_bus, registry, inventory_fns=inventory_fns)
    host.boot()
    player_id = world.create_entity()
    # PlayerTagComponent is required for LuaHost's own floor_changed
    # handler to keep campaign-scoped state bound to this entity across
    # floor transitions (see LuaHost._find_player_entity) -- otherwise
    # this component's own floor-transition-counter mechanism (see
    # shop.lua's module docstring) would lose its campaign_state_entity
    # the moment a test emits floor_changed.
    world.add_component(player_id, PlayerTagComponent())
    host.set_context(None, player_id)
    return host, world, event_bus, player_id


# -- price formula ------------------------------------------------------


def test_open_computes_buy_prices_from_base_price_and_formula(tmp_path, monkeypatch):
    host, world, bus, player_id = _boot(tmp_path, monkeypatch)
    slate = host.lua_runtime.globals().SLATE

    shown = []
    shop_opened = []
    bus.subscribe("show_panel", lambda p: shown.append(p))
    bus.subscribe("shop_opened", lambda p: shop_opened.append(p))

    slate.Shop.open(player_id, 99, SHOP_ID)

    assert shop_opened == [{"entity_id": player_id, "shop_id": SHOP_ID}]
    assert shown[0]["panel_id"] == "shop"
    prices = {item["item_id"]: item["price"] for item in shown[0]["data"]["stock"]}
    assert prices["healing_potion"] == pytest.approx(20 * 1.15)
    assert prices["long_sword"] == pytest.approx(100 * 1.15)
    assert prices["torch"] == pytest.approx(5 * 1.15)
    assert shown[0]["data"]["gold"] == 150


# -- stock generation determinism ----------------------------------------


def test_stock_quantities_are_deterministic_under_a_fixed_seed(tmp_path, monkeypatch):
    host, world, bus, player_id = _boot(tmp_path, monkeypatch)
    slate = host.lua_runtime.globals().SLATE
    engine = host.lua_runtime.globals().engine

    host.lua_runtime.execute("math.randomseed(1234)")
    slate.Shop.open(player_id, 99, SHOP_ID)
    first_stock = engine.get_floor_state("shop_mira_general_store_stock")
    first_quantities = {entry["item_id"]: entry["quantity"] for entry in first_stock.values()}

    # Rebuild a second, entirely independent host/world with the same seed
    # -- same quantities roll out, proving determinism under a fixed seed
    # (component doc §9's Test Plan).
    host_2, world_2, bus_2, player_id_2 = _boot(tmp_path, monkeypatch)
    slate_2 = host_2.lua_runtime.globals().SLATE
    engine_2 = host_2.lua_runtime.globals().engine
    host_2.lua_runtime.execute("math.randomseed(1234)")
    slate_2.Shop.open(player_id_2, 99, SHOP_ID)
    second_stock = engine_2.get_floor_state("shop_mira_general_store_stock")
    second_quantities = {entry["item_id"]: entry["quantity"] for entry in second_stock.values()}

    assert first_quantities == second_quantities
    for item_id, (lo, hi) in {
        "healing_potion": (3, 6), "long_sword": (1, 1), "torch": (4, 8)
    }.items():
        assert lo <= first_quantities[item_id] <= hi


# -- restock-interval counting -------------------------------------------


def test_restock_happens_only_after_the_configured_number_of_floor_transitions(tmp_path, monkeypatch):
    host, world, bus, player_id = _boot(tmp_path, monkeypatch)
    slate = host.lua_runtime.globals().SLATE
    engine = host.lua_runtime.globals().engine

    slate.Shop.open(player_id, 99, SHOP_ID)
    initial_stock = engine.get_floor_state("shop_mira_general_store_stock")

    refreshed = []
    bus.subscribe("shop_refreshed", lambda p: refreshed.append(p))

    # restock_interval_floors is 3 -- two floor_changed events aren't
    # enough yet.
    bus.emit("floor_changed", {"from_depth": 1, "to_depth": 2})
    bus.emit("floor_changed", {"from_depth": 2, "to_depth": 3})
    slate.Shop.open(player_id, 99, SHOP_ID)
    assert refreshed == []

    # The third floor transition crosses the threshold.
    bus.emit("floor_changed", {"from_depth": 3, "to_depth": 4})
    slate.Shop.open(player_id, 99, SHOP_ID)
    assert len(refreshed) == 1
    assert refreshed[0]["shop_id"] == SHOP_ID


def test_shop_with_no_restock_interval_never_restocks(tmp_path, monkeypatch):
    host, world, bus, player_id = _boot(
        tmp_path, monkeypatch, extra_registry={"shops": {SHOP_ID: {**SHOP_FIXTURE, "restock_interval_floors": 0}}}
    )
    slate = host.lua_runtime.globals().SLATE
    refreshed = []
    bus.subscribe("shop_refreshed", lambda p: refreshed.append(p))

    slate.Shop.open(player_id, 99, SHOP_ID)
    for _ in range(10):
        bus.emit("floor_changed", {})
    slate.Shop.open(player_id, 99, SHOP_ID)

    assert refreshed == []


# -- buy/sell via injected inventory_fns ----------------------------------


def test_buy_moves_gold_and_grants_item(tmp_path, monkeypatch):
    inventory = _FakeInventory()
    host, world, bus, player_id = _boot(tmp_path, monkeypatch, inventory=inventory)
    slate = host.lua_runtime.globals().SLATE
    engine = host.lua_runtime.globals().engine

    inventory.ledger[player_id] = {"gold": 100}
    slate.Shop.open(player_id, 99, SHOP_ID)

    trades = []
    bus.subscribe("trade_completed", lambda p: trades.append(p))

    bus.emit("buy_item", {"entity_id": player_id, "target_id": 99, "shop_id": SHOP_ID, "item": "torch"})

    assert inventory.grant_calls == [(player_id, "torch", 1)]
    assert inventory.remove_calls == [(player_id, "gold", pytest.approx(5.75))]
    assert len(trades) == 1
    assert trades[0]["item"] == "torch"
    assert trades[0]["price"] == pytest.approx(5.75)

    shop_gold = engine.get_floor_state("shop_mira_general_store_gold")
    assert shop_gold == pytest.approx(150 + 5.75)

    stock = engine.get_floor_state("shop_mira_general_store_stock")
    torch_entry = next(e for e in stock.values() if e["item_id"] == "torch")
    original_qty = next(
        e["restock_quantity"] for e in SHOP_FIXTURE["stock_table"] if e["item_id"] == "torch"
    )
    # Quantity decremented by exactly one from whatever was rolled.
    assert torch_entry["quantity"] >= original_qty[0] - 1


def test_buy_without_enough_gold_is_a_no_op(tmp_path, monkeypatch):
    inventory = _FakeInventory()
    host, world, bus, player_id = _boot(tmp_path, monkeypatch, inventory=inventory)
    slate = host.lua_runtime.globals().SLATE

    inventory.ledger[player_id] = {"gold": 0}
    slate.Shop.open(player_id, 99, SHOP_ID)

    trades = []
    bus.subscribe("trade_completed", lambda p: trades.append(p))
    bus.emit("buy_item", {"entity_id": player_id, "target_id": 99, "shop_id": SHOP_ID, "item": "torch"})

    assert trades == []
    assert inventory.grant_calls == []


def test_sell_moves_gold_from_shop_pool_and_restocks_item(tmp_path, monkeypatch):
    inventory = _FakeInventory()
    host, world, bus, player_id = _boot(tmp_path, monkeypatch, inventory=inventory)
    slate = host.lua_runtime.globals().SLATE
    engine = host.lua_runtime.globals().engine

    inventory.ledger[player_id] = {"long_sword": 1}
    slate.Shop.open(player_id, 99, SHOP_ID)

    trades = []
    bus.subscribe("trade_completed", lambda p: trades.append(p))
    bus.emit("sell_item", {"entity_id": player_id, "target_id": 99, "shop_id": SHOP_ID, "item": "long_sword"})

    expected_price = 100 * 0.5
    assert inventory.remove_calls == [(player_id, "long_sword", 1)]
    assert inventory.grant_calls == [(player_id, "gold", expected_price)]
    assert len(trades) == 1
    assert trades[0]["price"] == pytest.approx(expected_price)

    shop_gold = engine.get_floor_state("shop_mira_general_store_gold")
    assert shop_gold == pytest.approx(150 - expected_price)


def test_shop_out_of_gold_cannot_buy_from_the_player(tmp_path, monkeypatch):
    inventory = _FakeInventory()
    host, world, bus, player_id = _boot(
        tmp_path, monkeypatch, inventory=inventory,
        extra_registry={"shops": {SHOP_ID: {**SHOP_FIXTURE, "starting_gold": 0}}},
    )
    slate = host.lua_runtime.globals().SLATE

    inventory.ledger[player_id] = {"long_sword": 1}
    slate.Shop.open(player_id, 99, SHOP_ID)

    trades = []
    bus.subscribe("trade_completed", lambda p: trades.append(p))
    bus.emit("sell_item", {"entity_id": player_id, "target_id": 99, "shop_id": SHOP_ID, "item": "long_sword"})

    assert trades == []
    assert inventory.remove_calls == []


def test_leave_button_destroys_the_shop_panel(tmp_path, monkeypatch):
    host, world, bus, player_id = _boot(tmp_path, monkeypatch)
    slate = host.lua_runtime.globals().SLATE
    slate.Shop.open(player_id, 99, SHOP_ID)

    panel_closed = []
    bus.subscribe("panel_closed", lambda p: panel_closed.append(p))
    bus.emit("shop_leave_clicked", {"entity_id": player_id, "shop_id": SHOP_ID})

    assert panel_closed == [{"panel_id": "shop"}]
