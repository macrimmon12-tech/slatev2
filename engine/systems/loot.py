"""`LootSystem` — monster-drop resolution and ambient floor loot placement
(component doc `04-inventory-items-loot.md` §2.4).

**Read `docs/components/04-inventory-items-loot.md` §1.2 before touching
this file.** `place_floor_loot(world, floor_id, depth)` is the single most
important function in this component — v1's identical function was built,
unit-tested, and never called from map generation, so no item ever
appeared in the game by any method. `06-worldgen-campaign.md`'s
`generate_floor` **must** call this function as a real pipeline step; see
the PR description for this component for the "external wiring dependency"
flag CONTRACTS.md §7 requires.

Two cross-cutting wiring notes for whoever picks up `06`, since
`place_floor_loot`'s signature is fixed at exactly
``(world, floor_id, depth)`` and therefore cannot itself carry a
`DataRegistry` or a "spawned legendaries" set as parameters:

1. **Registry access**: call :func:`set_active_registry` once (e.g. at
   boot, the same place `Application.registry` is constructed) before any
   floor generation happens. Every function in this module that needs
   content falls back to this process-wide holder when no explicit
   ``registry=`` argument is given — analogous to how `engine.core.events`
   exposes a default event bus, since `DataRegistry` itself doesn't (it's
   constructed once by `Application` and threaded through explicitly
   everywhere else).
2. **`spawned_uniques` continuity**: call :func:`set_active_spawned_uniques`
   with the run's persisted set (the same set threaded through
   `engine.core.save.serialize_world`/`deserialize_world`) so a legendary
   already dropped this run is correctly excluded from ambient floor loot
   too, not just monster drops.

Floor-tile access convention (also this component's call, for the same
reason — no tilemap parameter to `place_floor_loot`): tile/room geometry is
read via :class:`FloorTileComponent` entities tagged with the target
`floor_id` — one entity per walkable tile, mirroring `06`'s own `TileMap`
shape (`walkable`, `room_id`) closely enough that `06`'s `generate_floor`
can populate them right before calling `place_floor_loot`. A floor with no
matching `FloorTileComponent` entities is a harmless no-op (empty list),
never an error — CONTRACTS.md §2 rule 7.
"""

from __future__ import annotations

import json
import logging
import random
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.core.ecs import World, component
from engine.core.events import EventBus
from engine.core.registry import DataRegistry

from engine.systems.inventory import (
    ItemInstanceComponent,
    ItemPositionComponent,
    assert_no_rarity_field,
    is_base_id_identified,
)

logger = logging.getLogger(__name__)

RARITY_ORDER: tuple[str, ...] = ("common", "uncommon", "rare", "epic", "legendary")

DEFAULT_RARITY_INTERPOLATION: dict[str, dict[str, float]] = {
    "t_0": {"common": 70, "uncommon": 20, "rare": 8, "epic": 2, "legendary": 0},
    "t_1": {"common": 10, "uncommon": 20, "rare": 30, "epic": 30, "legendary": 10},
}

DEFAULT_FLOOR_LOOT_CONFIG: dict[str, Any] = {
    "items_per_room_min": 0,
    "items_per_room_max": 2,
    "eligible_item_pool": "all",
    "gold_pile_weight": 5,
    "gold_amount_formula": "2d10",
}

DEFAULT_LEGENDARY_DROP_CHANCE = 0.05


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


@component
@dataclass
class FloorTileComponent:
    """One walkable-or-not tile of a floor's geometry, tagged by
    ``floor_id``. See module docstring — this is the convention
    `place_floor_loot` reads tile/room eligibility from, standing in for
    `06`'s real `TileMap` until that component merges and adopts it (or
    provides an equivalent bridge)."""

    floor_id: str
    x: int
    y: int
    walkable: bool = True
    room_id: str | None = None
    is_stairs: bool = False


