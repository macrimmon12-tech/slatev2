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

from engine.audio import audio_system as audio_system_module
from engine.game_loop import SAVES_DIR, GameSession
from engine.main import Application
from engine.systems import campaign as campaign_module
from engine.systems import worldgen as worldgen_module
from engine.systems.ai import PositionComponent
from engine.systems.inventory import InventoryComponent
from engine.systems.spells import SpellCasterComponent
from engine.systems.stats import StatsComponent


@pytest.fixture(autouse=True)
def _clean_module_state():
    """Every ``Application.boot()`` call registers this process into
    several module-level singletons (``worldgen``/``campaign``'s own
    "set the process-wide registry once at boot" convention — see
    ``engine/main.py``'s docstring). ``worldgen``'s per-floor
    ``SpatialHash``/``TileMap`` registries in particular are keyed by
    *floor id string* ("keep_l1", ...), not by ``World`` instance — so
    two different tests generating the same campaign's first floor would
    otherwise silently share (and corrupt) each other's spatial data.
    This is the same ``reset_module_state()``/``set_data_registry(None)``
    pattern ``tests/unit/test_campaign.py`` already established for
    exactly this reason."""
    worldgen_module.reset_module_state()
    campaign_module.set_data_registry(None)
    audio_system_module.reset_module_state()
    yield
    worldgen_module.reset_module_state()
    campaign_module.set_data_registry(None)
    audio_system_module.reset_module_state()


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


def _new_game(session):
    session.event_bus.emit("new_game_selected", {"campaign_id": None})


class _FakePressedKeys:
    """Stand-in for ``pygame.key.get_pressed()``'s ``ScancodeWrapper`` --
    this suite runs headless (``SDL_VIDEODRIVER=dummy``), which never
    populates real OS keyboard state, so tests that need a key "held"
    for ``GameSession._poll_camera_pan_keys`` monkeypatch
    ``pygame.key.get_pressed`` with one of these instead."""

    def __init__(self, held_key_codes: set[int]):
        self._held = held_key_codes

    def __getitem__(self, key_code: int) -> bool:
        return key_code in self._held


def _hold_keys(monkeypatch, *key_names: str) -> None:
    codes = {pygame.key.key_code(name) for name in key_names}
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: _FakePressedKeys(codes))


class TestInventoryAndSpellbook:
    def test_starter_kit_is_granted_and_listed(self, session):
        _new_game(session)
        rows = session._inventory_rows()
        names = {row["name"] for row in rows}
        assert "Long Sword" in names
        assert "Health Potion" in names

        spell_rows = session._spell_rows()
        assert any(row["spell_id"] == "minor_heal" for row in spell_rows)

    def test_clicking_an_equippable_item_equips_it(self, session):
        _new_game(session)
        sword = next(row for row in session._inventory_rows() if row["name"] == "Long Sword")
        session.event_bus.emit(
            "inventory_item_clicked", {"entity_id": session.player_id, "item_instance_id": sword["instance_id"]}
        )
        inventory = session.world.get_component(session.player_id, InventoryComponent)
        assert sword["instance_id"] in inventory.equipped.values()
        assert "Equipped" in next(r for r in session._inventory_rows() if r["instance_id"] == sword["instance_id"])["subtitle"]

        # clicking it again unequips (same click handles both directions)
        session.event_bus.emit(
            "inventory_item_clicked", {"entity_id": session.player_id, "item_instance_id": sword["instance_id"]}
        )
        inventory = session.world.get_component(session.player_id, InventoryComponent)
        assert sword["instance_id"] not in inventory.equipped.values()

    def test_clicking_a_consumable_uses_it_and_removes_it(self, session):
        _new_game(session)
        stats = session.world.get_component(session.player_id, StatsComponent)
        stats.base["hp"] = 5.0
        potion = next(row for row in session._inventory_rows() if row["name"] == "Health Potion")
        session.event_bus.emit(
            "inventory_item_clicked", {"entity_id": session.player_id, "item_instance_id": potion["instance_id"]}
        )
        assert stats.base["hp"] > 5.0
        inventory = session.world.get_component(session.player_id, InventoryComponent)
        assert potion["instance_id"] not in inventory.item_instance_ids

    def test_casting_a_self_spell_resolves_immediately(self, session):
        _new_game(session)
        stats = session.world.get_component(session.player_id, StatsComponent)
        stats.base["hp"] = 5.0
        mp_before = stats.base["mp"]
        session.event_bus.emit("spell_clicked", {"entity_id": session.player_id, "spell_id": "minor_heal"})
        assert stats.base["hp"] > 5.0
        assert stats.base["mp"] < mp_before
        assert session.targeting_overlay.is_active() is False


