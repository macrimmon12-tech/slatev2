"""Unit tests for engine/systems/campaign.py — see
docs/components/06-worldgen-campaign.md §7/§8."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from engine.core import save
from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.registry import DataRegistry
from engine.systems import campaign, worldgen
from engine.systems.ai import AIComponent, PlayerTagComponent, PositionComponent

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "worldgen"


@pytest.fixture(autouse=True)
def _clean_module_state():
    worldgen.reset_module_state()
    campaign.set_data_registry(None)
    yield
    worldgen.reset_module_state()
    campaign.set_data_registry(None)


def _fixture_registry() -> DataRegistry:
    registry = DataRegistry()
    registry.load(FIXTURES)
    return registry


def _spawn_player(world: World, pos=(0, 0)) -> int:
    player_id = world.create_entity()
    world.add_component(player_id, PlayerTagComponent())
    world.add_component(player_id, PositionComponent(*pos))
    return player_id


# ---------------------------------------------------------------------------
# CampaignSystem: depth resolved from level_def, not list index
# ---------------------------------------------------------------------------


def test_load_campaign_builds_linear_level_order_excluding_hubs():
    registry = _fixture_registry()
    campaign.set_data_registry(registry)
    world = World()
    bus = EventBus()
    cs = campaign.CampaignSystem(world, bus)

    cs.load_campaign("sample_campaign")

    # level_hub is 2nd in the raw list but excluded from the linear order.
    assert cs._linear_level_ids == ["level_a", "level_deep_but_early", "level_b"]


def test_depth_is_resolved_from_level_def_not_sequence_index():
    """§7: a fixture campaign where a later-in-sequence level (level_b,
    depth 2) has a LOWER depth than an earlier one (level_deep_but_early,
    depth 9) -- worldgen.current_depth must reflect level_def['depth'],
    never the level's position in the list."""
    registry = _fixture_registry()
    campaign.set_data_registry(registry)
    worldgen.set_data_registry(registry)
    world = World()
    bus = EventBus()
    cs = campaign.CampaignSystem(world, bus, rng=random.Random(1))
    cs.load_campaign("sample_campaign")

    cs.enter_level("level_deep_but_early")
    assert worldgen.current_depth(world) == 9

    cs.enter_level("level_b")
    assert worldgen.current_depth(world) == 2  # lower depth despite being later in the list


def test_hub_is_excluded_from_stair_use_linear_advance():
    registry = _fixture_registry()
    campaign.set_data_registry(registry)
    worldgen.set_data_registry(registry)
    world = World()
    bus = EventBus()
    player_id = _spawn_player(world)
    cs = campaign.CampaignSystem(world, bus, rng=random.Random(1))
    cs.load_campaign("sample_campaign")

    cs.enter_level("level_a")
    bus.emit("stair_use", {"entity_id": player_id, "direction": "down"})
    # Must skip level_hub entirely -- next in the *linear* order is
    # level_deep_but_early, not level_hub.
    assert cs.current_level_id == "level_deep_but_early"


def test_stair_use_down_from_hub_or_unvisited_state_enters_first_linear_level():
    registry = _fixture_registry()
    campaign.set_data_registry(registry)
    worldgen.set_data_registry(registry)
    world = World()
    bus = EventBus()
    player_id = _spawn_player(world)
    cs = campaign.CampaignSystem(world, bus, rng=random.Random(1))
    cs.load_campaign("sample_campaign")

    bus.emit("stair_use", {"entity_id": player_id, "direction": "down"})
    assert cs.current_level_id == "level_a"


def test_stair_use_up_at_first_level_is_a_no_op():
    registry = _fixture_registry()
    campaign.set_data_registry(registry)
    worldgen.set_data_registry(registry)
    world = World()
    bus = EventBus()
    player_id = _spawn_player(world)
    cs = campaign.CampaignSystem(world, bus, rng=random.Random(1))
    cs.load_campaign("sample_campaign")
    cs.enter_level("level_a")

    bus.emit("stair_use", {"entity_id": player_id, "direction": "up"})
    assert cs.current_level_id == "level_a"


def test_trigger_level_complete_emits_campaign_complete_only_on_last_linear_level():
    registry = _fixture_registry()
    campaign.set_data_registry(registry)
    worldgen.set_data_registry(registry)
    world = World()
    bus = EventBus()
    player_id = _spawn_player(world)
    completed = []
    bus.subscribe("campaign_complete", lambda payload: completed.append(payload))
    cs = campaign.CampaignSystem(world, bus, rng=random.Random(1))
    cs.load_campaign("sample_campaign")

    cs.enter_level("level_a")
    bus.emit("trigger_level_complete", {"entity_id": player_id})
    assert completed == []  # not the last linear level yet

    cs.enter_level("level_b")  # the last entry in _linear_level_ids
    bus.emit("trigger_level_complete", {"entity_id": player_id})
    assert completed == [{"campaign_id": "sample_campaign"}]


