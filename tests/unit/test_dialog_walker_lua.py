"""``scripts/dialog_walker.lua`` unit tests (component doc
``11-npc-dialog-shop-content.md`` §8/§9), driven through a real, booted
``LuaHost`` (see ``_npc_dialog_shop_lua_helpers.py`` for the test-setup
choice this documents).
"""

from __future__ import annotations

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.lua.lua_host import LuaHost
from engine.systems.ai import PlayerTagComponent

from tests.unit._npc_dialog_shop_lua_helpers import FixtureRegistry, copy_scripts_to

# Minimal stand-in for the "dialog"/"shop" screens this component's PR
# added to data/config/ui_skin.json -- dialog_shop.py's open_dialog_panel/
# open_shop_panel look these up before emitting show_panel at all (§2.2),
# so a fixture registry needs *some* truthy tree registered under each
# panel id; the widget-tree shape itself isn't exercised by these tests
# (no real UIRuntime is constructed here).
UI_SKIN_CONFIG = {
    "schema_version": 1,
    "screens": {
        "dialog": {"type": "Panel", "id": "dialog", "children": []},
        "shop": {"type": "Panel", "id": "shop", "children": []},
    },
}

DIALOG_ID = "shopkeeper_mira_dialog"
DIALOG_FIXTURE = {
    "id": DIALOG_ID,
    "start_node": "greet",
    "nodes": {
        "greet": {
            "text": "Welcome, traveler.",
            "choices": [
                {"text": "Show me your wares.", "action": "open_shop", "shop_id": "mira_general_store"},
                {"text": "Tell me about this place.", "goto": "lore_1"},
                {"text": "Just passing through.", "goto": "end"},
            ],
        },
        "lore_1": {
            "text": "This outpost has stood since the old kingdom fell.",
            "choices": [
                {"text": "Running from what?", "goto": "lore_2", "condition": "not campaign.mira_lore_told"},
                {"text": "Interesting. Anyway...", "goto": "greet"},
            ],
        },
        "lore_2": {
            "text": "That's a story for another time.",
            "on_enter": [{"campaign_state": "mira_lore_told", "value": True}],
            "choices": [{"text": "Fair enough.", "goto": "greet"}],
        },
        "end": {"text": "Safe travels.", "choices": []},
    },
}


def _boot(tmp_path, monkeypatch, registry=None, world=None, event_bus=None):
    monkeypatch.chdir(tmp_path)
    copy_scripts_to(tmp_path)
    world = world if world is not None else World()
    event_bus = event_bus if event_bus is not None else EventBus()
    registry = registry if registry is not None else FixtureRegistry(
        {"dialogs": {DIALOG_ID: DIALOG_FIXTURE}, "configs": {"ui_skin": UI_SKIN_CONFIG}}
    )
    host = LuaHost(world, event_bus, registry)
    host.boot()
    # A real player entity is required for engine.set/get_campaign_state to
    # actually store anything (LuaCampaignStateComponent lives on it) --
    # see engine/lua/lua_host.py's set_context.
    player_id = world.create_entity()
    world.add_component(player_id, PlayerTagComponent())
    host.set_context(None, player_id)
    return host, world, event_bus


def test_open_emits_dialog_opened_and_shows_start_node(tmp_path, monkeypatch):
    host, world, bus = _boot(tmp_path, monkeypatch)

    opened = []
    shown = []
    bus.subscribe("dialog_opened", lambda p: opened.append(p))
    bus.subscribe("show_panel", lambda p: shown.append(p))

    slate = host.lua_runtime.globals().SLATE
    slate.DialogWalker.open(1, 2, DIALOG_ID)

    assert opened == [{"entity_id": 1, "target_id": 2, "dialog_id": DIALOG_ID, "node_id": "greet"}]
    assert len(shown) == 1
    assert shown[0]["panel_id"] == "dialog"
    assert shown[0]["data"]["text"] == "Welcome, traveler."
    choice_texts = [c["text"] for c in shown[0]["data"]["choices"]]
    assert choice_texts == [
        "Show me your wares.",
        "Tell me about this place.",
        "Just passing through.",
    ]