class TestMouseDrivenTargeting:
    def test_casting_a_targeted_spell_enters_targeting_mode(self, session):
        _new_game(session)
        caster = session.world.get_component(session.player_id, SpellCasterComponent)
        caster.known_spells.append("firebolt")
        session.event_bus.emit("spell_clicked", {"entity_id": session.player_id, "spell_id": "firebolt"})
        assert session.targeting_overlay.is_active() is True
        assert session.input_handler._context == "targeting"

    def test_confirming_on_a_monster_resolves_the_spell(self, session):
        _new_game(session)
        caster = session.world.get_component(session.player_id, SpellCasterComponent)
        caster.known_spells.append("firebolt")
        monster_id = next(eid for eid, _s in session.world.query(StatsComponent) if eid != session.player_id)
        monster_pos = session.world.get_component(monster_id, PositionComponent)
        monster_hp_before = session.world.get_component(monster_id, StatsComponent).base["hp"]

        # Recenter the camera on the monster so it's actually on screen --
        # a real player can only click what's rendered; the monster's
        # world position is otherwise wherever floor generation happened
        # to put it, which is not guaranteed to be near the player.
        session.event_bus.emit(
            "player_moved",
            {"entity_id": session.player_id, "from": (monster_pos.x, monster_pos.y), "to": (monster_pos.x, monster_pos.y)},
        )

        session.event_bus.emit("spell_clicked", {"entity_id": session.player_id, "spell_id": "firebolt"})
        screen_pos = session.renderer.world_to_screen((monster_pos.x, monster_pos.y))
        target = session._resolve_click_target(screen_pos)
        assert target is not None and target.get("entity_id") == monster_id

        session.event_bus.emit("target_confirmed", {"target": target})
        assert session.targeting_overlay.is_active() is False
        assert session.input_handler._context == "game"
        monster_stats_after = session.world.get_component(monster_id, StatsComponent)
        if monster_stats_after is not None:
            # Hit (HP dropped) or missed (unchanged) -- either is a real
            # resolved outcome of a real hit-chance roll.
            assert monster_stats_after.base["hp"] <= monster_hp_before
        # else: firebolt's damage killed it outright and combat destroyed
        # the entity (engine.systems.combat.handle_potential_death) --
        # also a genuinely resolved outcome, not a missing one.

    def test_confirming_on_empty_ground_does_not_hit_the_caster(self, session):
        _new_game(session)
        caster = session.world.get_component(session.player_id, SpellCasterComponent)
        caster.known_spells.append("firebolt")
        player_stats = session.world.get_component(session.player_id, StatsComponent)
        hp_before = player_stats.base["hp"]

        session.event_bus.emit("spell_clicked", {"entity_id": session.player_id, "spell_id": "firebolt"})
        empty_pos = session.world.get_component(session.player_id, PositionComponent)
        # a tile several steps away almost certainly has no monster on it
        screen_pos = session.renderer.world_to_screen((empty_pos.x + 20, empty_pos.y + 20))
        target = session._resolve_click_target(screen_pos)
        session.event_bus.emit("target_confirmed", {"target": target})

        assert player_stats.base["hp"] == hp_before, "a whiffed single_enemy target must never hit the caster"

    def test_cancel_targeting_tears_down_cleanly(self, session):
        _new_game(session)
        caster = session.world.get_component(session.player_id, SpellCasterComponent)
        caster.known_spells.append("firebolt")
        session.event_bus.emit("spell_clicked", {"entity_id": session.player_id, "spell_id": "firebolt"})
        assert session.targeting_overlay.is_active() is True

        session.event_bus.emit("cancel_targeting", {"entity_id": session.player_id})
        assert session.targeting_overlay.is_active() is False
        assert session.input_handler._context == "game"


