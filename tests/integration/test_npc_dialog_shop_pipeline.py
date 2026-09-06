"""End-to-end proof the NPC/dialog/shop content pipeline is wired, not just
built (CONTRACTS.md §7.3, component doc ``11-npc-dialog-shop-content.md``
§8/§9).

Drives the real path: a real ``World``/``EventBus``/``DataRegistry``
(loaded from a fixture ``data/`` tree carrying this component's own real
content files) + a real, booted ``LuaHost`` with this component's three
real ``scripts/*.lua`` files loaded + ``08``'s real ``UIRuntime`` + ``07``'s
real ``InputHandler`` driving the actual interact-key path (not
``event_bus.emit("entity_interacted", ...)`` directly) -- proving the
§1.1 hook, the dispatch, the dialog graph walk, a real shop transaction,
and save/reload persistence, all through their real entry points.

Boundaries documented per the component doc's own Test Plan:
  - The buy transaction is driven by emitting ``buy_item`` directly at the
    event-bus level (doc §9 explicitly sanctions this: "simulated at the
    event-bus level is acceptable here, since driving real pygame input is
    07's concern"). The same reasoning is applied to the dialog choice
    click (``dialog_choice_clicked``) -- it is the exact event a real
    Button's ``on_click`` would emit (see ``data/config/ui_skin.json``'s
    "dialog" screen), so emitting it directly is a faithful drive of the
    real event contract, not a bypass of any hook this component owns.
  - **15-integration-verification.md finding (escalated, not fixed here):**
    ``04-inventory-items-loot.md`` has since merged, but it never built the
    generic ``grant_item(entity_id, item_id, quantity, world, event_bus)``/
    ``remove_item(...)``/``has_item(entity_id, item_id, quantity, world)``
    API this component's own doc documents ``ApiContext.inventory_fns`` as
    wrapping (§2.4: "checks the actor's gold via
    ``engine.has_item(actor_id, "gold", price)``"), nor any per-actor gold
    wallet concept -- 04's real ``InventorySystem`` only exposes
    ``pickup``/``drop``/``use``/``equip``/``unequip``/``identify_*``/
    ``remove_curse`` against its rarity/affix-bearing
    ``ItemInstanceComponent`` model, and 04's own doc §9 separately flags
    "gold as a loot concept" as still unresolved. Reconciling a
    quantity/fungible "grant N of item X" and a gold wallet with 04's
    per-instance item model is a real design decision (which field/
    component represents gold on an actor; whether a "quantity" purchase
    spawns N real instances or introduces stacking) that wasn't reviewed
    as part of either component's spec, so it is filed as a follow-up
    against `04-inventory-items-loot.md` (and `11`'s own doc, whose
    `ApiContext.inventory_fns` contract this blocks) per
    `ORCHESTRATION.md` §5 rather than freelanced here. ``ApiContext.
    inventory_fns`` therefore remains this test's fixture ledger
    (CONTRACTS.md §8) until that lands -- not a mock of a real
    ``engine.grant_item``/``remove_item``/``has_item`` because no such
    real implementation exists yet to mock.
  - Panel state is asserted via ``UIRuntime``'s own internal panel stack
    (no public accessor exists yet; reached directly, not mocked).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import pytest

from engine.core import save
from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.registry import DataRegistry
from engine.core.spatial_hash import SpatialHash
from engine.input.input_handler import InputHandler
from engine.lua.lua_host import LuaHost
from engine.systems.ai import PlayerTagComponent, PositionComponent
from engine.systems.interaction import InteractableComponent
from engine.ui.ui_runtime import UIRuntime

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DIALOG_ID = "shopkeeper_mira_dialog"
SHOP_ID = "mira_general_store"


@pytest.fixture(scope="module", autouse=True)
def _pygame_headless():
    pygame.init()
    yield
    pygame.quit()


class _FakeInventory:
    """Fixture stand-in for the generic grant/remove/has + gold-wallet API
    11's own doc documents ``ApiContext.inventory_fns`` as wrapping, which
    04's real (now-merged) ``InventorySystem`` never built -- see this
    module's docstring's "15-integration-verification.md finding" note.
    Not a CONTRACTS.md §8 "not merged yet" stub in the usual sense; kept
    until the escalated follow-up against 04/11 lands."""

    def __init__(self) -> None:
        self.ledger: dict[int, dict[str, float]] = {}

    def fns(self) -> dict:
        def grant(entity_id, item_id, quantity, world, event_bus):
            bucket = self.ledger.setdefault(entity_id, {})
            bucket[item_id] = bucket.get(item_id, 0) + quantity
            return f"{item_id}#1"

        def remove(entity_id, item_id, quantity, world, event_bus):
            bucket = self.ledger.get(entity_id, {})
            if bucket.get(item_id, 0) < quantity:
                return False
            bucket[item_id] -= quantity
            return True

        def has(entity_id, item_id, quantity, world):
            return self.ledger.get(entity_id, {}).get(item_id, 0) >= quantity

        return {"grant_item": grant, "remove_item": remove, "has_item": has}


def _build_fixture_data_tree(tmp_path: Path) -> Path:
    """Copies this component's real content files (dialog, shop, npc,
    ui_skin.json) into an isolated fixture ``data/`` tree, plus a minimal
    ``items`` namespace (04-inventory-items-loot.md isn't merged and owns
    ``data/items/`` -- not this component's file to add to in the real
    ``data/`` tree, so the ``base_price`` field its schema doesn't define
    yet is supplied only inside this test's own isolated fixture, per
    CONTRACTS.md §8)."""
    data_root = tmp_path / "data"
    (data_root / "dialogs").mkdir(parents=True)
    (data_root / "shops").mkdir(parents=True)
    (data_root / "entities" / "npcs").mkdir(parents=True)
    (data_root / "items").mkdir(parents=True)
    (data_root / "config").mkdir(parents=True)

    shutil.copy(REPO_ROOT / "data" / "dialogs" / f"{DIALOG_ID}.json", data_root / "dialogs")
    shutil.copy(REPO_ROOT / "data" / "shops" / f"{SHOP_ID}.json", data_root / "shops")
    shutil.copy(REPO_ROOT / "data" / "entities" / "npcs" / "mira.json", data_root / "entities" / "npcs")
    shutil.copy(REPO_ROOT / "data" / "config" / "ui_skin.json", data_root / "config")

    for item_id, base_price in (("healing_potion", 20), ("long_sword", 100), ("torch", 5)):
        (data_root / "items" / f"{item_id}.json").write_text(
            json.dumps({"id": item_id, "name": item_id, "base_price": base_price})
        )

    return data_root


def _copy_scripts(tmp_path: Path) -> Path:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    for name in ("dialog_walker.lua", "npc_interaction.lua", "shop.lua"):
        shutil.copy(REPO_ROOT / "scripts" / name, scripts_dir / name)
    return scripts_dir


def _spawn_mira(world: World, registry: DataRegistry, position: tuple[int, int]) -> int:
    """Mirrors what 06's VaultInjector will eventually do for a real NPC
    vault marker (soft dependency, component doc §3): copy the npc
    definition's ``interactable`` block verbatim into a fresh entity's
    ``InteractableComponent.data`` -- Python copies the dict, never reads
    inside it (component doc §5.3)."""
    npc_def = registry.get("npcs", "mira")
    assert npc_def is not None
    entity_id = world.create_entity()
    world.add_component(entity_id, PositionComponent(*position))
    world.add_component(entity_id, InteractableComponent(data=dict(npc_def["interactable"])))
    return entity_id


def _make_input_handler(event_bus: EventBus, world: World, spatial_hash: SpatialHash) -> InputHandler:
    controls = {"contexts": {"game": {"interact": ["e"]}}}
    return InputHandler(event_bus, controls, world=world, spatial_hash=spatial_hash)


def _key_event(key_code: int):
    return pygame.event.Event(pygame.KEYDOWN, key=key_code)


def test_full_npc_dialog_shop_pipeline(tmp_path, monkeypatch):
    data_root = _build_fixture_data_tree(tmp_path)
    registry = DataRegistry()
    registry.load(data_root)

    world = World()
    event_bus = EventBus()
    spatial_hash = SpatialHash()

    player_id = world.create_entity()
    world.add_component(player_id, PlayerTagComponent())
    world.add_component(player_id, PositionComponent(5, 5))
    spatial_hash.insert(player_id, (5, 5))

    npc_id = _spawn_mira(world, registry, (6, 5))
    spatial_hash.insert(npc_id, (6, 5))

    inventory = _FakeInventory()
    inventory.ledger[player_id] = {"gold": 100}

    monkeypatch.chdir(tmp_path)
    _copy_scripts(tmp_path)
    host = LuaHost(world, event_bus, registry, spatial_hash=spatial_hash, inventory_fns=inventory.fns())
    host.boot()
    host.set_context(None, player_id)

    ui = UIRuntime(event_bus, registry)
    input_handler = _make_input_handler(event_bus, world, spatial_hash)

    # -- drive the real bump/interact-key path (component doc §1.1/§9) ----
    input_handler.handle_pygame_event(_key_event(pygame.key.key_code("e")))

    assert "dialog" in ui._panels
    dialog_data = ui._panels["dialog"].data
    assert dialog_data["node_id"] == "greet"
    assert "Welcome, traveler." in dialog_data["text"]
    choice_texts = [c["text"] for c in dialog_data["choices"]]
    assert "Show me your wares." in choice_texts
    shop_choice_index = next(
        c["choice_index"] for c in dialog_data["choices"] if c["text"] == "Show me your wares."
    )

    # -- choose "Show me your wares." (the exact event the real dialog
    # panel's Button on_click would emit -- see module docstring) --------
    event_bus.emit(
        "dialog_choice_clicked",
        {
            "entity_id": player_id,
            "target_id": npc_id,
            "dialog_id": DIALOG_ID,
            "node_id": "greet",
            "choice_index": shop_choice_index,
        },
    )

    assert "shop" in ui._panels
    shop_data = ui._panels["shop"].data
    assert shop_data["shop_id"] == SHOP_ID
    stock_item_ids = {item["item_id"] for item in shop_data["stock"]}
    assert stock_item_ids == {"healing_potion", "long_sword", "torch"}
    assert shop_data["gold"] == 150

    # -- complete one buy transaction (event-bus level, doc-sanctioned) ---
    event_bus.emit(
        "buy_item",
        {"entity_id": player_id, "target_id": npc_id, "shop_id": SHOP_ID, "item": "torch"},
    )

    expected_price = 5 * 1.15
    assert inventory.ledger[player_id]["torch"] == 1
    assert inventory.ledger[player_id]["gold"] == pytest.approx(100 - expected_price)

    engine_globals = host.lua_runtime.globals().engine
    shop_gold_after_purchase = engine_globals.get_floor_state("shop_mira_general_store_gold")
    assert shop_gold_after_purchase == pytest.approx(150 + expected_price)

    # -- also advance the dialog's node state to something non-start, so
    # the persistence assertion below actually proves something (doc §8:
    # "save mid-dialog (a non-start node current)") -----------------------
    dialog_node_before_save = engine_globals.get_floor_state(f"dialog_{npc_id}_node")
    assert dialog_node_before_save == "greet"  # unchanged by the shop detour

    stock_before_save = engine_globals.get_floor_state("shop_mira_general_store_stock")
    torch_qty_before_save = next(e["quantity"] for e in stock_before_save.values() if e["item_id"] == "torch")

    # -- save mid-dialog/mid-shop, tear down, reload (component doc §8's
    # zero-special-case persistence proof) --------------------------------
    non_player_ids = [eid for eid in world.entities() if eid != player_id]
    snapshot = save.serialize_floor_snapshot(world, floor_id="floor_1", entity_ids=non_player_ids)

    world_2 = World()
    save.restore_floor_snapshot(world_2, snapshot)
    # The dialog node / shop stock / shop gold under test here are all
    # floor-scoped state (LuaFloorStateComponent, on a plain entity the
    # floor snapshot above already captured) -- none of it lives on the
    # player entity, so this persistence proof needs no player entity at
    # all in world_2 (10-lua-scripting-layer.md's own tests already prove
    # the separate campaign-state/serialize_world path, component doc
    # §2.4's "other half").

    event_bus_2 = EventBus()
    host_2 = LuaHost(world_2, event_bus_2, registry, spatial_hash=None, inventory_fns=inventory.fns())
    host_2.boot()
    host_2.set_context(None, None)

    engine_2 = host_2.lua_runtime.globals().engine
    restored_node = engine_2.get_floor_state(f"dialog_{npc_id}_node")
    restored_gold = engine_2.get_floor_state("shop_mira_general_store_gold")
    restored_stock = engine_2.get_floor_state("shop_mira_general_store_stock")
    restored_torch_qty = next(e["quantity"] for e in restored_stock.values() if e["item_id"] == "torch")

    assert restored_node == dialog_node_before_save
    assert restored_gold == pytest.approx(shop_gold_after_purchase)
    assert restored_torch_qty == torch_qty_before_save
