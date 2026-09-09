"""Integration coverage for ``engine/game_loop.py`` — the real, playable
frame loop (see that module's docstring for the wiring it does and the
gaps it fixes along the way). Every other integration test in this repo
proves one component's own pipeline works in isolation; this one proves
the actual thing a person runs (main menu -> New Game -> a live floor
with a real player who can move, fight, and see it reflected in the HUD)
works end to end, against real objects, headless (``SDL_VIDEODRIVER=dummy``
in CI) rather than mocked.
"""

from __future__ import annotations

import pygame
import pytest

from engine.game_loop import GameSession
from engine.main import Application
from engine.systems import worldgen as worldgen_module
from engine.systems.ai import PositionComponent
from engine.systems.stats import StatsComponent


@pytest.fixture
def session():
    app = Application()
    app.boot()
    s = GameSession(app)
    yield s
    pygame.quit()


def test_boots_to_main_menu(session):
    assert session.state == "menu"
    assert "main_menu" in session.ui_runtime._stack


def test_new_game_loads_floor_and_spawns_player(session):
    session.event_bus.emit("new_game_selected", {"campaign_id": None})

    assert session.state == "playing"
    assert session.player_id is not None
    assert session._current_floor_id is not None
    assert "main_menu" not in session.ui_runtime._stack
    assert "hud" in session.ui_runtime._stack

    tilemap = worldgen_module.get_tilemap(session._current_floor_id)
    assert tilemap is not None
    position = session.world.get_component(session.player_id, PositionComponent)
    tile = tilemap.tiles.get((position.x, position.y))
    assert tile is not None and tile.walkable, "player must spawn on a walkable tile"

    # The per-floor SpatialHash (worldgen's, not Application's fixed one --
    # see game_loop's module docstring gap 5) must actually be the one
    # InputHandler queries, and the player must be discoverable in it.
    per_floor_hash = worldgen_module.get_spatial_hash(session._current_floor_id)
    assert session.input_handler._spatial_hash is per_floor_hash
    assert per_floor_hash.position_of(session.player_id) == (position.x, position.y)


def test_bump_attack_resolves_real_combat(session):
    session.event_bus.emit("new_game_selected", {"campaign_id": None})

    monster_id = next(
        eid for eid, _stats in session.world.query(StatsComponent) if eid != session.player_id
    )
    monster_pos = session.world.get_component(monster_id, PositionComponent)
    monster_hp_before = session.world.get_component(monster_id, StatsComponent).base["hp"]

    player_pos = session.world.get_component(session.player_id, PositionComponent)
    player_pos.x, player_pos.y = monster_pos.x - 1, monster_pos.y
    spatial_hash = session.input_handler._spatial_hash
    if spatial_hash.position_of(session.player_id) is None:
        spatial_hash.insert(session.player_id, (player_pos.x, player_pos.y))
    else:
        spatial_hash.move(
            session.player_id, spatial_hash.position_of(session.player_id), (player_pos.x, player_pos.y)
        )

    move_east = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RIGHT, mod=0, unicode="")
    session.input_handler.handle_pygame_event(move_east)

    assert session._turn_pending is True, "a bump attack must count as a turn"
    assert (player_pos.x, player_pos.y) == (monster_pos.x - 1, monster_pos.y), (
        "attacking must not also move the player onto the monster's tile"
    )

    monster_hp_after = session.world.get_component(monster_id, StatsComponent).base["hp"]
    # Either a hit landed (HP dropped) or missed (unchanged) -- both are
    # legitimate outcomes of a real hit-chance roll; what must be true
    # either way is that a real combat.resolve_hit call happened (not a
    # silent no-op) and something landed in the message log about it.
    assert monster_hp_after <= monster_hp_before
    assert session.messages, "combat must produce a message-log entry"


def test_player_death_returns_to_menu(session):
    session.event_bus.emit("new_game_selected", {"campaign_id": None})
    assert session.state == "playing"

    stats = session.world.get_component(session.player_id, StatsComponent)
    stats.base["hp"] = 0

    session.event_bus.emit("player_died", {"entity_id": session.player_id})

    assert session.state == "menu"
    assert session.player_id is None
    assert "hud" not in session.ui_runtime._stack
    assert "main_menu" in session.ui_runtime._stack
    assert any("died" in m["text"] for m in session.messages)


def test_quit_selected_stops_the_loop(session):
    assert session._running is True
    session.event_bus.emit("quit_selected", None)
    assert session._running is False


def test_run_terminates_on_max_frames_without_crashing(session):
    session.event_bus.emit("new_game_selected", {"campaign_id": None})
    session.run(max_frames=3)
    assert session._running is False


def test_mouse_click_on_new_game_button_starts_the_game(session):
    # Exercises UIRuntime.handle_mouse_click end to end against the real
    # main_menu tree from data/config/ui_skin.json, not a synthetic tree --
    # this is the actual path a player clicking the button takes.
    # Rather than guess exact pixel coordinates (fragile across any layout
    # tweak), resolve the button's real rect the same way the runtime does.
    state = session.ui_runtime._panels["main_menu"]
    hits = list(
        session.ui_runtime._iter_interactive_with_rect(
            state.root, state.data, session.ui_runtime._screen_rect_provider(False)
        )
    )
    new_game_hit = next(w for w, _ctx, _rect in hits if w.spec.get("id") == "btn_new_game")
    _widget, _ctx, btn_rect = next(h for h in hits if h[0] is new_game_hit)
    bx, by, bw, bh = btn_rect
    center = (bx + bw // 2, by + bh // 2)

    claimed = session.ui_runtime.handle_mouse_click(center)
    assert claimed is True
    assert session.state == "playing"
