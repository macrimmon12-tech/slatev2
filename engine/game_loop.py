"""The live, playable game loop.

``engine/main.py``'s own docstring is explicit about what it deliberately
left out: "a real pygame display/input/audio/UI loop... requires
resolution/asset-path/window decisions no doc specifies." This module is
that follow-up — it takes ``Application`` (already boots and cross-wires
every real Wave 1 gameplay system against one ``World``/``EventBus``) and
wires `07-input-renderer-audio.md`'s ``InputHandler``/``Renderer``/
``AudioSystem``, `08-ui-runtime.md`'s ``UIRuntime``/``TargetingOverlay``,
and the inventory/spell/save-load UI on top, so the game can actually be
played rather than just tested.

Nothing here is a new gameplay *system* — every rule (hit chance, damage,
XP, loot, spell resolution) still lives exactly where CONTRACTS.md says
it does. This module's whole job is the wiring CONTRACTS.md §7 exists to
make sure someone does. See the per-fix docstrings in
``engine/render/renderer.py``, ``engine/input/input_handler.py``,
``engine/ui/ui_runtime.py``, ``engine/audio/audio_system.py``, and
``engine/systems/campaign.py`` for the specific gaps each one closes;
this module's own additions:

1. **Player content ownership.** No component doc owns "the player" —
   :func:`_spawn_player` creates one with a starting kit (a weapon, a
   potion, one self-cast spell) that is this module's own reasonable
   default, not any content file's.
2. **Item pickup.** No component wired "walk onto an item -> it's yours."
   :meth:`GameSession._on_player_moved_pickup` does, classic-roguelike
   auto-pickup style.
3. **Inventory/Spellbook UI + the equip/use/cast decision.** New
   ``inventory``/``spellbook`` screens (``data/config/ui_skin.json``) are
   pure display; deciding what a click *does* (equip vs. unequip vs. use
   an item; cast-immediately vs. enter targeting for a spell) is real
   game logic that belongs somewhere, and nothing else claims it.
4. **Mouse-driven spell targeting.** ``TargetingOverlay`` already existed
   and already knows how to flip ``InputHandler``'s context via an
   optional constructor hook — nobody had ever passed it a real
   ``InputHandler`` before, or anything to confirm/cancel against. This
   module does both: it wires a real one in, and — since neither
   ``SpellSystem`` nor ``InputHandler`` itself subscribes to
   ``target_confirmed``/``cancel_targeting`` — it's the subscriber that
   turns those events into the actual ``SpellSystem.confirm_target``/
   ``cancel_cast`` calls.
5. **Save/Load.** ``FloorManager`` already calls ``serialize_world`` after
   every transition and exposes ``last_save_data``/``set_save_sink``, but
   nothing wrote that to disk or read it back. This module does both,
   against plain JSON files under ``saves/``.
6. **Pause menu / settings / camera pan.** None of these have a home in
   any component doc either — Escape/menu-button wiring, volume control,
   and manual camera panning (mouse wheel over the viewport) all live
   here for the same reason as everything else in this list: they're
   real functionality with no more specific owner.

Run with: ``python -m engine.game_loop`` (or ``run_game.bat`` on Windows).
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any

import pygame

from engine.audio.audio_system import AudioSystem
from engine.core import save as save_module
from engine.input.input_handler import InputHandler
from engine.main import Application
from engine.render.renderer import HUD_WIDTH, Renderer
from engine.systems import combat as combat_module
from engine.systems import worldgen as worldgen_module
from engine.systems.ai import PlayerTagComponent, PositionComponent
from engine.systems.inventory import InventoryComponent, ItemInstanceComponent, ItemPositionComponent
from engine.systems.spells import SpellCasterComponent
from engine.systems.stats import StatsComponent
from engine.ui.targeting_overlay import TargetingOverlay
from engine.ui.ui_runtime import UIRuntime

logger = logging.getLogger(__name__)

WINDOW_SIZE = (1280, 800)
DEFAULT_CAMPAIGN_ID = "the_sunken_keep"
MAX_LOG_MESSAGES = 40
SAVES_DIR = Path(__file__).resolve().parent.parent / "saves"
CAMERA_PAN_STEP = 3  # tiles per wheel notch

# This module's own default, not any content file's (see module
# docstring, point 1) -- deliberately unremarkable starting numbers for a
# depth-1 character, easy to rebalance later without touching any wiring
# here.
PLAYER_BASE_STATS: dict[str, float] = {
    "hp": 30.0,
    "max_hp": 30.0,
    "mp": 10.0,
    "max_mp": 10.0,
    "strength": 6.0,
    "dexterity": 6.0,
    "intelligence": 6.0,
    "damage_min": 1.0,
    "damage_max": 4.0,
    "vision_range": 8.0,
}
PLAYER_STARTER_ITEMS = ("long_sword", "health_potion")
PLAYER_STARTER_SPELLS = ("minor_heal",)


def _make_screen_rect_provider():
    """See ``engine/render/renderer.py``'s ``_draw_hud`` fix docstring for
    why this convention (``full_screen=True`` -> the HUD strip,
    default/``False`` -> the whole window) is the one that makes every
    existing ``ui_skin.json`` screen resolve correctly with zero JSON
    changes. Reads the *current* window size from pygame itself every
    call so it stays correct across :meth:`Renderer.resize`."""

    def provider(full_screen: bool) -> tuple[int, int, int, int]:
        surface = pygame.display.get_surface()
        width, height = surface.get_size() if surface is not None else WINDOW_SIZE
        if full_screen:
            return (width - HUD_WIDTH, 0, HUD_WIDTH, height)
        return (0, 0, width, height)

    return provider


def _find_player_spawn(tilemap: Any) -> tuple[int, int]:
    """A walkable tile to place the player on. Prefers ``stairs_up``
    (every floor but the first has one); otherwise any walkable tile that
    isn't ``stairs_down`` (the first floor's only landmark); otherwise any
    walkable tile at all; ``(0, 0)`` on a fully degenerate floor (should
    be unreachable per ``worldgen``'s own guarantees, but this module
    still never raises here — CONTRACTS.md §2 rule 7)."""
    if getattr(tilemap, "stairs_up", None) is not None:
        return tilemap.stairs_up
    stairs_down = getattr(tilemap, "stairs_down", None)
    for pos, tile in tilemap.tiles.items():
        if tile.walkable and pos != stairs_down:
            return pos
    if stairs_down is not None:
        return stairs_down
    return (0, 0)


class GameSession:
    """Owns the live window and per-frame loop. One instance per run."""

    def __init__(self, app: Application) -> None:
        self.app = app
        self.world = app.world
        self.event_bus = app.event_bus
        self.registry = app.registry

        self.ui_runtime = UIRuntime(
            self.event_bus, self.registry, screen_rect_provider=_make_screen_rect_provider()
        )
        self.renderer = Renderer(
            self.world,
            self.registry,
            self.event_bus,
            window_size=WINDOW_SIZE,
            get_tilemap=self._current_tilemap,
            ui_runtime=self.ui_runtime,
        )
        self.audio = AudioSystem(self.event_bus, self.registry)

        controls_config = self.registry.get("configs", "controls") if self.registry else None
        self.input_handler = InputHandler(
            self.event_bus,
            controls_config,
            context="ui",
            world=self.world,
            spatial_hash=None,  # set on the first floor_changed (see module docstring)
            is_passable=self._is_passable,
            get_current_target=lambda: self._pending_target,
        )
        self.targeting_overlay = TargetingOverlay(
            self.event_bus,
            screen_rect_provider=_make_screen_rect_provider(),
            input_handler=self.input_handler,
        )

        # A save-to-disk sink now that one exists (module docstring,
        # point 5) -- every floor transition's already-called
        # serialize_world() actually lands somewhere.
        self.app.campaign_system.floor_manager.set_save_sink(self._write_autosave)

        self.player_id: int | None = None
        self._current_floor_id: str | None = None
        self._current_spatial_hash: Any | None = None
        self.messages: list[dict[str, str]] = []
        self.state = "menu"  # "menu" | "playing" | "game_over"
        self._turn_pending = False
        self._running = True
        self._pending_target: dict[str, Any] | None = None

        self.event_bus.subscribe("new_game_selected", self._on_new_game)
        self.event_bus.subscribe("quit_selected", self._on_quit_selected)
        self.event_bus.subscribe("quit_to_main_menu_clicked", self._on_quit_to_main_menu_clicked)
        self.event_bus.subscribe("floor_changed", self._on_floor_changed)
        self.event_bus.subscribe("melee_attack_attempt", self._on_melee_attack_attempt)
        self.event_bus.subscribe("player_moved", self._on_player_took_turn)
        self.event_bus.subscribe("player_moved", self._on_player_moved_pickup)
        self.event_bus.subscribe("entity_interacted", self._on_player_took_turn)
        self.event_bus.subscribe("stair_use", self._on_player_took_turn)
        self.event_bus.subscribe("player_died", self._on_player_died)
        self.event_bus.subscribe("damage_dealt", self._on_damage_dealt)
        self.event_bus.subscribe("miss", self._on_miss)
        self.event_bus.subscribe("entity_died", self._on_entity_died)
        self.event_bus.subscribe("message", self._on_message)
        self.event_bus.subscribe("show_panel", self._on_show_panel_requested)
        self.event_bus.subscribe("inventory_item_clicked", self._on_inventory_item_clicked)
        self.event_bus.subscribe("spell_clicked", self._on_spell_clicked)
        self.event_bus.subscribe("target_confirmed", self._on_target_confirmed)
        self.event_bus.subscribe("cancel_targeting", self._on_cancel_targeting)
        self.event_bus.subscribe("save_game_clicked", self._on_save_game_clicked)
        self.event_bus.subscribe("save_slot_selected", self._on_save_slot_selected)
        self.event_bus.subscribe("volume_adjust_clicked", self._on_volume_adjust_clicked)

        self.audio.set_volume(0.7)
        self.event_bus.emit("show_panel", {"panel_id": "main_menu"})

    # -- tilemap / passability -------------------------------------------

    def _current_tilemap(self) -> Any | None:
        if self._current_floor_id is None:
            return None
        return worldgen_module.get_tilemap(self._current_floor_id)

    def _is_passable(self, pos: tuple[int, int]) -> bool:
        tilemap = self._current_tilemap()
        if tilemap is None:
            return False
        tile = tilemap.tiles.get(pos)
        return tile is not None and tile.walkable

    # -- player -------------------------------------------------------------

    def _spawn_player(self) -> None:
        if self.player_id is not None:
            return
        entity_id = self.world.create_entity()
        self.world.add_component(entity_id, PositionComponent(x=0, y=0))
        self.world.add_component(entity_id, PlayerTagComponent())
        self.world.add_component(entity_id, StatsComponent(base=dict(PLAYER_BASE_STATS), modifiers={}))
        self.world.add_component(entity_id, InventoryComponent())
        self.world.add_component(entity_id, SpellCasterComponent(known_spells=list(PLAYER_STARTER_SPELLS)))
        self.player_id = entity_id
        self._grant_starter_items()

    def _grant_starter_items(self) -> None:
        if self.player_id is None:
            return
        from engine.systems.loot import spawn_item_instance

        inventory = self.world.get_component(self.player_id, InventoryComponent)
        if inventory is None:
            return
        for item_id in PLAYER_STARTER_ITEMS:
            instance_id = spawn_item_instance(item_id, depth=1, position=(-1, -1), world=self.world, registry=self.registry)
            entity_id, _item = self._find_item_instance(instance_id)
            if entity_id is not None:
                self.world.remove_component(entity_id, ItemPositionComponent)
            inventory.item_instance_ids.append(instance_id)

    def _find_item_instance(self, instance_id: str) -> tuple[int | None, ItemInstanceComponent | None]:
        for entity_id, item in self.world.query(ItemInstanceComponent):
            if item.instance_id == instance_id:
                return entity_id, item
        return None, None

    # -- new game / quit --------------------------------------------------------

    def _on_new_game(self, payload: dict | None) -> None:
        if self.state == "playing":
            return
        payload = payload or {}
        campaign_id = payload.get("campaign_id") or DEFAULT_CAMPAIGN_ID

        self.app.campaign_system.load_campaign(campaign_id)
        self._spawn_player()

        campaign_data = self.registry.get("campaigns", campaign_id) if self.registry else None
        levels = (campaign_data or {}).get("levels", [])
        first_level = next((lvl for lvl in levels if not lvl.get("is_hub")), None)
        if first_level is None:
            logger.warning("New Game: campaign %r has no non-hub level to start on.", campaign_id)
            return

        self.ui_runtime.destroy_panel("main_menu")
        self.ui_runtime.destroy_panel("save_select")

        self.app.campaign_system.enter_level(first_level["id"])  # -> floor_changed, handled below

        self.messages = []
        self.event_bus.emit(
            "show_panel",
            {
                "panel_id": "hud",
                "data": {
                    "hp": PLAYER_BASE_STATS["hp"],
                    "max_hp": PLAYER_BASE_STATS["max_hp"],
                    "mp": PLAYER_BASE_STATS["mp"],
                    "max_mp": PLAYER_BASE_STATS["max_mp"],
                    "messages": self.messages,
                },
            },
        )
        self.input_handler.set_context("game")
        self.state = "playing"

    def _on_quit_selected(self, _payload: dict | None) -> None:
        self._running = False

    def _on_quit_to_main_menu_clicked(self, _payload: dict | None) -> None:
        """Pause menu's own "give up on this run" path — distinct from
        ``quit_selected``, which quits the whole application."""
        self._return_to_main_menu()

    def _return_to_main_menu(self) -> None:
        for panel_id in ("hud", "menu", "inventory", "spellbook", "settings", "save_select"):
            self.ui_runtime.destroy_panel(panel_id)
        self.player_id = None
        self._current_floor_id = None
        self.input_handler.set_context("ui")
        self.event_bus.emit("show_panel", {"panel_id": "main_menu"})
        self.state = "menu"

    # -- floor transitions --------------------------------------------------

    def _on_floor_changed(self, _payload: dict | None) -> None:
        self._current_floor_id = self.app.campaign_system.floor_manager.current_floor_id
        tilemap = self._current_tilemap()
        self.renderer.set_tilemap(tilemap)

        per_floor_hash = worldgen_module.get_spatial_hash(self._current_floor_id or "")
        self._current_spatial_hash = per_floor_hash
        self.input_handler.set_spatial_hash(per_floor_hash)

        if self.player_id is None or tilemap is None:
            return
        spawn = _find_player_spawn(tilemap)
        position = self.world.get_component(self.player_id, PositionComponent)
        if position is not None:
            position.x, position.y = spawn
        if per_floor_hash is not None:
            per_floor_hash.insert(self.player_id, spawn)
        self.event_bus.emit("player_moved", {"entity_id": self.player_id, "from": spawn, "to": spawn})

    # -- combat / turns -------------------------------------------------------

    def _on_melee_attack_attempt(self, payload: dict | None) -> None:
        if not payload:
            return
        combat_module.resolve_hit(payload["attacker_id"], payload["target_id"], self.world, self.event_bus)
        self._turn_pending = True

    def _on_player_took_turn(self, _payload: dict | None) -> None:
        if self.state == "playing":
            self._turn_pending = True

    def _on_player_moved_pickup(self, payload: dict | None) -> None:
        """Module docstring, point 2 -- classic auto-pickup-on-step.
        Zero component doc wires this; ambient floor loot
        (``04-inventory-items-loot.md``'s ``place_floor_loot``) otherwise
        just sits there forever with no way into the player's
        inventory."""
        if not payload or self.player_id is None or payload.get("entity_id") != self.player_id:
            return
        to_pos = tuple(payload.get("to", ()))
        if len(to_pos) != 2:
            return
        inventory = self.world.get_component(self.player_id, InventoryComponent)
        if inventory is None:
            return
        for entity_id, item_pos in self.world.query(ItemPositionComponent):
            if (item_pos.x, item_pos.y) != to_pos:
                continue
            item = self.world.get_component(entity_id, ItemInstanceComponent)
            if item is None:
                continue
            self.world.remove_component(entity_id, ItemPositionComponent)
            inventory.item_instance_ids.append(item.instance_id)
            item_def = (self.registry.get("items", item.base_id) or self.registry.get("legendaries", item.base_id) or {}) if self.registry else {}
            self._log(f"You pick up {item_def.get('display_name', item.base_id)}.")
            self.event_bus.emit("item_pickup", {"entity_id": self.player_id, "item_instance_id": item.instance_id})

    def _on_player_died(self, _payload: dict | None) -> None:
        self._log("You have died.")
        self._return_to_main_menu()

    def _on_damage_dealt(self, payload: dict | None) -> None:
        if not payload:
            return
        amount = payload.get("amount", 0)
        if payload.get("target_id") == self.player_id:
            self._log(f"You take {amount:.0f} damage.")
        else:
            self._log(f"You deal {amount:.0f} damage.")

    def _on_miss(self, payload: dict | None) -> None:
        if not payload:
            return
        if payload.get("attacker_id") == self.player_id:
            self._log("You miss.")
        elif payload.get("defender_id") == self.player_id:
            self._log("The attack misses you.")

    def _on_entity_died(self, payload: dict | None) -> None:
        if payload and payload.get("entity_id") != self.player_id:
            self._log("An enemy falls.")

    def _on_message(self, payload: dict | None) -> None:
        if payload and payload.get("text"):
            self._log(str(payload["text"]))

    def _log(self, text: str) -> None:
        self.messages.append({"text": text})
        del self.messages[:-MAX_LOG_MESSAGES]

    # -- inventory / spellbook ------------------------------------------------

    def _on_show_panel_requested(self, payload: dict | None) -> None:
        """Injects live data into a panel at the moment it's (re)shown.
        Runs after ``UIRuntime``'s own ``show_panel`` handler (subscribed
        first, in this class's ``__init__``, since ``self.ui_runtime`` is
        constructed before these subscriptions) so the panel already
        exists by the time this fires — ``update_panel`` is a no-op
        otherwise (CONTRACTS.md §2 rule 7), never an error either way."""
        if not payload:
            return
        panel_id = payload.get("panel_id")
        if panel_id == "inventory":
            self.ui_runtime.update_panel("inventory", {"entity_id": self.player_id, "items": self._inventory_rows()})
        elif panel_id == "spellbook":
            self.ui_runtime.update_panel("spellbook", {"entity_id": self.player_id, "spells": self._spell_rows()})
        elif panel_id == "save_select":
            self.ui_runtime.update_panel("save_select", {"save_slots": self._list_save_slots()})
        elif panel_id == "settings":
            self.ui_runtime.update_panel("settings", {"volume_pct": round(self.audio.get_volume() * 100)})

    def _inventory_rows(self) -> list[dict[str, str]]:
        if self.player_id is None or self.registry is None:
            return []
        inventory = self.world.get_component(self.player_id, InventoryComponent)
        if inventory is None:
            return []
        equipped_ids = {v for v in inventory.equipped.values() if v}
        rows: list[dict[str, str]] = []
        for instance_id in inventory.item_instance_ids:
            _entity_id, item = self._find_item_instance(instance_id)
            if item is None:
                continue
            item_def = self.registry.get("items", item.base_id) or self.registry.get("legendaries", item.base_id) or {}
            name = item_def.get("display_name", item.base_id)
            if instance_id in equipped_ids:
                subtitle = "Equipped"
            elif item_def.get("consumable"):
                subtitle = "Usable"
            else:
                subtitle = item.rarity.capitalize()
            rows.append({"instance_id": instance_id, "name": name, "subtitle": subtitle})
        return rows

    def _spell_rows(self) -> list[dict[str, Any]]:
        if self.player_id is None or self.registry is None:
            return []
        caster = self.world.get_component(self.player_id, SpellCasterComponent)
        if caster is None:
            return []
        rows: list[dict[str, Any]] = []
        for spell_id in caster.known_spells:
            spell_def = self.registry.get("spells", spell_id) or {}
            rows.append(
                {"spell_id": spell_id, "name": spell_def.get("display_name", spell_id), "mp_cost": spell_def.get("mp_cost", 0)}
            )
        return rows

    def _on_inventory_item_clicked(self, payload: dict | None) -> None:
        if not payload or self.registry is None:
            return
        entity_id = payload.get("entity_id")
        instance_id = payload.get("item_instance_id")
        if entity_id is None or not instance_id:
            return
        inventory = self.world.get_component(entity_id, InventoryComponent)
        _item_entity_id, item = self._find_item_instance(instance_id)
        if inventory is None or item is None:
            return
        item_def = self.registry.get("items", item.base_id) or self.registry.get("legendaries", item.base_id) or {}
        display_name = item_def.get("display_name", item.base_id)
        equipped_ids = {v for v in inventory.equipped.values() if v}

        if instance_id in equipped_ids:
            slot = next((s for s, v in inventory.equipped.items() if v == instance_id), None)
            if slot is not None and self.app.inventory_system.unequip(entity_id, slot):
                self._log(f"Unequipped {display_name}.")
        elif item_def.get("equippable"):
            slot = item_def.get("slot", "main_hand")
            if self.app.inventory_system.equip(entity_id, instance_id, slot):
                self._log(f"Equipped {display_name}.")
        elif item_def.get("consumable"):
            if self.app.inventory_system.use(entity_id, instance_id):
                self._log(f"Used {display_name}.")

        self.ui_runtime.update_panel("inventory", {"items": self._inventory_rows()})

    def _on_spell_clicked(self, payload: dict | None) -> None:
        if not payload:
            return
        entity_id = payload.get("entity_id")
        spell_id = payload.get("spell_id")
        if entity_id is None or not spell_id:
            return
        self.ui_runtime.destroy_panel("spellbook")
        self.app.spell_system.cast(entity_id, spell_id, None)
        self._turn_pending = True

    # -- mouse-driven spell targeting -----------------------------------------

    def _resolve_click_target(self, screen_pos: tuple[int, int]) -> dict[str, Any] | None:
        tile = self.renderer.screen_to_world(screen_pos)
        if tile is None:
            return None
        target: dict[str, Any] = {"position": list(tile)}
        if self._current_spatial_hash is not None:
            for candidate_id in self._current_spatial_hash.query_radius(tile, 0):
                if candidate_id != self.player_id and self.world.get_component(candidate_id, StatsComponent) is not None:
                    target["entity_id"] = candidate_id
                    break
        return target

    def _on_target_confirmed(self, payload: dict | None) -> None:
        if self.player_id is None:
            return
        target = (payload or {}).get("target")
        if target is not None:
            self.app.spell_system.confirm_target(self.player_id, target)
            self._turn_pending = True

    def _on_cancel_targeting(self, _payload: dict | None) -> None:
        if self.player_id is not None:
            self.app.spell_system.cancel_cast(self.player_id)

    # -- save / load ------------------------------------------------------------

    def _write_autosave(self, save_data: dict) -> None:
        self._write_save_file("autosave", save_data)

    def _write_save_file(self, slot_id: str, save_data: dict) -> None:
        SAVES_DIR.mkdir(parents=True, exist_ok=True)
        payload = dict(save_data)
        payload["_floor_id"] = self._current_floor_id
        payload["_campaign_id"] = self.app.campaign_system.campaign_id
        (SAVES_DIR / f"{slot_id}.json").write_text(json.dumps(payload, indent=2))

    def _list_save_slots(self) -> list[dict[str, str]]:
        if not SAVES_DIR.exists():
            return []
        slots = []
        for path in sorted(SAVES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            slots.append({"slot_id": path.stem, "name": path.stem})
        return slots

    def _on_save_game_clicked(self, _payload: dict | None) -> None:
        if self.player_id is None:
            return
        slot_id = "save_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        floor_manager = self.app.campaign_system.floor_manager
        save_data = save_module.serialize_world(self.world, spawned_uniques=floor_manager.spawned_uniques)
        self._write_save_file(slot_id, save_data)
        self._log("Game saved.")

    def _on_save_slot_selected(self, payload: dict | None) -> None:
        slot = (payload or {}).get("slot")
        if not slot:
            return
        path = SAVES_DIR / f"{slot}.json"
        if not path.exists():
            self._log(f"Save file {slot!r} not found.")
            return
        data = json.loads(path.read_text())

        # A loaded save replaces the live world outright -- destroy every
        # current entity first (world.entities() is a live view; snapshot
        # it before mutating).
        for entity_id in list(self.world.entities()):
            self.world.destroy_entity(entity_id)

        floor_manager = self.app.campaign_system.floor_manager
        save_module.deserialize_world(data, self.world, spawned_uniques=floor_manager.spawned_uniques)

        player_rows = self.world.query(PlayerTagComponent)
        self.player_id = player_rows[0][0] if player_rows else None

        self._current_floor_id = data.get("_floor_id")
        campaign_id = data.get("_campaign_id")
        if campaign_id:
            self.app.campaign_system.load_campaign(campaign_id)
        floor_manager.restore_current_floor_id(self._current_floor_id)

        tilemap = self._current_tilemap()
        self.renderer.set_tilemap(tilemap)
        per_floor_hash = worldgen_module.get_spatial_hash(self._current_floor_id or "")
        self._current_spatial_hash = per_floor_hash
        self.input_handler.set_spatial_hash(per_floor_hash)

        for panel_id in ("main_menu", "save_select", "menu"):
            self.ui_runtime.destroy_panel(panel_id)
        self.messages = []
        self.event_bus.emit(
            "show_panel",
            {"panel_id": "hud", "data": {"hp": 0, "max_hp": 1, "mp": 0, "max_mp": 1, "messages": self.messages}},
        )
        self.input_handler.set_context("game")
        self.state = "playing"
        self._log("Game loaded.")

    # -- settings ---------------------------------------------------------------

    def _on_volume_adjust_clicked(self, payload: dict | None) -> None:
        delta = (payload or {}).get("delta", 0)
        self.audio.set_volume(self.audio.get_volume() + delta / 100.0)
        self.ui_runtime.update_panel("settings", {"volume_pct": round(self.audio.get_volume() * 100)})

    # -- per-frame HUD data refresh -------------------------------------------

    def _refresh_hud(self) -> None:
        if self.state != "playing" or self.player_id is None:
            return
        stats = self.app.stats_system
        self.ui_runtime.update_panel(
            "hud",
            {
                "hp": stats.get_stat(self.player_id, "hp", self.world),
                "max_hp": stats.get_stat(self.player_id, "max_hp", self.world),
                "mp": stats.get_stat(self.player_id, "mp", self.world),
                "max_mp": stats.get_stat(self.player_id, "max_mp", self.world),
                "messages": self.messages,
            },
        )

    # -- the loop itself -------------------------------------------------------

    def run(self, max_frames: int | None = None) -> None:
        """Run until the window closes / Quit is selected. ``max_frames``
        (used by tests and by nothing else) makes this terminate on its
        own for a headless smoke run instead of requiring a real quit
        signal."""
        clock = pygame.time.Clock()
        frames = 0
        while self._running:
            self._pump_events()
            if self._turn_pending:
                combat_module.process_monster_turns(self.world, self.event_bus)
                self._turn_pending = False
            self._refresh_hud()
            # Renderer.draw_frame() already calls ui_runtime.draw(...) as
            # its last step (that fix's docstring) -- panel drawing is not
            # this loop's job to duplicate. TargetingOverlay draws
            # separately since it's explicitly not a panel (its own
            # module docstring) -- drawn after so the cursor is visible
            # over the map even though it technically also ends up under
            # any modal panel drawn by the ui_runtime.draw() call inside
            # draw_frame(); an acceptable ordering trade-off (the overlay
            # is only ever active mid-gameplay, when no modal panel is up).
            self.renderer.draw_frame()
            self.targeting_overlay.draw(self.renderer.get_surface())
            pygame.display.flip()
            clock.tick(60)
            frames += 1
            if max_frames is not None and frames >= max_frames:
                self._running = False

    def _pump_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self._running = False
                continue
            if event.type == pygame.VIDEORESIZE:
                self.renderer.resize((event.w, event.h))
                continue
            if event.type == pygame.MOUSEMOTION and self.targeting_overlay.is_active():
                tile = self.renderer.screen_to_world(event.pos)
                self.targeting_overlay.set_cursor_tile(tile)
                continue
            if event.type == pygame.MOUSEWHEEL:
                mouse_pos = pygame.mouse.get_pos()
                if self.ui_runtime.handle_mouse_scroll(mouse_pos, -event.y):
                    continue
                if self.state == "playing" and self.renderer.get_viewport_rect().collidepoint(mouse_pos):
                    self.renderer.pan_camera(event.x * CAMERA_PAN_STEP, -event.y * CAMERA_PAN_STEP)
                continue
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if self.ui_runtime.handle_mouse_click(event.pos):
                    continue
                if self.targeting_overlay.is_active():
                    target = self._resolve_click_target(event.pos)
                    if target is not None:
                        self.event_bus.emit("target_confirmed", {"target": target})
                    continue
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3 and self.targeting_overlay.is_active():
                self.event_bus.emit("cancel_targeting", {"entity_id": self.player_id})
                continue
            if event.type == pygame.KEYDOWN:
                ui_action = self.input_handler.resolve_action(event)
                if ui_action and self.ui_runtime.handle_ui_input(ui_action):
                    continue
                self.input_handler.handle_pygame_event(event)


def run_game(data_root: Path | None = None, mods_dir: Path | None = None) -> None:
    """Boot ``Application`` (real ``data_root``/``mods_dir`` unless
    overridden — its own defaults already point at this repo's ``data/``
    and ``mods/``) and run a live ``GameSession`` until the window closes."""
    logging.basicConfig(level=logging.INFO)
    kwargs: dict[str, Path] = {}
    if data_root is not None:
        kwargs["data_root"] = data_root
    if mods_dir is not None:
        kwargs["mods_dir"] = mods_dir
    app = Application(**kwargs)
    app.boot()

    session = GameSession(app)
    try:
        session.run()
    finally:
        pygame.quit()


if __name__ == "__main__":
    run_game()