def test_trigger_level_complete_on_hub_never_completes_campaign():
    registry = _fixture_registry()
    campaign.set_data_registry(registry)
    worldgen.set_data_registry(registry)
    world = World()
    bus = EventBus()
    player_id = _spawn_player(world)
    completed = []
    bus.subscribe("campaign_complete", lambda payload: completed.append(payload))
    cs = campaign.CampaignSystem(world, bus, rng=random.Random(1))
    cs.load_campaign("sample_campaign")

    cs.enter_level("level_hub")
    bus.emit("trigger_level_complete", {"entity_id": player_id})
    assert completed == []


# ---------------------------------------------------------------------------
# FloorManager: generate-fresh vs restore, snapshot, auto-save
# ---------------------------------------------------------------------------


def test_floor_manager_generates_fresh_on_first_visit_and_restores_on_revisit():
    registry = _fixture_registry()
    worldgen.set_data_registry(registry)
    world = World()
    bus = EventBus()
    fm = campaign.FloorManager(world, bus, rng=random.Random(1))

    level_a = {"id": "a", "depth": 1, "width": 30, "height": 20, "monster_spawns": [{"entity_id": "fixture_rat", "count": [1, 1]}], "vaults": []}
    level_b = {"id": "b", "depth": 2, "width": 30, "height": 20, "monster_spawns": [], "vaults": []}

    fm.transition_to(level_a)
    monster_ids = [row[0] for row in world.query(AIComponent)]
    assert len(monster_ids) == 1
    world.destroy_entity(monster_ids[0])  # kill it before leaving

    fm.transition_to(level_b)
    assert world.query(AIComponent) == []  # b has no monster_spawns

    fm.transition_to(level_a)  # revisit -- must restore, not regenerate
    assert world.query(AIComponent) == []  # the kill persisted across the round trip


def test_floor_manager_emits_floor_changed_with_correct_depths():
    world = World()
    bus = EventBus()
    events = []
    bus.subscribe("floor_changed", lambda payload: events.append(payload))
    fm = campaign.FloorManager(world, bus, rng=random.Random(1))

    fm.transition_to({"id": "a", "depth": 1, "width": 20, "height": 15, "monster_spawns": [], "vaults": []})
    fm.transition_to({"id": "b", "depth": 5, "width": 20, "height": 15, "monster_spawns": [], "vaults": []})

    assert events[0] == {"from_depth": None, "to_depth": 1}
    assert events[1] == {"from_depth": 1, "to_depth": 5}


def test_floor_manager_calls_serialize_world_after_every_transition():
    world = World()
    bus = EventBus()
    saved = []
    fm = campaign.FloorManager(world, bus, rng=random.Random(1), save_sink=saved.append)

    fm.transition_to({"id": "a", "depth": 1, "width": 20, "height": 15, "monster_spawns": [], "vaults": []})
    fm.transition_to({"id": "b", "depth": 2, "width": 20, "height": 15, "monster_spawns": [], "vaults": []})

    assert len(saved) == 2
    assert "entities" in saved[-1]
    assert fm.last_save_data == saved[-1]


def test_floor_manager_player_entity_persists_across_transitions_untouched():
    world = World()
    bus = EventBus()
    player_id = _spawn_player(world)
    fm = campaign.FloorManager(world, bus, rng=random.Random(1))

    fm.transition_to({"id": "a", "depth": 1, "width": 20, "height": 15, "monster_spawns": [], "vaults": []})
    fm.transition_to({"id": "b", "depth": 2, "width": 20, "height": 15, "monster_spawns": [], "vaults": []})

    assert world.get_component(player_id, PlayerTagComponent) is not None


def test_validate_campaign_data_accepts_the_real_shipped_campaign():
    import json

    data = json.loads(
        (Path(__file__).resolve().parent.parent.parent / "data" / "campaigns" / "the_sunken_keep.json").read_text()
    )
    assert campaign.validate_campaign_data(data) == []


def test_validate_campaign_data_rejects_missing_required_fields():
    errors = campaign.validate_campaign_data({"levels": [{"id": "only_id"}]})
    assert errors