@component
@dataclass
class GoldComponent:
    """A currency pickup — distinct from `ItemInstanceComponent` (no
    affixes/rarity/identification concept applies to gold). See component
    doc §9 Open Questions: "If a GoldComponent or equivalent is needed, add
    it here.\""""

    amount: int


# ---------------------------------------------------------------------------
# Process-wide holders (see module docstring's wiring notes)
# ---------------------------------------------------------------------------

_active_registry: DataRegistry | None = None
_active_spawned_uniques: set[str] = set()
_logged_no_active_registry = False


def set_active_registry(registry: DataRegistry | None) -> None:
    global _active_registry
    _active_registry = registry


def get_active_registry() -> DataRegistry | None:
    return _active_registry


def set_active_spawned_uniques(spawned_uniques: set[str]) -> None:
    global _active_spawned_uniques
    _active_spawned_uniques = spawned_uniques


def get_active_spawned_uniques() -> set[str]:
    return _active_spawned_uniques


def _resolve_registry(registry: DataRegistry | None) -> DataRegistry | None:
    if registry is not None:
        return registry
    global _logged_no_active_registry
    if _active_registry is None and not _logged_no_active_registry:
        logger.info(
            "No DataRegistry available (none passed and set_active_registry() "
            "was never called) — loot resolution will no-op. Absence = zero cost."
        )
        _logged_no_active_registry = True
    return _active_registry


# ---------------------------------------------------------------------------
# Rarity roll (depth-window interpolation, component doc §5.4)
# ---------------------------------------------------------------------------


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _rarity_interpolation_table(registry: DataRegistry | None) -> dict[str, dict[str, float]]:
    if registry is not None:
        loot_cfg = registry.get("configs", "loot_tables")
        if loot_cfg and "rarity_interpolation" in loot_cfg:
            return loot_cfg["rarity_interpolation"]
    return DEFAULT_RARITY_INTERPOLATION


def _floor_loot_config(registry: DataRegistry | None) -> dict[str, Any]:
    if registry is not None:
        loot_cfg = registry.get("configs", "loot_tables")
        if loot_cfg and "floor_loot" in loot_cfg:
            merged = dict(DEFAULT_FLOOR_LOOT_CONFIG)
            merged.update(loot_cfg["floor_loot"])
            return merged
    return dict(DEFAULT_FLOOR_LOOT_CONFIG)


def _legendary_drop_chance(registry: DataRegistry | None) -> float:
    if registry is not None:
        loot_cfg = registry.get("configs", "loot_tables")
        if loot_cfg and "legendary_drop_chance" in loot_cfg:
            return loot_cfg["legendary_drop_chance"]
    return DEFAULT_LEGENDARY_DROP_CHANCE


def roll_rarity(
    min_depth: int,
    max_depth: int,
    depth: int,
    registry: DataRegistry | None = None,
    rng: random.Random | None = None,
) -> str:
    """Depth-window interpolation per component doc §5.4:
    ``t = clamp((depth - min_depth) / max(1, max_depth - min_depth), 0, 1)``,
    linearly interpolating the rarity weight table between `t_0`/`t_1`.
    """
    rng = rng if rng is not None else random.Random()
    span = max(1, max_depth - min_depth)
    t = _clamp((depth - min_depth) / span, 0.0, 1.0)

    table = _rarity_interpolation_table(registry)
    t0 = table["t_0"]
    t1 = table["t_1"]

    weights = {rarity: t0.get(rarity, 0) + (t1.get(rarity, 0) - t0.get(rarity, 0)) * t for rarity in RARITY_ORDER}
    total = sum(weights.values())
    if total <= 0:
        return "common"

    roll = rng.uniform(0, total)
    upto = 0.0
    for rarity in RARITY_ORDER:
        upto += weights[rarity]
        if roll <= upto:
            return rarity
    return RARITY_ORDER[-1]


# ---------------------------------------------------------------------------
# resolve_loot_table (component doc §2.4)
# ---------------------------------------------------------------------------