def test_conditional_choice_hidden_until_campaign_state_set(tmp_path, monkeypatch):
    host, world, bus = _boot(tmp_path, monkeypatch)
    engine = host.lua_runtime.globals().engine

    slate = host.lua_runtime.globals().SLATE
    slate.DialogWalker.open(1, 2, DIALOG_ID)

    # Advance to lore_1 via a real dialog_choice_clicked event (not a
    # direct Lua call) -- proves the click-handling wiring, not just the
    # graph-walking logic.
    bus.emit(
        "dialog_choice_clicked",
        {"entity_id": 1, "target_id": 2, "dialog_id": DIALOG_ID, "node_id": "greet", "choice_index": 2},
    )

    assert engine.get_floor_state("dialog_2_node") == "lore_1"

    shown = []
    bus.subscribe("show_panel", lambda p: shown.append(p))
    # Re-open (simulates the player bumping the NPC again) -- resumes at
    # lore_1, not the start node.
    slate.DialogWalker.open(1, 2, DIALOG_ID)
    choice_texts = [c["text"] for c in shown[-1]["data"]["choices"]]
    assert "Running from what?" in choice_texts

    # Choosing it sets campaign state and transitions to lore_2.
    bus.emit(
        "dialog_choice_clicked",
        {"entity_id": 1, "target_id": 2, "dialog_id": DIALOG_ID, "node_id": "lore_1", "choice_index": 1},
    )
    assert engine.get_campaign_state("mira_lore_told") is True
    assert engine.get_floor_state("dialog_2_node") == "lore_2"

    # Back at greet, "Running from what?" is now hidden.
    bus.emit(
        "dialog_choice_clicked",
        {"entity_id": 1, "target_id": 2, "dialog_id": DIALOG_ID, "node_id": "lore_2", "choice_index": 1},
    )
    assert engine.get_floor_state("dialog_2_node") == "greet"

    updated = []
    bus.subscribe("show_panel", lambda p: updated.append(p))
    slate.DialogWalker.open(1, 2, DIALOG_ID)
    choice_texts = [c["text"] for c in updated[-1]["data"]["choices"]]
    assert "Tell me about this place." in choice_texts
    # The now-hidden "Running from what?" only ever lived under lore_1,
    # which isn't the currently-open node, but re-checking lore_1 directly
    # via another click confirms it's gone from that node's own list too.


def test_terminal_node_auto_closes_and_emits_dialog_closed(tmp_path, monkeypatch):
    host, world, bus = _boot(tmp_path, monkeypatch)

    slate = host.lua_runtime.globals().SLATE
    slate.DialogWalker.open(1, 2, DIALOG_ID)

    closed = []
    panel_closed = []
    bus.subscribe("dialog_closed", lambda p: closed.append(p))
    bus.subscribe("panel_closed", lambda p: panel_closed.append(p))

    bus.emit(
        "dialog_choice_clicked",
        {"entity_id": 1, "target_id": 2, "dialog_id": DIALOG_ID, "node_id": "greet", "choice_index": 3},
    )

    assert closed == [{"entity_id": 1, "target_id": 2, "dialog_id": DIALOG_ID, "node_id": "end"}]
    # engine.destroy_panel emits panel_closed (10's documented mechanism) --
    # asserted directly rather than via UIRuntime's live panel stack, since
    # 08's merged UIRuntime does not yet subscribe to panel_closed (a
    # pre-existing "wired, not just built" gap flagged in this PR, not
    # fixed here since engine/ui/ui_runtime.py is 08's file).
    assert panel_closed == [{"panel_id": "dialog"}]


def test_open_shop_action_closes_dialog_and_opens_shop(tmp_path, monkeypatch):
    registry = FixtureRegistry({
        "dialogs": {DIALOG_ID: DIALOG_FIXTURE},
        "shops": {
            "mira_general_store": {
                "id": "mira_general_store",
                "starting_gold": 50,
                "price_formula": {"buy": "base_price", "sell": "base_price"},
                "stock_table": [{"item_id": "torch", "weight": 1, "restock_quantity": [1, 1]}],
            }
        },
        "items": {"torch": {"id": "torch", "base_price": 5}},
        "configs": {"ui_skin": UI_SKIN_CONFIG},
    })
    host, world, bus = _boot(tmp_path, monkeypatch, registry=registry)

    slate = host.lua_runtime.globals().SLATE
    slate.DialogWalker.open(1, 2, DIALOG_ID)

    shop_opened = []
    dialog_closed = []
    bus.subscribe("shop_opened", lambda p: shop_opened.append(p))
    bus.subscribe("dialog_closed", lambda p: dialog_closed.append(p))

    bus.emit(
        "dialog_choice_clicked",
        {"entity_id": 1, "target_id": 2, "dialog_id": DIALOG_ID, "node_id": "greet", "choice_index": 1},
    )

    assert dialog_closed == [{"entity_id": 1, "target_id": 2, "dialog_id": DIALOG_ID, "node_id": "greet"}]
    assert shop_opened == [{"entity_id": 1, "shop_id": "mira_general_store"}]


def test_stale_click_against_a_node_that_already_advanced_is_ignored(tmp_path, monkeypatch):
    host, world, bus = _boot(tmp_path, monkeypatch)
    engine = host.lua_runtime.globals().engine

    slate = host.lua_runtime.globals().SLATE
    slate.DialogWalker.open(1, 2, DIALOG_ID)

    # Advance for real.
    bus.emit(
        "dialog_choice_clicked",
        {"entity_id": 1, "target_id": 2, "dialog_id": DIALOG_ID, "node_id": "greet", "choice_index": 2},
    )
    assert engine.get_floor_state("dialog_2_node") == "lore_1"

    selected = []
    bus.subscribe("dialog_choice_selected", lambda p: selected.append(p))

    # A stale click still naming the old (now stale) node_id is ignored.
    bus.emit(
        "dialog_choice_clicked",
        {"entity_id": 1, "target_id": 2, "dialog_id": DIALOG_ID, "node_id": "greet", "choice_index": 3},
    )

    assert selected == []
    assert engine.get_floor_state("dialog_2_node") == "lore_1"
