"""Unit tests for engine/systems/vaults.py — see
docs/components/06-worldgen-campaign.md §7/§8."""

from __future__ import annotations

import random

import pytest

from engine.systems import vaults
from engine.systems.worldgen import Tile, TileMap


@pytest.fixture(autouse=True)
def _clean_registry():
    vaults.set_data_registry(None)
    yield
    vaults.set_data_registry(None)


class _FixtureRegistry:
    """Minimal stand-in exposing DataRegistry's documented `get` shape --
    used so these are real ``inject()`` calls against real vault dicts, not
    a mock of ``inject`` itself."""

    def __init__(self, vaults_by_id: dict[str, dict]) -> None:
        self._vaults_by_id = vaults_by_id

    def get(self, namespace: str, vault_id: str) -> dict | None:
        assert namespace == "vaults"
        return self._vaults_by_id.get(vault_id)


_BIG_GUARANTEED_VAULT = {
    "id": "big_vault",
    "width": 5,
    "height": 5,
    "tiles": ["#####", "#...#", "#.#.#", "#...#", "#####"],
    "entity_spawns": [{"entity_id": "fixture_rat", "x": 1, "y": 1}],
    "loot_spawns": [{"item_id": "fixture_potion", "x": 3, "y": 3}],
}

_TINY_WEIGHTED_VAULT = {
    "id": "tiny_vault",
    "width": 2,
    "height": 2,
    "tiles": ["##", "##"],
    "entity_spawns": [],
    "loot_spawns": [],
}


def _tilemap_with_rooms(room_specs: dict[str, tuple[int, int, int, int]]) -> TileMap:
    """Builds a minimal fixture TileMap with the given room_id -> (x,y,w,h)
    bounds, all tiles inside marked walkable with that room_id."""
    tiles: dict[tuple[int, int], Tile] = {}
    rooms: dict[str, dict] = {}
    for room_id, (x, y, w, h) in room_specs.items():
        room_tiles = [(xx, yy) for xx in range(x, x + w) for yy in range(y, y + h)]
        for pos in room_tiles:
            tiles[pos] = Tile(walkable=True, room_id=room_id)
        rooms[room_id] = {"lit": False, "tiles": room_tiles, "bounds": (x, y, w, h)}
    return TileMap(width=20, height=20, tiles=tiles, rooms=rooms)


def test_inject_places_guaranteed_vault_into_fitting_room():
    vaults.set_data_registry(_FixtureRegistry({"big_vault": _BIG_GUARANTEED_VAULT}))
    tilemap = _tilemap_with_rooms({"r_00": (0, 0, 6, 6)})

    ok = vaults.inject(tilemap, [{"vault_id": "big_vault", "guaranteed": True}], depth=1, rng=random.Random(1))

    assert ok is True
    assert tilemap.rooms["r_00"]["vault_id"] == "big_vault"
    # A '#' vault tile is stamped non-walkable but still carries the room's
    # room_id (not None) -- and a '.' tile is walkable.
    origin_x, origin_y = tilemap.rooms["r_00"]["vault_origin"]
    assert tilemap.tiles[(origin_x, origin_y)].walkable is False
    assert tilemap.tiles[(origin_x, origin_y)].room_id == "r_00"
    assert tilemap.tiles[(origin_x + 1, origin_y + 1)].walkable is True
    assert tilemap.tiles[(origin_x + 1, origin_y + 1)].room_id == "r_00"


def test_inject_returns_false_when_no_room_fits_a_guaranteed_vault():
    """§7: unit tested with a vault template larger than any room a tiny
    fixture BSP layout could produce."""
    vaults.set_data_registry(_FixtureRegistry({"big_vault": _BIG_GUARANTEED_VAULT}))
    tilemap = _tilemap_with_rooms({"r_00": (0, 0, 3, 3)})  # smaller than the 5x5 vault

    ok = vaults.inject(tilemap, [{"vault_id": "big_vault", "guaranteed": True}], depth=1, rng=random.Random(1))

    assert ok is False
    assert "vault_id" not in tilemap.rooms["r_00"]