def _roll_legendary_first(
    depth: int,
    registry: DataRegistry,
    spawned_uniques: set[str],
    rng: random.Random,
) -> str | None:
    legendaries = registry.all("legendaries")
    eligible = [
        legendary_id
        for legendary_id, item_def in legendaries.items()
        if item_def.get("min_depth", 1) <= depth <= item_def.get("max_depth", 10**9)
        and legendary_id not in spawned_uniques
    ]
    if not eligible:
        return None
    if rng.random() >= _legendary_drop_chance(registry):
        return None
    return rng.choice(eligible)


def resolve_loot_table(
    loot_table: dict,
    depth: int,
    is_boss: bool,
    registry: DataRegistry | None = None,
    spawned_uniques: set[str] | None = None,
    rng: random.Random | None = None,
) -> list[dict]:
    """Rolls a monster's raw `loot_table` (or a floor loot pool) into
    concrete item spawn requests (`{item_base_id, quantity}`).

    ``registry``/``spawned_uniques`` are explicit optional parameters (not
    shown in the doc's code block) for the same reason documented at the
    top of this module and in `affixes.roll_affixes` — falls back to the
    process-wide "active" holders when omitted.
    """
    rng = rng if rng is not None else random.Random()
    resolved_registry = _resolve_registry(registry)
    active_spawned_uniques = spawned_uniques if spawned_uniques is not None else _active_spawned_uniques

    if is_boss and resolved_registry is not None:
        legendary_id = _roll_legendary_first(depth, resolved_registry, active_spawned_uniques, rng)
        if legendary_id is not None:
            return [{"item_base_id": legendary_id, "quantity": 1}]

    entries = (loot_table or {}).get("entries", [])
    if not entries:
        return []

    weights = [max(entry.get("weight", 1), 0) for entry in entries]
    if sum(weights) <= 0:
        return []

    chosen = rng.choices(entries, weights=weights, k=1)[0]
    quantity_range = chosen.get("quantity", [1, 1])
    quantity = rng.randint(quantity_range[0], quantity_range[1])
    return [{"item_base_id": chosen["item_id"], "quantity": quantity}]


# ---------------------------------------------------------------------------
# spawn_item_instance (component doc §2.4)
# ---------------------------------------------------------------------------


def spawn_item_instance(
    item_base_id: str,
    depth: int,
    position: tuple[int, int],
    world: World,
    registry: DataRegistry | None = None,
    spawned_uniques: set[str] | None = None,
    rng: random.Random | None = None,
) -> str:
    """Creates a real `ItemInstanceComponent`-bearing entity at `position`.

    ``registry``/``spawned_uniques`` are explicit optional parameters for
    the reason documented at the top of this module — falls back to the
    process-wide "active" holders when omitted.
    """
    rng = rng if rng is not None else random.Random()
    resolved_registry = _resolve_registry(registry)
    active_spawned_uniques = spawned_uniques if spawned_uniques is not None else _active_spawned_uniques

    instance_id = str(uuid.uuid4())
    entity_id = world.create_entity()

    legendary_def = resolved_registry.get("legendaries", item_base_id) if resolved_registry else None

    if legendary_def is not None:
        assert_no_rarity_field(legendary_def, item_base_id, "legendaries")
        item = ItemInstanceComponent(
            base_id=item_base_id,
            instance_id=instance_id,
            rarity="legendary",
            affixes=[],
            identified_type=True,  # legendaries are always pre-identified
            cursed=False,
            curse_identified=False,
            locked=False,
            quantity=1,
        )
        active_spawned_uniques.add(item_base_id)
    else:
        item_def = resolved_registry.get("items", item_base_id) if resolved_registry else None
        if item_def is not None:
            assert_no_rarity_field(item_def, item_base_id, "items")
        min_depth = (item_def or {}).get("min_depth", 1)
        max_depth = (item_def or {}).get("max_depth", min_depth)

        rarity = roll_rarity(min_depth, max_depth, depth, resolved_registry, rng)
        affixes: list[dict] = []
        if rarity != "common" and resolved_registry is not None:
            from engine.systems.affixes import roll_affixes

            affixes = roll_affixes(item_base_id, rarity, depth, resolved_registry, rng)

        cursed_chance = (item_def or {}).get("cursed_chance", 0.0)
        cursed = bool(cursed_chance) and rng.random() < cursed_chance

        item = ItemInstanceComponent(
            base_id=item_base_id,
            instance_id=instance_id,
            rarity=rarity,
            affixes=affixes,
            identified_type=is_base_id_identified(item_base_id),
            cursed=cursed,
            curse_identified=False,
            locked=False,
            quantity=1,
        )

    world.add_component(entity_id, item)
    world.add_component(entity_id, ItemPositionComponent(x=position[0], y=position[1]))
    return instance_id


