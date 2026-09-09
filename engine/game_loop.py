"""The live, playable game loop.

``engine/main.py``'s own docstring is explicit about what it deliberately
left out: "a real pygame display/input/audio/UI loop... requires
resolution/asset-path/window decisions no doc specifies." This module is
that follow-up — it takes ``Application`` (already boots and cross-wires
every real Wave 1 gameplay system against one ``World``/``EventBus``) and
wires `07-input-renderer-audio.md`'s ``InputHandler``/``Renderer``/
``AudioSystem`` and `08-ui-runtime.md`'s ``UIRuntime`` into an actual
frame loop with a real window, so the game can be played rather than just
tested.

Nothing here is a new gameplay *system* — every rule (hit chance, damage,
XP, loot) still lives exactly where CONTRACTS.md says it does. This
module's whole job is the wiring CONTRACTS.md §7 exists to make sure
someone does: turning "every piece works in isolation, per its own
component doc's tests" into "the pieces work together, in one running
process, driven by real input." Several genuine gaps below were found by
actually trying to run the pieces together for the first time (see the
docstrings on the specific fixes in ``engine/render/renderer.py`` and
``engine/input/input_handler.py`` alongside this file) — flagged here
too, in one place, since this is the first module that exercises all of
them at once:

1. **``Renderer``/``UIRuntime`` were never paired with real objects
   before.** ``Renderer._draw_hud`` called ``ui_runtime.draw(surface,
   hud_rect)`` — a signature ``UIRuntime.draw`` never actually had. Fixed
   in ``engine/render/renderer.py``; the real contract is
   ``UIRuntime``'s own ``screen_rect_provider`` constructor argument,
   which this module supplies (see :func:`_make_screen_rect_provider`).
2. **``data/config/ui_config.json`` existed but drove nothing.**
   ``UIRuntime``'s colors were a hardcoded Python dict. Fixed in
   ``engine/ui/ui_runtime.py`` (now reads the registry's ``ui_config``
   colors, falling back to the same hardcoded defaults if absent).
3. **No mouse support anywhere.** Neither component doc specifies one.
   Added ``UIRuntime.handle_mouse_click``/hover tracking (see that
   module) — this file is the first caller.
4. **Bump-to-attack didn't exist.** ``InputHandler``'s movement path only
   ever checked the interactable-bump case; walking into a monster just
   silently failed the passability check. Added a parallel
   ``melee_attack_attempt`` request event in ``engine/input/input_handler.py``
   (InputHandler still never decides whether the hit lands — that stays
   ``combat.resolve_hit``'s call, made from :meth:`GameSession._on_melee_attack_attempt`
   below).
5. **``worldgen`` keeps a floor-scoped ``SpatialHash`` per floor**
   (``register_spatial_hash``/``get_spatial_hash``), never the single
   instance ``Application.spatial_hash`` holds. ``InputHandler`` needs the
   *current* floor's, so :meth:`GameSession._on_floor_changed` swaps it in
   via the new ``InputHandler.set_spatial_hash``.
6. **Player creation, and picking a spawn tile, belongs to nobody's
   doc.** No component owns "the player" as content — every doc assumes
   one already exists. :meth:`GameSession._spawn_player` creates a bare
   ``PositionComponent``/``PlayerTagComponent``/``StatsComponent`` entity
   (base stats are this module's own reasonable, documented default, not
   any content file's) and :func:`_find_player_spawn` picks a walkable
   tile.

Run with: ``python -m engine.game_loop`` (or via ``run_game.py`` at the
repo root, which also handles the venv-and-import-path bookkeeping a
plain double-clicked script needs on Windows).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pygame

from engine.audio.audio_system import AudioSystem
from engine.input.input_handler import InputHandler
from engine.main import Application
from engine.render.renderer import HUD_WIDTH, Renderer
from engine.systems import combat as combat_module
from engine.systems import worldgen as worldgen_module
from engine.systems.ai import PlayerTagComponent, PositionComponent
from engine.systems.stats import StatsComponent
from engine.ui.ui_runtime import UIRuntime

logger = logging.getLogger(__name__)

WINDOW_SIZE = (1280, 800)
DEFAULT_CAMPAIGN_ID = "the_sunken_keep"
MAX_LOG_MESSAGES = 40

# This module's own default, not any content file's (see module
# docstring, gap 6) -- deliberately unremarkable starting numbers for a
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


def _make_screen_rect_provider():
    """See module docstring, gap 1. The convention that makes every
    existing ``ui_skin.json`` screen (none of which set ``full_screen``,
    all of which need the *whole* window to center against) and the new
    ``hud`` screen (which does set ``full_screen: true``, per
    ``13``'s Sprite Manager precedent of using explicit fields rather
    than guessing) both resolve correctly, with zero JSON changes:
    ``full_screen=True`` -> the HUD strip, default/``False`` -> the whole
    window. Reads the *current* window size from pygame itself every call
    (rather than closing over a fixed size) so it stays correct across
    :meth:`Renderer.resize`."""

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
            spatial_hash=None,  # set on the first floor_changed (module docstring, gap 5)
            is_passable=self._is_passable,
        )

        self.player_id: int | None = None
        self._current_floor_id: str | None = None
        self.messages: list[dict[str, str]] = []
        self.state = "menu"  # "menu" | "playing" | "game_over"
        self._turn_pending = False
        self._running = True

        self.event_bus.subscribe("new_game_selected", self._on_new_game)
        self.event_bus.subscribe("quit_selected", self._on_quit_selected)
        self.event_bus.subscribe("floor_changed", self._on_floor_changed)
        self.event_bus.subscribe("melee_attack_attempt", self._on_melee_attack_attempt)
        self.event_bus.subscribe("player_moved", self._on_player_took_turn)
        self.event_bus.subscribe("entity_interacted", self._on_player_took_turn)
        self.event_bus.subscribe("stair_use", self._on_player_took_turn)
        self.event_bus.subscribe("player_died", self._on_player_died)
        self.event_bus.subscribe("damage_dealt", self._on_damage_dealt)
        self.event_bus.subscribe("miss", self._on_miss)
        self.event_bus.subscribe("entity_died", self._on_entity_died)
        self.event_bus.subscribe("message", self._on_message)

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
        self.player_id = entity_id

    # -- event handlers -------------------------------------------------------

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

    def _on_floor_changed(self, _payload: dict | None) -> None:
        self._current_floor_id = self.app.campaign_system.floor_manager.current_floor_id
        tilemap = self._current_tilemap()
        self.renderer.set_tilemap(tilemap)

        # Module docstring, gap 5.
        per_floor_hash = worldgen_module.get_spatial_hash(self._current_floor_id or "")
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

    def _on_melee_attack_attempt(self, payload: dict | None) -> None:
        if not payload:
            return
        combat_module.resolve_hit(payload["attacker_id"], payload["target_id"], self.world, self.event_bus)
        self._turn_pending = True

    def _on_player_took_turn(self, _payload: dict | None) -> None:
        if self.state == "playing":
            self._turn_pending = True

    def _on_player_died(self, _payload: dict | None) -> None:
        self._log("You have died.")
        self.state = "game_over"
        self.ui_runtime.destroy_panel("hud")
        self.player_id = None
        self._current_floor_id = None
        self.input_handler.set_context("ui")
        self.event_bus.emit("show_panel", {"panel_id": "main_menu"})
        self.state = "menu"

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
            # its last step (module docstring, gap 1's fix) -- panel
            # drawing is not this loop's job to duplicate.
            self.renderer.draw_frame()
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
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if self.ui_runtime.handle_mouse_click(event.pos):
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
