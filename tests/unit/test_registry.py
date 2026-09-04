from pathlib import Path

import pytest

from engine.core.registry import DataRegistry, DuplicateIdError, NAMESPACE_TABLE

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "data_registry"


def _loaded_registry() -> DataRegistry:
    registry = DataRegistry()
    registry.load(FIXTURES / "base")
    return registry


def test_namespace_table_covers_contracts_namespaces():
    expected = {
        "entities",
        "items",
        "spells",
        "effects",
        "affixes",
        "campaigns",
        "vaults",
        "legendaries",
        "sets",
        "maps",
        "configs",
        "npcs",
        "dialogs",
        "shops",
    }
    assert expected <= set(NAMESPACE_TABLE)


@pytest.mark.parametrize(
    "namespace,expected_id",
    [
        ("entities", "goblin"),
        ("items", "sword"),
        ("spells", "fireball"),
        ("effects", "burn"),
        ("affixes", "of_fire"),
        ("campaigns", "main"),
        ("vaults", "vault1"),
        ("legendaries", "excalibur"),
        ("sets", "set_piece"),
        ("maps", "floor1"),
        ("npcs", "villager"),
        ("dialogs", "villager_dialog"),
        ("shops", "general_store"),
    ],
)
def test_each_namespace_loads_its_fixture(namespace, expected_id):
    registry = _loaded_registry()
    entry = registry.get(namespace, expected_id)
    assert entry is not None
    assert entry["id"] == expected_id


def test_entities_namespace_excludes_npcs_subdir():
    registry = _loaded_registry()
    assert registry.get("entities", "villager") is None
    assert "villager" not in registry.all("entities")
    assert registry.get("npcs", "villager") is not None


def test_items_namespace_excludes_legendary_and_sets():
    registry = _loaded_registry()
    assert registry.get("items", "excalibur") is None
    assert registry.get("items", "set_piece") is None
    assert registry.get("legendaries", "excalibur") is not None
    assert registry.get("sets", "set_piece") is not None


def test_maps_namespace_excludes_vaults():
    registry = _loaded_registry()
    assert registry.get("maps", "vault1") is None
    assert registry.get("vaults", "vault1") is not None


def test_configs_keyed_by_filename_stem_not_id_field():
    registry = _loaded_registry()
    game_config = registry.get("configs", "game")
    assert game_config == {"schema_version": 1}
    controls_config = registry.get("configs", "controls")
    assert controls_config == {"move_up": ["w", "up"]}


def test_get_missing_id_returns_none():
    registry = _loaded_registry()
    assert registry.get("entities", "does_not_exist") is None


def test_get_unknown_namespace_raises():
    registry = _loaded_registry()
    with pytest.raises(KeyError):
        registry.get("not_a_namespace", "x")


def test_all_returns_copy_not_live_reference():
    registry = _loaded_registry()
    entities = registry.all("entities")
    entities["goblin"] = {"tampered": True}
    assert registry.get("entities", "goblin")["name"] == "Goblin"


def test_mods_load_order_additive_new_ids():
    registry = DataRegistry()
    registry.load(FIXTURES / "base", mod_load_order=[FIXTURES / "mod_a"])
    assert registry.get("entities", "goblin") is not None
    assert registry.get("entities", "goblin_variant") is not None


def test_higher_priority_mod_overrides_silently_no_warning(caplog):
    registry = DataRegistry()
    with caplog.at_level("WARNING"):
        registry.load(FIXTURES / "base", mod_load_order=[FIXTURES / "mod_b"])

    goblin = registry.get("entities", "goblin")
    assert goblin["name"] == "Goblin (mod override)"
    assert goblin["hp"] == 20
    # Silent override — CONTRACTS.md §4 explicitly says no warning here.
    assert not any("goblin" in record.message.lower() for record in caplog.records)


def test_load_order_list_ordering_matters():
    """mod_load_order is ascending priority — later entries in the list win
    over earlier ones (and both win over the base data_root)."""
    registry = DataRegistry()
    registry.load(
        FIXTURES / "base",
        mod_load_order=[FIXTURES / "mod_a", FIXTURES / "mod_b"],
    )
    goblin = registry.get("entities", "goblin")
    assert goblin["name"] == "Goblin (mod override)"  # mod_b (later) wins
    assert registry.get("entities", "goblin_variant") is not None  # mod_a still additive


def test_duplicate_id_same_priority_raises_with_both_paths():
    registry = DataRegistry()
    with pytest.raises(DuplicateIdError) as exc_info:
        registry.load(FIXTURES / "dup_same_priority")
    message = str(exc_info.value)
    assert "goblin_1.json" in message
    assert "goblin_2.json" in message


def test_reload_rescans_from_same_sources():
    registry = _loaded_registry()
    assert registry.get("entities", "goblin") is not None
    registry.reload()
    assert registry.get("entities", "goblin") is not None


def test_reload_before_load_raises():
    registry = DataRegistry()
    with pytest.raises(RuntimeError):
        registry.reload()


def test_missing_namespace_directory_is_zero_cost(tmp_path):
    """A source root that doesn't have every namespace's directory must
    not raise — absence = zero cost (CONTRACTS.md §2 rule 7)."""
    registry = DataRegistry()
    registry.load(tmp_path)  # empty directory, nothing under it
    for namespace in NAMESPACE_TABLE:
        assert registry.all(namespace) == {}


def test_content_file_missing_id_field_raises_at_load_time(tmp_path):
    entities_dir = tmp_path / "entities"
    entities_dir.mkdir()
    (entities_dir / "broken.json").write_text('{"name": "No ID here"}')

    registry = DataRegistry()
    with pytest.raises(ValueError, match="id"):
        registry.load(tmp_path)


def test_malformed_json_raises_at_load_time(tmp_path):
    entities_dir = tmp_path / "entities"
    entities_dir.mkdir()
    (entities_dir / "broken.json").write_text("{not valid json")

    registry = DataRegistry()
    with pytest.raises(ValueError):
        registry.load(tmp_path)
