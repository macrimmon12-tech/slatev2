"""``scripts/npc_interaction.lua`` unit tests (component doc
``11-npc-dialog-shop-content.md`` §8/§9): dispatch on
``InteractableComponent.data.kind``, and the documented no-op cases
(absent component, unrecognized kind).
"""

from __future__ import annotations

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.lua.lua_host import LuaHost
from engine.systems.interaction import InteractableComponent

from tests.unit._npc_dialog_shop_lua_helpers import FixtureRegistry, copy_scripts_to

UI_SKIN_CONFIG = {
    "schema_version": 1,
    "screens": {
        "dialog": {"type": "Panel", "id": "dialog", "children": []},
        "shop": {"type": "Panel", "id": "shop", "children": []},
    },
}


def _boot(tmp_path, monkeypatch, registry=None):
    monkeypatch.chdir(tmp_path)
    copy_scripts_to(tmp_path)
    world = World()
    event_bus = EventBus()
    registry = registry if registry is not None else FixtureRegistry({"configs": {"ui_skin": UI_SKIN_CONFIG}})
    host = LuaHost(world, event_bus, registry)
    host.boot()
    host.set_context(None, None)
    return host, world, event_bus


def test_bump_into_non_interactable_entity_is_a_no_op(tmp_path, monkeypatch):
    host, world, bus = _boot(tmp_path, monkeypatch)
    target_id = world.create_entity()  # no InteractableComponent

    shown = []
    bus.subscribe("show_panel", lambda p: shown.append(p))

    bus.emit("entity_interacted", {"actor_id": 1, "target_id": target_id})

    assert shown == []


def test_shop_kind_dispatches_directly_to_shop_open(tmp_path, monkeypatch):
    registry = FixtureRegistry({
        "configs": {"ui_skin": UI_SKIN_CONFIG},
        "shops": {
            "general_store": {
                "id": "general_store",
                "starting_gold": 10,
                "price_formula": {"buy": "base_price", "sell": "base_price"},
                "stock_table": [{"item_id": "torch", "weight": 1, "restock_quantity": [1, 1]}],
            }
        },
        "items": {"torch": {"id": "torch", "base_price": 5}},
    })
    host, world, bus = _boot(tmp_path, monkeypatch, registry=registry)

    target_id = world.create_entity()
    world.add_component(target_id, InteractableComponent(data={"kind": "shop", "shop_id": "general_store"}))

    shop_opened = []
    bus.subscribe("shop_opened", lambda p: shop_opened.append(p))

    bus.emit("entity_interacted", {"actor_id": 1, "target_id": target_id})

    assert shop_opened == [{"entity_id": 1, "shop_id": "general_store"}]


def test_unrecognized_kind_does_not_open_any_panel(tmp_path, monkeypatch):
    host, world, bus = _boot(tmp_path, monkeypatch)
    target_id = world.create_entity()
    world.add_component(target_id, InteractableComponent(data={"kind": "lever"}))

    shown = []
    bus.subscribe("show_panel", lambda p: shown.append(p))

    bus.emit("entity_interacted", {"actor_id": 1, "target_id": target_id})

    assert shown == []


def test_npc_kind_falls_back_to_npc_id_registry_lookup(tmp_path, monkeypatch):
    registry = FixtureRegistry({
        "configs": {"ui_skin": UI_SKIN_CONFIG},
        "npcs": {"mira": {"id": "mira", "interactable": {"kind": "npc", "dialog_id": "shopkeeper_mira_dialog"}}},
        "dialogs": {
            "shopkeeper_mira_dialog": {
                "id": "shopkeeper_mira_dialog",
                "start_node": "greet",
                "nodes": {"greet": {"text": "hi", "choices": []}},
            }
        },
    })
    host, world, bus = _boot(tmp_path, monkeypatch, registry=registry)
    target_id = world.create_entity()
    # No dialog_id directly on the interactable -- resolved via npc_id's
    # own registry entry (component doc §2.2).
    world.add_component(target_id, InteractableComponent(data={"kind": "npc", "npc_id": "mira"}))

    opened = []
    bus.subscribe("dialog_opened", lambda p: opened.append(p))

    bus.emit("entity_interacted", {"actor_id": 1, "target_id": target_id})

    assert opened == [
        {"entity_id": 1, "target_id": target_id, "dialog_id": "shopkeeper_mira_dialog", "node_id": "greet"}
    ]
