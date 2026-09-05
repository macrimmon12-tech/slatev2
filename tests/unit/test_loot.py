"""Unit tests for `engine.systems.loot` (component doc
`04-inventory-items-loot.md` §2.4, Definition of Done)."""

import random
from pathlib import Path

import pytest

from engine.core.ecs import World
from engine.core.registry import DataRegistry
from engine.systems.inventory import (
    ItemInstanceComponent,
    assert_no_rarity_field,
    reset_identified_base_ids,
)
from engine.systems.loot import (
    resolve_loot_table,
    roll_rarity,
    set_active_registry,
    set_active_spawned_uniques,
    spawn_item_instance,
)

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "fixtures"
REPO_DATA_ROOT = Path(__file__).resolve().parent.parent.parent / "data"


@pytest.fixture(autouse=True)
def _reset_module_globals():
    reset_identified_base_ids()
    set_active_registry(None)
    set_active_spawned_uniques(set())
    yield
    reset_identified_base_ids()
    set_active_registry(None)
    set_active_spawned_uniques(set())


@pytest.fixture
def registry() -> DataRegistry:
    reg = DataRegistry()
    reg.load(FIXTURES_ROOT)
    return reg


# ---------------------------------------------------------------------------
# resolve_loot_table
# ---------------------------------------------------------------------------


def test_resolve_loot_table_weighted_rolling_favors_heavier_entries(registry):
    loot_table = {
        "entries": [
            {"item_id": "dagger", "weight": 1, "quantity": [1, 1]},
            {"item_id": "potion", "weight": 99, "quantity": [1, 1]},
        ]
    }
    rng = random.Random(5)
    counts = {"dagger": 0, "potion": 0}
    for _ in range(300):
        resolved = resolve_loot_table(loot_table, depth=1, is_boss=False, registry=registry, rng=rng)
        counts[resolved[0]["item_base_id"]] += 1
    assert counts["potion"] > counts["dagger"]


def test_resolve_loot_table_quantity_range_respected(registry):
    loot_table = {"entries": [{"item_id": "dagger", "weight": 1, "quantity": [2, 4]}]}
    rng = random.Random(1)
    for _ in range(50):
        resolved = resolve_loot_table(loot_table, depth=1, is_boss=False, registry=registry, rng=rng)
        assert 2 <= resolved[0]["quantity"] <= 4


def test_resolve_loot_table_empty_entries_is_harmless():
    assert resolve_loot_table({"entries": []}, depth=1, is_boss=False) == []
    assert resolve_loot_table({}, depth=1, is_boss=False) == []


def test_boss_legendary_first_lookup_returns_legendary_on_success(registry):
    loot_table = {"entries": [{"item_id": "dagger", "weight": 1, "quantity": [1, 1]}]}

    class AlwaysHitRandom(random.Random):
        def random(self):
            return 0.0  # always beats any drop_chance > 0

    rng = AlwaysHitRandom()
    resolved = resolve_loot_table(loot_table, depth=1, is_boss=True, registry=registry, spawned_uniques=set(), rng=rng)
    assert resolved == [{"item_base_id": "testblade", "quantity": 1}]


def test_boss_legendary_roll_failure_falls_through_to_normal_table(registry):
    loot_table = {"entries": [{"item_id": "dagger", "weight": 1, "quantity": [1, 1]}]}

    class AlwaysMissRandom(random.Random):
        def random(self):
            return 0.999  # never beats drop_chance

    rng = AlwaysMissRandom()
    resolved = resolve_loot_table(loot_table, depth=1, is_boss=True, registry=registry, spawned_uniques=set(), rng=rng)
    assert resolved == [{"item_base_id": "dagger", "quantity": 1}]


def test_boss_legendary_first_lookup_excludes_spawned_uniques(registry):
    loot_table = {"entries": [{"item_id": "dagger", "weight": 1, "quantity": [1, 1]}]}

    class AlwaysHitRandom(random.Random):
        def random(self):
            return 0.0

    rng = AlwaysHitRandom()
    already_spawned = {"testblade"}
    resolved = resolve_loot_table(
        loot_table, depth=1, is_boss=True, registry=registry, spawned_uniques=already_spawned, rng=rng
    )
    # testblade excluded -> falls through to the normal weighted table.
    assert resolved == [{"item_base_id": "dagger", "quantity": 1}]


def test_non_boss_never_rolls_legendary_first(registry):
    loot_table = {"entries": [{"item_id": "dagger", "weight": 1, "quantity": [1, 1]}]}

    class AlwaysHitRandom(random.Random):
        def random(self):
            return 0.0

    rng = AlwaysHitRandom()
    resolved = resolve_loot_table(loot_table, depth=1, is_boss=False, registry=registry, rng=rng)
    assert resolved == [{"item_base_id": "dagger", "quantity": 1}]


# ---------------------------------------------------------------------------
# roll_rarity (depth-window interpolation, §5.4)
# ---------------------------------------------------------------------------


def test_roll_rarity_differs_statistically_between_shallow_and_deep(registry):
    rng_shallow = random.Random(11)
    rng_deep = random.Random(11)

    shallow_counts = {}
    deep_counts = {}
    for _ in range(500):
        r = roll_rarity(1, 20, depth=1, registry=registry, rng=rng_shallow)
        shallow_counts[r] = shallow_counts.get(r, 0) + 1
    for _ in range(500):
        r = roll_rarity(1, 20, depth=20, registry=registry, rng=rng_deep)
        deep_counts[r] = deep_counts.get(r, 0) + 1

    # At t=0 commons dominate; at t=1 legendary/epic/rare dominate.
    assert shallow_counts.get("common", 0) > deep_counts.get("common", 0)
    assert deep_counts.get("legendary", 0) > shallow_counts.get("legendary", 0)