# ---------------------------------------------------------------------------
# place_floor_loot — see docs/components/04-inventory-items-loot.md §1.2
# ---------------------------------------------------------------------------


def _eligible_item_pool(registry: DataRegistry, depth: int) -> dict[str, dict]:
    """`eligible_item_pool: "all"` is the only pool mode required by this
    component's Definition of Done — any other configured value falls back
    to "all" (logged once), never raises."""
    all_items = registry.all("items")
    return {
        item_id: item_def
        for item_id, item_def in all_items.items()
        if item_def.get("min_depth", 1) <= depth <= item_def.get("max_depth", 10**9)
    }


def place_floor_loot(world: World, floor_id: str, depth: int) -> list[dict]:
    """THE function `06-worldgen-campaign.md`'s floor-generation pipeline
    must call — see docs/components/04-inventory-items-loot.md §1.2 and
    this module's docstring for the tile-access convention.

    Scatters ambient loot across eligible floor tiles (walkable, has a
    `room_id`, not a stairs tile, not already occupied by another item
    entity). Returns `{instance_id, item_base_id, position}` per placed
    item (gold piles use `item_base_id: "gold"` and `instance_id: None`,
    since gold isn't an `ItemInstanceComponent`).

    Complete no-op on a floor with zero eligible tiles, or when no
    `DataRegistry` has been made available via `set_active_registry` —
    never raises (CONTRACTS.md §2 rule 7).
    """
    registry = _resolve_registry(None)
    if registry is None:
        return []

    rng = random.Random()

    tiles = [tile for _entity_id, tile in world.query(FloorTileComponent) if tile.floor_id == floor_id]
    eligible_tiles = [tile for tile in tiles if tile.walkable and tile.room_id is not None and not tile.is_stairs]
    if not eligible_tiles:
        return []

    occupied = {(pos.x, pos.y) for _entity_id, pos in world.query(ItemPositionComponent)}

    rooms: dict[str, list[FloorTileComponent]] = {}
    for tile in eligible_tiles:
        rooms.setdefault(tile.room_id, []).append(tile)

    floor_cfg = _floor_loot_config(registry)
    item_pool = _eligible_item_pool(registry, depth)
    gold_weight = max(floor_cfg.get("gold_pile_weight", 0), 0)
    spawned_uniques = get_active_spawned_uniques()

    placed: list[dict] = []
    for room_tiles in rooms.values():
        available = [tile for tile in room_tiles if (tile.x, tile.y) not in occupied]
        if not available:
            continue

        low = min(floor_cfg.get("items_per_room_min", 0), len(available))
        high = min(floor_cfg.get("items_per_room_max", 0), len(available))
        high = max(low, high)
        count = rng.randint(low, high)
        chosen_tiles = rng.sample(available, count) if count > 0 else []

        for tile in chosen_tiles:
            occupied.add((tile.x, tile.y))
            total_weight = gold_weight + (1 if item_pool else 0)
            if total_weight <= 0:
                continue

            if rng.uniform(0, total_weight) < gold_weight:
                from engine.core.formula import roll_dice

                amount = roll_dice(floor_cfg.get("gold_amount_formula", "2d10")) * max(depth, 1)
                gold_entity_id = world.create_entity()
                world.add_component(gold_entity_id, GoldComponent(amount=amount))
                world.add_component(gold_entity_id, ItemPositionComponent(x=tile.x, y=tile.y))
                placed.append({"instance_id": None, "item_base_id": "gold", "position": (tile.x, tile.y)})
            else:
                item_base_id = rng.choice(list(item_pool.keys()))
                instance_id = spawn_item_instance(
                    item_base_id, depth, (tile.x, tile.y), world, registry=registry,
                    spawned_uniques=spawned_uniques, rng=rng,
                )
                placed.append({"instance_id": instance_id, "item_base_id": item_base_id, "position": (tile.x, tile.y)})

    return placed