def test_inject_returns_false_when_guaranteed_vault_data_unresolvable():
    vaults.set_data_registry(_FixtureRegistry({}))
    tilemap = _tilemap_with_rooms({"r_00": (0, 0, 6, 6)})

    ok = vaults.inject(tilemap, [{"vault_id": "missing_vault", "guaranteed": True}], depth=1, rng=random.Random(1))

    assert ok is False


def test_inject_never_retries_on_weighted_vault_failure():
    """§7: weighted vaults fill remaining eligible rooms probabilistically,
    never causing a retry on failure to place."""
    vaults.set_data_registry(_FixtureRegistry({"tiny_vault": _TINY_WEIGHTED_VAULT}))
    tilemap = _tilemap_with_rooms({"r_00": (0, 0, 1, 1)})  # too small even for the 2x2 weighted vault

    ok = vaults.inject(tilemap, [{"vault_id": "tiny_vault", "weight": 5}], depth=1, rng=random.Random(1))

    assert ok is True  # weighted-only failures never fail the whole floor
    assert "vault_id" not in tilemap.rooms["r_00"]


def test_inject_places_weighted_vault_when_fill_probability_rolls_success():
    vaults.set_data_registry(_FixtureRegistry({"tiny_vault": _TINY_WEIGHTED_VAULT}))
    tilemap = _tilemap_with_rooms({"r_00": (0, 0, 4, 4)})

    class _AlwaysHitRng(random.Random):
        def random(self):
            return 0.0

        def uniform(self, a, b):
            return a

    ok = vaults.inject(
        tilemap, [{"vault_id": "tiny_vault", "weight": 1}], depth=1, rng=_AlwaysHitRng(), vault_fill_probability=1.0
    )

    assert ok is True
    assert tilemap.rooms["r_00"].get("vault_id") == "tiny_vault"


def test_inject_respects_depth_gating_on_guaranteed_vault():
    gated_vault = dict(_BIG_GUARANTEED_VAULT, min_depth=5, max_depth=10)
    vaults.set_data_registry(_FixtureRegistry({"big_vault": gated_vault}))
    tilemap = _tilemap_with_rooms({"r_00": (0, 0, 6, 6)})

    ok = vaults.inject(tilemap, [{"vault_id": "big_vault", "guaranteed": True}], depth=1, rng=random.Random(1))

    assert ok is False  # depth 1 is outside [5, 10]


def test_stamped_vault_translates_entity_and_loot_spawns_to_absolute_positions():
    vaults.set_data_registry(_FixtureRegistry({"big_vault": _BIG_GUARANTEED_VAULT}))
    tilemap = _tilemap_with_rooms({"r_00": (2, 3, 6, 6)})

    vaults.inject(tilemap, [{"vault_id": "big_vault", "guaranteed": True}], depth=1, rng=random.Random(1))

    origin_x, origin_y = tilemap.rooms["r_00"]["vault_origin"]
    entity_spawns = tilemap.rooms["r_00"]["vault_entity_spawns"]
    assert entity_spawns == [{"entity_id": "fixture_rat", "position": (origin_x + 1, origin_y + 1)}]
    loot_spawns = tilemap.rooms["r_00"]["vault_loot_spawns"]
    assert loot_spawns == [{"item_id": "fixture_potion", "position": (origin_x + 3, origin_y + 3)}]


def test_inject_with_no_vault_refs_is_a_harmless_success():
    tilemap = _tilemap_with_rooms({"r_00": (0, 0, 6, 6)})
    assert vaults.inject(tilemap, [], depth=1, rng=random.Random(1)) is True


def test_validate_vault_data_accepts_well_formed_vault():
    assert vaults.validate_vault_data(_BIG_GUARANTEED_VAULT) == []


def test_validate_vault_data_rejects_row_count_mismatch():
    bad = dict(_BIG_GUARANTEED_VAULT, height=99)
    errors = vaults.validate_vault_data(bad)
    assert errors


def test_validate_vault_data_rejects_row_width_mismatch():
    bad = dict(_BIG_GUARANTEED_VAULT, tiles=["###", "#.#", "#.#", "#.#", "#####"])
    errors = vaults.validate_vault_data(bad)
    assert errors