def test_roll_rarity_falls_back_to_default_table_without_registry():
    rng = random.Random(3)
    rarity = roll_rarity(1, 10, depth=1, registry=None, rng=rng)
    assert rarity in ("common", "uncommon", "rare", "epic", "legendary")


# ---------------------------------------------------------------------------
# spawn_item_instance
# ---------------------------------------------------------------------------


def test_spawn_item_instance_creates_real_entity_at_position(registry):
    world = World()
    instance_id = spawn_item_instance("dagger", depth=1, position=(3, 4), world=world, registry=registry)

    found = [
        (eid, item) for eid, item in world.query(ItemInstanceComponent) if item.instance_id == instance_id
    ]
    assert len(found) == 1
    _eid, item = found[0]
    assert item.base_id == "dagger"
    assert item.rarity in ("common", "uncommon", "rare", "epic", "legendary")


def test_spawn_item_instance_rejects_rarity_field_in_content():
    world = World()
    reg = DataRegistry()
    reg.load(FIXTURES_ROOT / "invalid_items")
    with pytest.raises(ValueError):
        spawn_item_instance("bad_item", depth=1, position=(0, 0), world=world, registry=reg)


def test_spawn_legendary_bypasses_affix_generator_and_is_preidentified(registry):
    world = World()
    instance_id = spawn_item_instance("testblade", depth=10, position=(0, 0), world=world, registry=registry)
    found = [item for _eid, item in world.query(ItemInstanceComponent) if item.instance_id == instance_id]
    item = found[0]
    assert item.rarity == "legendary"
    assert item.affixes == []
    assert item.identified_type is True


def test_spawn_legendary_adds_to_spawned_uniques_and_second_spawn_is_skippable(registry):
    world = World()
    spawned_uniques: set[str] = set()
    spawn_item_instance(
        "testblade", depth=10, position=(0, 0), world=world, registry=registry, spawned_uniques=spawned_uniques
    )
    assert "testblade" in spawned_uniques

    # A second legendary-first roll must now fall through — this is the
    # DoD-required behavior; resolve_loot_table is the actual gate a caller
    # uses to avoid a second legendary drop.
    loot_table = {"entries": [{"item_id": "dagger", "weight": 1, "quantity": [1, 1]}]}

    class AlwaysHitRandom(random.Random):
        def random(self):
            return 0.0

    resolved = resolve_loot_table(
        loot_table, depth=10, is_boss=True, registry=registry, spawned_uniques=spawned_uniques, rng=AlwaysHitRandom()
    )
    assert resolved == [{"item_base_id": "dagger", "quantity": 1}]


def test_spawn_item_instance_respects_globally_identified_base_id(registry):
    from engine.systems.inventory import mark_base_id_identified

    mark_base_id_identified("dagger")
    world = World()
    instance_id = spawn_item_instance("dagger", depth=1, position=(0, 0), world=world, registry=registry)
    found = [item for _eid, item in world.query(ItemInstanceComponent) if item.instance_id == instance_id]
    assert found[0].identified_type is True


def test_spawn_item_instance_falls_back_to_active_registry_when_omitted(registry):
    set_active_registry(registry)
    world = World()
    instance_id = spawn_item_instance("dagger", depth=1, position=(0, 0), world=world)
    found = [item for _eid, item in world.query(ItemInstanceComponent) if item.instance_id == instance_id]
    assert found[0].base_id == "dagger"


# ---------------------------------------------------------------------------
# Real shipped content round-trip (CONTRACTS.md §9: every new JSON schema
# ships a round-trip test loading every fixture file under its data/
# directory — this component's real `data/items`, `data/affixes`,
# `data/items/sets`, `data/items/legendary` content).
# ---------------------------------------------------------------------------


def test_real_shipped_content_loads_and_has_no_rarity_field():
    real_registry = DataRegistry()
    real_registry.load(REPO_DATA_ROOT)

    items = real_registry.all("items")
    legendaries = real_registry.all("legendaries")
    sets = real_registry.all("sets")
    affixes = real_registry.all("affixes")

    assert items, "data/items/ must ship at least one real item fixture"
    assert legendaries, "data/items/legendary/ must ship at least one real legendary"
    assert sets, "data/items/sets/ must ship at least one real set definition"
    assert affixes, "data/affixes/ must ship at least one real affix"

    for item_id, item_def in items.items():
        assert_no_rarity_field(item_def, item_id, "items")
    for item_id, item_def in legendaries.items():
        assert_no_rarity_field(item_def, item_id, "legendaries")

    loot_tables_cfg = real_registry.get("configs", "loot_tables")
    assert loot_tables_cfg is not None
    assert "rarity_interpolation" in loot_tables_cfg
    assert "floor_loot" in loot_tables_cfg


def test_real_shipped_set_pieces_actually_reference_a_real_set_definition():
    real_registry = DataRegistry()
    real_registry.load(REPO_DATA_ROOT)

    items = real_registry.all("items")
    sets = real_registry.all("sets")
    set_ids_referenced = {item_def["set_id"] for item_def in items.values() if item_def.get("set_id")}
    assert set_ids_referenced, "expected at least one shipped item to carry a set_id"
    for set_id in set_ids_referenced:
        assert set_id in sets, f"item references set_id {set_id!r} with no matching data/items/sets/ definition"