class TestSaveLoad:
    def test_save_then_load_restores_real_state(self, session, tmp_path, monkeypatch):
        import engine.game_loop as game_loop_module

        monkeypatch.setattr(game_loop_module, "SAVES_DIR", tmp_path)
        _new_game(session)

        stats = session.world.get_component(session.player_id, StatsComponent)
        stats.base["hp"] = 17.0
        position = session.world.get_component(session.player_id, PositionComponent)
        saved_pos = (position.x, position.y)

        session.event_bus.emit("save_game_clicked", {})
        save_files = list(tmp_path.glob("save_*.json"))
        assert len(save_files) == 1

        # Mutate state after saving, then load back over it.
        stats.base["hp"] = 1.0
        position.x, position.y = 0, 0

        session.event_bus.emit("show_panel", {"panel_id": "save_select"})
        slots = session.ui_runtime._panels["save_select"].data["save_slots"]
        slot_id = next(s["slot_id"] for s in slots if s["slot_id"].startswith("save_"))
        session.event_bus.emit("save_slot_selected", {"slot": slot_id})

        assert session.state == "playing"
        restored_stats = session.world.get_component(session.player_id, StatsComponent)
        restored_pos = session.world.get_component(session.player_id, PositionComponent)
        assert restored_stats.base["hp"] == 17.0
        assert (restored_pos.x, restored_pos.y) == saved_pos

    def test_floor_transition_autosave_writes_a_file(self, session, tmp_path, monkeypatch):
        import engine.game_loop as game_loop_module

        monkeypatch.setattr(game_loop_module, "SAVES_DIR", tmp_path)
        _new_game(session)  # entering the first floor already transitions once
        assert (tmp_path / "autosave.json").exists()


class TestPauseMenuAndSettings:
    def test_escape_opens_pause_menu(self, session):
        _new_game(session)
        session.event_bus.emit("show_panel", {"panel_id": "menu"})
        assert "menu" in session.ui_runtime._stack

    def test_resume_closes_the_pause_menu(self, session):
        _new_game(session)
        session.event_bus.emit("show_panel", {"panel_id": "menu"})
        session.event_bus.emit("panel_closed", {"panel_id": "menu"})
        assert "menu" not in session.ui_runtime._stack

    def test_quit_to_main_menu_returns_to_menu_state(self, session):
        _new_game(session)
        assert session.state == "playing"
        session.event_bus.emit("quit_to_main_menu_clicked", {})
        assert session.state == "menu"
        assert session.player_id is None
        assert "main_menu" in session.ui_runtime._stack

    def test_volume_adjust_clamps_and_updates_settings_panel(self, session):
        session.audio.set_volume(0.05)
        session.event_bus.emit("show_panel", {"panel_id": "settings"})
        session.event_bus.emit("volume_adjust_clicked", {"delta": -10})
        assert session.audio.get_volume() == 0.0  # clamped, never negative
        assert session.ui_runtime._panels["settings"].data["volume_pct"] == 0