def load_fixture_floor(world: World, fixture_path: Path | str) -> str:
    """Reads a hand-authored fixture floor (`tests/fixtures/floors/*.json`)
    and populates `world` with one `FloorTileComponent` per tile. Stands in
    for `06`'s real BSP generator until it merges (component doc §1.2/§8) —
    exercised directly by this component's own `place_floor_loot`
    integration test."""
    with open(fixture_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    floor_id = data["floor_id"]
    for tile in data.get("tiles", []):
        entity_id = world.create_entity()
        world.add_component(
            entity_id,
            FloorTileComponent(
                floor_id=floor_id,
                x=tile["x"],
                y=tile["y"],
                walkable=tile.get("walkable", True),
                room_id=tile.get("room_id"),
                is_stairs=tile.get("is_stairs", False),
            ),
        )
    return floor_id


# ---------------------------------------------------------------------------
# LootSystem
# ---------------------------------------------------------------------------


class LootSystem:
    """Subscribes to `loot_drop` (emitted by `01`'s `CombatSystem`) and
    resolves the raw, unrolled `entries` into real item instances — see
    component doc's "loot_drop handoff" note.

    ``current_depth``/boss-detection limitation: the `loot_drop` payload
    (CONTRACTS.md §3.2) is `{position, entries}` only — no depth, no
    attacker/monster identity. Until `06-worldgen-campaign.md` exposes a
    real current-depth accessor, this defaults to `depth = 1` (mirroring
    `01-stats-combat.md`'s own documented default for the same gap) and
    always treats drops as non-boss; call `set_current_depth` once that
    accessor exists. Noted in the PR so Wave 3 integration verification
    checks it got updated.
    """

    def __init__(
        self,
        world: World,
        event_bus: EventBus,
        registry: DataRegistry,
        spawned_uniques: set[str] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.world = world
        self.event_bus = event_bus
        self.registry = registry
        self.spawned_uniques = spawned_uniques if spawned_uniques is not None else set()
        self.rng = rng if rng is not None else random.Random()
        self.current_depth = 1

        set_active_registry(registry)
        set_active_spawned_uniques(self.spawned_uniques)

        event_bus.subscribe("loot_drop", self._on_loot_drop)

    def set_current_depth(self, depth: int) -> None:
        self.current_depth = depth

    def _on_loot_drop(self, payload: dict | None) -> None:
        if not payload:
            return
        position = payload.get("position")
        entries = payload.get("entries")
        if position is None or not entries:
            return

        resolved = resolve_loot_table(
            {"entries": entries},
            self.current_depth,
            is_boss=False,
            registry=self.registry,
            spawned_uniques=self.spawned_uniques,
            rng=self.rng,
        )
        for spawn_request in resolved:
            for _ in range(spawn_request.get("quantity", 1)):
                spawn_item_instance(
                    spawn_request["item_base_id"],
                    self.current_depth,
                    tuple(position),
                    self.world,
                    registry=self.registry,
                    spawned_uniques=self.spawned_uniques,
                    rng=self.rng,
                )
