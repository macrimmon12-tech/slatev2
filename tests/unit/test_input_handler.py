"""Unit tests for engine.input.input_handler -- see
docs/components/07-input-renderer-audio.md §8/§9 for the Definition of
Done bullets these correspond to.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import pytest

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.spatial_hash import SpatialHash
from engine.input.input_handler import DEFAULT_CONTROLS, InputHandler
from engine.systems.ai import PlayerTagComponent, PositionComponent

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture(scope="module", autouse=True)
def _pygame_headless():
    pygame.init()
    yield
    pygame.quit()


def load_fixture_controls() -> dict:
    with (FIXTURES / "controls_minimal.json").open() as fh:
        return json.load(fh)


def make_player(world: World) -> int:
    entity_id = world.create_entity()
    world.add_component(entity_id, PositionComponent(x=5, y=5))
    world.add_component(entity_id, PlayerTagComponent())
    return entity_id


def key_event(key_code: int) -> pygame.event.Event:
    return pygame.event.Event(pygame.KEYDOWN, key=key_code)


def collect(bus: EventBus, event_type: str) -> list:
    received = []
    bus.subscribe(event_type, lambda payload: received.append(payload))
    return received


# -- config loading -----------------------------------------------------------


def test_loads_controls_config_contexts():
    bus = EventBus()
    handler = InputHandler(bus, load_fixture_controls())
    assert "move_north" in handler._contexts["game"]
    assert handler._contexts["game"]["move_north"] == ["Up"]


def test_missing_config_falls_back_to_default_controls():
    bus = EventBus()
    handler = InputHandler(bus, None)
    assert handler._contexts == DEFAULT_CONTROLS


def test_malformed_config_falls_back_to_default_controls():
    bus = EventBus()
    handler = InputHandler(bus, {"nonsense": True})
    assert handler._contexts == DEFAULT_CONTROLS


# -- context scoping (same key, different action per context) ----------------


def test_same_physical_key_different_action_per_context():
    world = World()
    bus = EventBus()
    make_player(world)
    handler = InputHandler(bus, load_fixture_controls(), world=world)

    escape_code = pygame.key.key_code("Escape")

    shown = collect(bus, "show_panel")
    handler.set_context("game")
    handler.handle_pygame_event(key_event(escape_code))
    assert shown == [{"panel_id": "menu", "data": None}]

    cancelled = collect(bus, "cancel_targeting")
    handler.set_context("targeting")
    handler.handle_pygame_event(key_event(escape_code))
    assert len(cancelled) == 1

    quit_selected = []
    bus.subscribe("quit_selected", lambda payload: quit_selected.append(payload))
    handler.set_context("ui")
    handler.handle_pygame_event(key_event(escape_code))
    assert quit_selected == [None]


def test_unbound_key_in_context_is_ignored():
    world = World()
    bus = EventBus()
    make_player(world)
    handler = InputHandler(bus, load_fixture_controls(), world=world)
    handler.set_context("ui")
    moved = collect(bus, "player_moved")
    # "Up" has no binding in the "ui" context in the fixture config.
    handler.handle_pygame_event(key_event(pygame.key.key_code("Up")))
    assert moved == []


# -- multiple keys per action --------------------------------------------------


def test_multiple_keys_bound_to_same_action():
    world = World()
    bus = EventBus()
    make_player(world)
    handler = InputHandler(
        bus,
        {"contexts": {"game": {"move_east": ["Right", "d", "KP6"]}}},
        world=world,
    )
    moved = collect(bus, "player_moved")
    handler.handle_pygame_event(key_event(pygame.key.key_code("d")))
    handler.handle_pygame_event(key_event(pygame.K_KP6))
    assert [payload["to"] for payload in moved] == [(6, 5), (7, 5)]


# -- rebind ---------------------------------------------------------------------


def test_rebind_replaces_existing_binding():
    world = World()
    bus = EventBus()
    make_player(world)
    handler = InputHandler(bus, load_fixture_controls(), world=world)
    moved = collect(bus, "player_moved")

    handler.rebind("move_north", "game", "n")
    # Old key no longer works.
    handler.handle_pygame_event(key_event(pygame.key.key_code("Up")))
    assert moved == []
    # New key does.
    handler.handle_pygame_event(key_event(pygame.key.key_code("n")))
    assert len(moved) == 1


# -- movement -------------------------------------------------------------------


def test_move_updates_position_component_and_emits_events():
    world = World()
    bus = EventBus()
    entity_id = make_player(world)
    handler = InputHandler(bus, load_fixture_controls(), world=world)

    entity_moved = collect(bus, "entity_moved")
    player_moved = collect(bus, "player_moved")

    handler.handle_pygame_event(key_event(pygame.key.key_code("Right")))

    position = world.get_component(entity_id, PositionComponent)
    assert (position.x, position.y) == (6, 5)
    assert entity_moved == [{"entity_id": entity_id, "from": (5, 5), "to": (6, 5)}]
    assert player_moved == [{"entity_id": entity_id, "from": (5, 5), "to": (6, 5)}]


def test_move_updates_spatial_hash():
    world = World()
    bus = EventBus()
    entity_id = make_player(world)
    spatial_hash = SpatialHash()
    spatial_hash.insert(entity_id, (5, 5))
    handler = InputHandler(bus, load_fixture_controls(), world=world, spatial_hash=spatial_hash)

    handler.handle_pygame_event(key_event(pygame.key.key_code("Right")))

    assert spatial_hash.position_of(entity_id) == (6, 5)


def test_move_blocked_by_is_passable():
    world = World()
    bus = EventBus()
    entity_id = make_player(world)
    handler = InputHandler(
        bus, load_fixture_controls(), world=world, is_passable=lambda pos: False
    )
    moved = collect(bus, "player_moved")

    handler.handle_pygame_event(key_event(pygame.key.key_code("Right")))

    position = world.get_component(entity_id, PositionComponent)
    assert (position.x, position.y) == (5, 5)
    assert moved == []


def test_move_with_no_player_entity_is_a_noop():
    world = World()
    bus = EventBus()
    handler = InputHandler(bus, load_fixture_controls(), world=world)
    moved = collect(bus, "player_moved")

    handler.handle_pygame_event(key_event(pygame.key.key_code("Right")))

    assert moved == []


# -- interact ---------------------------------------------------------------------


def test_interact_emits_entity_interacted_for_adjacent_entity():
    world = World()
    bus = EventBus()
    entity_id = make_player(world)
    spatial_hash = SpatialHash()
    spatial_hash.insert(entity_id, (5, 5))
    npc_id = world.create_entity()
    spatial_hash.insert(npc_id, (6, 5))
    handler = InputHandler(bus, load_fixture_controls(), world=world, spatial_hash=spatial_hash)

    interacted = collect(bus, "entity_interacted")
    handler.handle_pygame_event(key_event(pygame.key.key_code("e")))

    assert interacted == [{"actor_id": entity_id, "target_id": npc_id}]


def test_interact_with_no_adjacent_entity_is_a_noop():
    world = World()
    bus = EventBus()
    entity_id = make_player(world)
    spatial_hash = SpatialHash()
    spatial_hash.insert(entity_id, (5, 5))
    handler = InputHandler(bus, load_fixture_controls(), world=world, spatial_hash=spatial_hash)

    interacted = collect(bus, "entity_interacted")
    handler.handle_pygame_event(key_event(pygame.key.key_code("e")))

    assert interacted == []


# -- use_stairs ---------------------------------------------------------------------


def test_use_stairs_direction_from_key():
    world = World()
    bus = EventBus()
    entity_id = make_player(world)
    handler = InputHandler(
        bus, {"contexts": {"game": {"use_stairs": ["Greater", "Less"]}}}, world=world
    )
    stair_events = collect(bus, "stair_use")

    handler.handle_pygame_event(key_event(pygame.K_GREATER))
    handler.handle_pygame_event(key_event(pygame.K_LESS))

    assert stair_events == [
        {"entity_id": entity_id, "direction": "down"},
        {"entity_id": entity_id, "direction": "up"},
    ]


# -- targeting context --------------------------------------------------------------


def test_confirm_target_uses_get_current_target_callback():
    world = World()
    bus = EventBus()
    make_player(world)
    handler = InputHandler(
        bus,
        load_fixture_controls(),
        world=world,
        get_current_target=lambda: (7, 7),
    )
    handler.set_context("targeting")
    confirmed = collect(bus, "target_confirmed")

    handler.handle_pygame_event(key_event(pygame.key.key_code("Return")))

    assert confirmed == [{"target": (7, 7)}]


def test_confirm_target_without_callback_sends_none():
    world = World()
    bus = EventBus()
    make_player(world)
    handler = InputHandler(bus, load_fixture_controls(), world=world)
    handler.set_context("targeting")
    confirmed = collect(bus, "target_confirmed")

    handler.handle_pygame_event(key_event(pygame.key.key_code("Return")))

    assert confirmed == [{"target": None}]


# -- cast spell / use item slot resolvers (absence = zero cost) --------------------


def test_cast_spell_without_resolver_is_a_noop():
    world = World()
    bus = EventBus()
    make_player(world)
    handler = InputHandler(
        bus, {"contexts": {"game": {"cast_spell_1": ["1"]}}}, world=world
    )
    cast = collect(bus, "spell_cast_initiated")

    handler.handle_pygame_event(key_event(pygame.key.key_code("1")))

    assert cast == []


def test_cast_spell_with_resolver_emits_spell_cast_initiated():
    world = World()
    bus = EventBus()
    entity_id = make_player(world)
    handler = InputHandler(
        bus,
        {"contexts": {"game": {"cast_spell_1": ["1"]}}},
        world=world,
        resolve_spell_slot=lambda eid, slot: "fireball" if slot == 1 else None,
    )
    cast = collect(bus, "spell_cast_initiated")

    handler.handle_pygame_event(key_event(pygame.key.key_code("1")))

    assert cast == [{"entity_id": entity_id, "spell_id": "fireball", "targeting_mode": None}]


def test_use_item_without_resolver_is_a_noop():
    world = World()
    bus = EventBus()
    make_player(world)
    handler = InputHandler(
        bus, {"contexts": {"game": {"use_item_1": ["q"]}}}, world=world
    )
    used = collect(bus, "item_used")

    handler.handle_pygame_event(key_event(pygame.key.key_code("q")))

    assert used == []


def test_use_item_with_resolver_emits_item_used():
    world = World()
    bus = EventBus()
    entity_id = make_player(world)
    handler = InputHandler(
        bus,
        {"contexts": {"game": {"use_item_1": ["q"]}}},
        world=world,
        resolve_item_slot=lambda eid, slot: "potion_001" if slot == 1 else None,
    )
    used = collect(bus, "item_used")

    handler.handle_pygame_event(key_event(pygame.key.key_code("q")))

    assert used == [{"entity_id": entity_id, "item_instance_id": "potion_001"}]


# -- show_panel / quit ----------------------------------------------------------------


def test_inventory_key_emits_show_panel():
    world = World()
    bus = EventBus()
    make_player(world)
    handler = InputHandler(bus, DEFAULT_CONTROLS, world=world)
    shown = collect(bus, "show_panel")

    handler.handle_pygame_event(key_event(pygame.key.key_code("i")))

    assert shown == [{"panel_id": "inventory", "data": None}]