class TestScrollingAndCamera:
    def test_message_log_scrolls_and_clips_once_it_overflows(self, session):
        _new_game(session)
        for i in range(60):
            session._log(f"message {i}")
        session._refresh_hud()
        session.renderer.draw_frame()  # drives _draw_list_items, which owns scroll state

        log_spec = _find_widget(session.ui_runtime._panels["hud"].root.spec, "hud_message_log")
        offset = session.ui_runtime._scroll_offsets[id(log_spec)]
        assert offset > 0, "a log that overflows its own height must auto-scroll to the newest entry"

        consumed = session.ui_runtime.handle_mouse_scroll((1150, 300), -3)
        assert consumed is True
        assert session.ui_runtime._scroll_offsets[id(log_spec)] < offset, "scrolling up must move away from the bottom"

    def test_camera_pan_moves_the_origin_without_touching_player_position(self, session):
        _new_game(session)
        position = session.world.get_component(session.player_id, PositionComponent)
        before_pos = (position.x, position.y)
        origin_before = session.renderer._camera_origin

        session.renderer.pan_camera(4, -2)

        assert session.renderer._camera_origin == (origin_before[0] + 4, origin_before[1] - 2)
        assert (position.x, position.y) == before_pos

    def test_player_move_recenters_camera_after_a_pan(self, session):
        _new_game(session)
        session.renderer.pan_camera(30, 30)
        panned_origin = session.renderer._camera_origin

        position = session.world.get_component(session.player_id, PositionComponent)
        session.event_bus.emit(
            "player_moved", {"entity_id": session.player_id, "from": (position.x, position.y), "to": (position.x, position.y)}
        )
        assert session.renderer._camera_origin != panned_origin

    def test_holding_a_pan_key_pans_the_camera_every_poll(self, session, monkeypatch):
        _new_game(session)
        origin_before = session.renderer._camera_origin
        _hold_keys(monkeypatch, "PageUp")  # pan_camera_north

        session._poll_camera_pan_keys()
        assert session.renderer._camera_origin == (origin_before[0], origin_before[1] - 1)

        session._poll_camera_pan_keys()  # still held next frame -> keeps panning
        assert session.renderer._camera_origin == (origin_before[0], origin_before[1] - 2)

    def test_all_four_pan_keys_move_the_expected_directions(self, session, monkeypatch):
        _new_game(session)
        origin = session.renderer._camera_origin

        _hold_keys(monkeypatch, "End")  # pan_camera_east
        session._poll_camera_pan_keys()
        assert session.renderer._camera_origin == (origin[0] + 1, origin[1])

        _hold_keys(monkeypatch, "Home")  # pan_camera_west
        session._poll_camera_pan_keys()
        assert session.renderer._camera_origin == (origin[0], origin[1])

        _hold_keys(monkeypatch, "PageDown")  # pan_camera_south
        session._poll_camera_pan_keys()
        assert session.renderer._camera_origin == (origin[0], origin[1] + 1)

    def test_opposite_pan_keys_held_together_cancel_out(self, session, monkeypatch):
        _new_game(session)
        origin_before = session.renderer._camera_origin
        _hold_keys(monkeypatch, "Home", "End")  # west + east simultaneously

        session._poll_camera_pan_keys()

        assert session.renderer._camera_origin == origin_before

    def test_diagonal_pan_keys_held_together_combine(self, session, monkeypatch):
        _new_game(session)
        origin_before = session.renderer._camera_origin
        _hold_keys(monkeypatch, "PageUp", "Home")  # north + west

        session._poll_camera_pan_keys()

        assert session.renderer._camera_origin == (origin_before[0] - 1, origin_before[1] - 1)

    def test_pan_keys_do_nothing_outside_the_playing_state(self, session, monkeypatch):
        assert session.state == "menu"  # no _new_game() call
        origin_before = session.renderer._camera_origin
        _hold_keys(monkeypatch, "PageUp")

        session._poll_camera_pan_keys()

        assert session.renderer._camera_origin == origin_before

    def test_keyboard_pan_does_not_touch_player_position(self, session, monkeypatch):
        _new_game(session)
        position = session.world.get_component(session.player_id, PositionComponent)
        before_pos = (position.x, position.y)
        _hold_keys(monkeypatch, "PageUp", "End")

        session._poll_camera_pan_keys()

        assert (position.x, position.y) == before_pos


def _find_widget(spec: dict, widget_id: str) -> dict | None:
    if spec.get("id") == widget_id:
        return spec
    for child in spec.get("children", []):
        found = _find_widget(child, widget_id)
        if found is not None:
            return found
    return None
