"""Procedural floor generation — BSP dungeon layout, monster spawning, and
the floor-loot wiring call. See ``docs/components/06-worldgen-campaign.md``
for the full spec this implements.

**§1.1's `place_floor_loot` wiring requirement is the reason this module
exists in the shape it does.** v1's biggest documented failure was a fully
built, fully tested loot-placement function that map generation never
called. :func:`generate_floor` below calls it for real, as a real pipeline
step — see the import/fallback shim just below the module docstring.

**§1.2's `room_id` hard requirement**: every walkable tile
:func:`_generate_bsp_layout` produces — room interiors *and* corridors —
carries a non-``None`` ``room_id``. This is asserted by this module's own
unit tests and is a Definition-of-Done gate, not best-effort.

CONTRACTS.md §8 stubbing / cross-component wiring notes (several
signatures documented elsewhere in this codebase reference functions this
module must provide, or functions this module needs from systems that
either aren't merged yet or were never given a place to receive this
module's own output):

- ``01-stats-combat.md``'s ``engine/systems/combat.py`` (already merged)
  already contains a guarded ``from engine.systems.worldgen import
  current_depth`` fallback import for its depth-aware difficulty scaling
  (see that module's ``_current_depth``). This module provides
  :func:`current_depth`/:func:`set_current_depth` to satisfy that contract
  for real, plus :func:`combat.set_monster_data_lookup <engine.systems.combat.set_monster_data_lookup>`
  wiring (via :func:`_register_monster_data`) so a monster this module
  spawns has its real ``xp_value``/``loot_table`` available to
  ``CombatSystem.handle_potential_death`` on death — both are genuine
  "wired, not just built" gaps 01 left open for whoever spawns monsters
  for real, which per this component's own doc §1.1 is us.
- ``05-progression-vision.md``'s ``VisionSystem`` (already merged) exposes
  ``register_floor_rooms(floor_id, rooms)`` specifically inviting "06 (or
  whatever wires floors up in the meantime)" to call it once room data
  exists. :func:`set_vision_system`/the internal ``_push_rooms_to_vision``
  helper close that loop.
- Neither ``04-inventory-items-loot.md`` (``place_floor_loot``) nor a
  ``DataRegistry``/``EventBus`` singleton is threaded through
  ``generate_floor``'s documented ``(level_def, world, rng) -> str``
  signature. Following the same backward-compatible-optional-kwarg
  extension precedent already used twice in this codebase
  (``02-ai-system.md``'s ``AISystem.__init__`` extra kwargs,
  ``01-stats-combat.md``'s ``combat.set_data_registry`` module-level
  setter), this module adds an optional ``event_bus`` keyword to
  ``generate_floor`` and a module-level :func:`set_data_registry` — both
  no-ops-but-harmless when absent, never breaking the documented
  3-positional-arg call shape.
- ``vaults.inject(tilemap, vault_refs, depth, rng)`` (04-independent,
  02.2 of this doc) also has no ``world`` parameter, so a vault's own
  ``entity_spawns``/``loot_spawns`` (§5.2) can't be realized as real
  entities from inside ``vaults.py`` itself. ``VaultInjector`` instead
  records translated absolute-position spawn requests onto
  ``tilemap.rooms[room_id]`` (``vault_entity_spawns``/``vault_loot_spawns``)
  and *this* module (which does have ``world``) realizes them during its
  own monster-spawning/loot-placement steps. See ``vaults.py`` for the
  stamping side of this hand-off.
"""

from __future__ import annotations

import logging
import random
import weakref
from dataclasses import dataclass, field
from typing import Any

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.spatial_hash import SpatialHash
from engine.systems.ai import PositionComponent, create_ai_component
from engine.systems.stats import StatsComponent

logger = logging.getLogger(__name__)

Position = tuple[int, int]

__all__ = [
    "Tile",
    "TileMap",
    "tilemap_to_dict",
    "tilemap_from_dict",
    "generate_floor",
    "set_data_registry",
    "set_vision_system",
    "current_depth",
    "set_current_depth",
    "get_tilemap",
    "register_tilemap",
    "get_spatial_hash",
    "register_spatial_hash",
    "place_floor_loot",
    "validate_worldgen_config",
    "reset_module_state",
]


# ---------------------------------------------------------------------------
# place_floor_loot — THE wiring call (§1.1). Real function if 04 has merged,
# a logged, harmless no-op otherwise. Either way the call site in
# generate_floor below is real source, never a TODO comment.
# ---------------------------------------------------------------------------

try:
    from engine.systems.loot import place_floor_loot  # type: ignore[import-not-found]
except ImportError:
    _warned_no_loot_system = False

    def place_floor_loot(world: World, floor_id: str, depth: int) -> list[dict]:
        """Fallback stand-in for `04-inventory-items-loot.md`'s documented
        ``place_floor_loot(world, floor_id, depth) -> list[dict]`` (its §1.2),
        used only until that component merges. :func:`generate_floor` below
        calls this name unconditionally as a real pipeline step (§1.1's hard
        requirement) — once 04 merges, the ``try/except ImportError`` above
        binds the real implementation instead and this branch becomes dead
        code, with zero changes needed here."""
        global _warned_no_loot_system
        if not _warned_no_loot_system:
            logger.info(
                "place_floor_loot is a no-op: engine.systems.loot "
                "(04-inventory-items-loot.md) is not merged yet. generate_floor's "
                "real call site to place_floor_loot(world, floor_id, depth) already "
                "exists (see generate_floor in this module) -- once 04 merges this "
                "fallback stops being used, with no code change required here."
            )
            _warned_no_loot_system = True
        return []


# ---------------------------------------------------------------------------
# TileMap (§2.1) — plain data, not an ECS component (per-floor world
# geometry referenced by SpatialHash/renderer, not attached to an entity).
# ---------------------------------------------------------------------------


@dataclass
class Tile:
    walkable: bool
    room_id: str | None  # REQUIRED non-None on every walkable tile (§1.2)
    sprite_ref: str | None = None
    is_lit_room: bool = False


@dataclass
class TileMap:
    width: int
    height: int
    tiles: dict[Position, Tile] = field(default_factory=dict)
    rooms: dict[str, dict] = field(default_factory=dict)
    stairs_down: Position | None = None
    stairs_up: Position | None = None


def tilemap_to_dict(tilemap: TileMap) -> dict:
    """JSON-safe serialization for ``TileMap`` — used as the opaque
    ``tilemap`` payload foundation's ``serialize_floor_snapshot`` stores
    (that function treats it as opaque; this module is the one that
    interprets it on restore)."""
    return {
        "width": tilemap.width,
        "height": tilemap.height,
        "tiles": [
            {
                "x": x,
                "y": y,
                "walkable": tile.walkable,
                "room_id": tile.room_id,
                "sprite_ref": tile.sprite_ref,
                "is_lit_room": tile.is_lit_room,
            }
            for (x, y), tile in tilemap.tiles.items()
        ],
        "rooms": {
            room_id: {
                **room,
                "tiles": [list(pos) for pos in room.get("tiles", [])],
                "vault_entity_spawns": [
                    {**s, "position": list(s["position"])} for s in room.get("vault_entity_spawns", [])
                ],
                "vault_loot_spawns": [
                    {**s, "position": list(s["position"])} for s in room.get("vault_loot_spawns", [])
                ],
            }
            for room_id, room in tilemap.rooms.items()
        },
        "stairs_down": list(tilemap.stairs_down) if tilemap.stairs_down is not None else None,
        "stairs_up": list(tilemap.stairs_up) if tilemap.stairs_up is not None else None,
    }


def tilemap_from_dict(data: dict) -> TileMap:
    tiles: dict[Position, Tile] = {}
    for entry in data.get("tiles", []):
        tiles[(entry["x"], entry["y"])] = Tile(
            walkable=entry["walkable"],
            room_id=entry.get("room_id"),
            sprite_ref=entry.get("sprite_ref"),
            is_lit_room=entry.get("is_lit_room", False),
        )
    rooms: dict[str, dict] = {}
    for room_id, room in data.get("rooms", {}).items():
        restored = dict(room)
        if "tiles" in restored:
            restored["tiles"] = [tuple(pos) for pos in restored["tiles"]]
        if "bounds" in restored and restored["bounds"] is not None:
            restored["bounds"] = tuple(restored["bounds"])
        if "vault_origin" in restored and restored["vault_origin"] is not None:
            restored["vault_origin"] = tuple(restored["vault_origin"])
        for spawn_key in ("vault_entity_spawns", "vault_loot_spawns"):
            if spawn_key in restored:
                restored[spawn_key] = [
                    {**s, "position": tuple(s["position"])} for s in restored[spawn_key]
                ]
        rooms[room_id] = restored
    stairs_down = tuple(data["stairs_down"]) if data.get("stairs_down") is not None else None
    stairs_up = tuple(data["stairs_up"]) if data.get("stairs_up") is not None else None
    return TileMap(
        width=data["width"],
        height=data["height"],
        tiles=tiles,
        rooms=rooms,
        stairs_down=stairs_down,
        stairs_up=stairs_up,
    )


# ---------------------------------------------------------------------------
# BSP dungeon generation (§2.1) — recursive partition, one room per leaf,
# L-shaped corridors connecting siblings bottom-up (§9's committed default:
# any internally-consistent variation is fine as long as full connectivity
# and 100% room_id coverage hold).
# ---------------------------------------------------------------------------


@dataclass
class _Partition:
    x: int
    y: int
    w: int
    h: int
    left: "_Partition | None" = None
    right: "_Partition | None" = None
    room_bounds: tuple[int, int, int, int] | None = None  # x, y, w, h
    room_id: str | None = None

    @property
    def is_leaf(self) -> bool:
        return self.left is None and self.right is None

    def center(self) -> Position:
        if self.room_bounds is not None:
            rx, ry, rw, rh = self.room_bounds
            return (rx + rw // 2, ry + rh // 2)
        return (self.x + self.w // 2, self.y + self.h // 2)  # defensive, shouldn't be hit


def _build_partition_tree(
    x: int, y: int, w: int, h: int, depth: int, max_depth: int, min_room_size: int, rng: random.Random
) -> _Partition:
    node = _Partition(x, y, w, h)
    if depth >= max_depth:
        return node

    split_horizontal = w < h if w != h else rng.random() < 0.5
    if split_horizontal:
        # Cut across height -> top/bottom children.
        low, high = min_room_size, h - min_room_size
        if high <= low:
            return node  # too small to split further -- stays a leaf
        split_at = rng.randint(low, high)
        node.left = _build_partition_tree(x, y, w, split_at, depth + 1, max_depth, min_room_size, rng)
        node.right = _build_partition_tree(x, y + split_at, w, h - split_at, depth + 1, max_depth, min_room_size, rng)
    else:
        # Cut across width -> left/right children.
        low, high = min_room_size, w - min_room_size
        if high <= low:
            return node
        split_at = rng.randint(low, high)
        node.left = _build_partition_tree(x, y, split_at, h, depth + 1, max_depth, min_room_size, rng)
        node.right = _build_partition_tree(x + split_at, y, w - split_at, h, depth + 1, max_depth, min_room_size, rng)
    return node


def _carve_rooms(node: _Partition, rng: random.Random, min_room_size: int, room_margin: int, counter: list[int]) -> None:
    if not node.is_leaf:
        _carve_rooms(node.left, rng, min_room_size, room_margin, counter)
        _carve_rooms(node.right, rng, min_room_size, room_margin, counter)
        return

    max_room_w = max(min_room_size, node.w - 2 * room_margin)
    max_room_h = max(min_room_size, node.h - 2 * room_margin)
    room_w = min(rng.randint(min_room_size, max_room_w), node.w - 2 * room_margin) if max_room_w > min_room_size else min_room_size
    room_h = min(rng.randint(min_room_size, max_room_h), node.h - 2 * room_margin) if max_room_h > min_room_size else min_room_size
    room_w = max(1, min(room_w, node.w))
    room_h = max(1, min(room_h, node.h))

    max_x_offset = max(0, node.w - 2 * room_margin - room_w)
    max_y_offset = max(0, node.h - 2 * room_margin - room_h)
    offset_x = rng.randint(0, max_x_offset)
    offset_y = rng.randint(0, max_y_offset)

    room_x = min(node.x + room_margin + offset_x, node.x + node.w - room_w)
    room_y = min(node.y + room_margin + offset_y, node.y + node.h - room_h)
    room_x = max(room_x, node.x)
    room_y = max(room_y, node.y)

    node.room_id = f"r_{counter[0]:02d}"
    counter[0] += 1
    node.room_bounds = (room_x, room_y, room_w, room_h)


def _carve_l_corridor(tiles: dict[Position, Tile], a: Position, b: Position, corridor_id: str, rng: random.Random) -> None:
    ax, ay = a
    bx, by = b
    points: list[Position] = []
    if rng.random() < 0.5:
        for x in range(min(ax, bx), max(ax, bx) + 1):
            points.append((x, ay))
        for y in range(min(ay, by), max(ay, by) + 1):
            points.append((bx, y))
    else:
        for y in range(min(ay, by), max(ay, by) + 1):
            points.append((ax, y))
        for x in range(min(ax, bx), max(ax, bx) + 1):
            points.append((x, by))

    for pos in points:
        existing = tiles.get(pos)
        if existing is not None and existing.walkable:
            continue  # already a room tile -- never overwrite its room_id
        tiles[pos] = Tile(walkable=True, room_id=corridor_id)


def _connect_and_stamp(
    node: _Partition, tiles: dict[Position, Tile], rng: random.Random, corridor_prefix: str, counter: list[int]
) -> None:
    if node.is_leaf:
        rx, ry, rw, rh = node.room_bounds
        for xx in range(rx, rx + rw):
            for yy in range(ry, ry + rh):
                tiles[(xx, yy)] = Tile(walkable=True, room_id=node.room_id)
        return

    _connect_and_stamp(node.left, tiles, rng, corridor_prefix, counter)
    _connect_and_stamp(node.right, tiles, rng, corridor_prefix, counter)

    corridor_id = f"{corridor_prefix}_{counter[0]:03d}"
    counter[0] += 1
    _carve_l_corridor(tiles, node.left.center(), node.right.center(), corridor_id, rng)

    # Propagate one child's representative room upward so an ancestor can
    # connect into this whole subtree via a single point.
    node.room_bounds = node.left.room_bounds
    node.room_id = node.left.room_id


def _collect_room_bounds(node: _Partition) -> dict[str, tuple[int, int, int, int]]:
    if node.is_leaf:
        return {node.room_id: node.room_bounds}
    result = _collect_room_bounds(node.left)
    result.update(_collect_room_bounds(node.right))
    return result


def _generate_bsp_layout(
    width: int,
    height: int,
    rng: random.Random,
    min_room_size: int = 5,
    max_depth: int = 5,
    room_margin: int = 1,
    corridor_room_id_prefix: str = "corridor",
) -> TileMap:
    """Standard recursive BSP (§2.1). Every carved room AND corridor tile
    gets a non-``None`` ``room_id`` (§1.2, hard requirement) — walls (every
    tile outside a carved room/corridor) get ``room_id=None``, which is
    fine since the requirement only applies to walkable tiles."""
    root = _build_partition_tree(0, 0, width, height, 0, max_depth, min_room_size, rng)

    room_counter = [0]
    _carve_rooms(root, rng, min_room_size, room_margin, room_counter)

    room_bounds = _collect_room_bounds(root)

    tiles: dict[Position, Tile] = {}
    corridor_counter = [0]
    _connect_and_stamp(root, tiles, rng, corridor_room_id_prefix, corridor_counter)

    for x in range(width):
        for y in range(height):
            if (x, y) not in tiles:
                tiles[(x, y)] = Tile(walkable=False, room_id=None)

    rooms: dict[str, dict] = {}
    for room_id, bounds in room_bounds.items():
        rx, ry, rw, rh = bounds
        room_tiles = [(xx, yy) for xx in range(rx, rx + rw) for yy in range(ry, ry + rh)]
        rooms[room_id] = {"lit": False, "tiles": room_tiles, "bounds": bounds}

    return TileMap(width=width, height=height, tiles=tiles, rooms=rooms)


# ---------------------------------------------------------------------------
# Cross-module wiring hooks (module-level setters, mirroring
# 01-stats-combat.md's combat.set_data_registry precedent).
# ---------------------------------------------------------------------------

_data_registry: Any | None = None
_vision_system: Any | None = None
_warned_no_data_registry = False
_warned_no_monster_registry = False

_tilemaps: dict[str, TileMap] = {}
_spatial_hashes: dict[str, SpatialHash] = {}
_world_depth: "weakref.WeakKeyDictionary[World, int]" = weakref.WeakKeyDictionary()

_monster_data_by_entity: dict[int, dict] = {}
_monster_lookup_registered = False

_DEFAULT_DEPTH = 1


def set_data_registry(registry: Any | None) -> None:
    """Registers the loaded ``DataRegistry`` so ``entities``/``vaults``/
    ``configs`` lookups actually read real content. Pass ``None`` to clear
    (test teardown)."""
    global _data_registry
    _data_registry = registry
    from engine.systems import vaults

    vaults.set_data_registry(registry)


def set_vision_system(vision_system: Any | None) -> None:
    """Registers 05's ``VisionSystem`` instance so newly generated floors'
    room/lit data reaches it (``VisionSystem.register_floor_rooms`` — see
    that module's own docstring inviting this call). Pass ``None`` to clear."""
    global _vision_system
    _vision_system = vision_system


def current_depth(world: World) -> int:
    """The current floor depth for ``world`` — 01-stats-combat.md's
    ``CombatSystem`` imports this by name as its documented depth accessor
    (see its module docstring / §9 Open Questions). Defaults to 1 for a
    world that hasn't transitioned to any floor yet."""
    return _world_depth.get(world, _DEFAULT_DEPTH)


def set_current_depth(world: World, depth: int) -> None:
    _world_depth[world] = depth


def get_tilemap(floor_id: str) -> TileMap | None:
    return _tilemaps.get(floor_id)


def register_tilemap(floor_id: str, tilemap: TileMap) -> None:
    """"Register the floor with FloorManager's floor registry" (§2.1 step
    6) — this module-level per-floor-id table is that registry; ``campaign.
    FloorManager`` reads it back via this accessor when it needs the
    tilemap for a floor it's about to snapshot or has just restored."""
    _tilemaps[floor_id] = tilemap


def get_spatial_hash(floor_id: str) -> SpatialHash | None:
    return _spatial_hashes.get(floor_id)


def register_spatial_hash(floor_id: str, spatial_hash: SpatialHash) -> None:
    _spatial_hashes[floor_id] = spatial_hash


def reset_module_state() -> None:
    """Test-only convenience clearing every module-level registration this
    module accumulates — mirrors ``engine.core.events.reset()``'s role for
    the event bus. Gameplay code should never call this mid-run."""
    global _data_registry, _vision_system, _monster_lookup_registered
    global _warned_no_data_registry, _warned_no_monster_registry
    _data_registry = None
    _vision_system = None
    _tilemaps.clear()
    _spatial_hashes.clear()
    _world_depth.clear()
    _monster_data_by_entity.clear()
    _monster_lookup_registered = False
    _warned_no_data_registry = False
    _warned_no_monster_registry = False
    from engine.systems import vaults

    vaults.set_data_registry(None)
    try:
        from engine.systems import combat
    except ImportError:
        pass
    else:
        combat.set_monster_data_lookup(None)


def _emit(event_bus: EventBus | None, event_type: str, payload: dict) -> None:
    if event_bus is not None:
        event_bus.emit(event_type, payload)
        return
    from engine.core import events as default_bus

    default_bus.emit(event_type, payload)


def _get_worldgen_config() -> dict:
    global _warned_no_data_registry
    if _data_registry is not None:
        config = _data_registry.get("configs", "worldgen")
        if config is not None:
            return config
    if not _warned_no_data_registry:
        logger.info(
            "data/config/worldgen.json not wired yet (no DataRegistry registered via "
            "worldgen.set_data_registry, or it has no 'worldgen' config) -- using the "
            "built-in default that mirrors that file's documented contents."
        )
        _warned_no_data_registry = True
    return _DEFAULT_WORLDGEN_CONFIG


_DEFAULT_WORLDGEN_CONFIG = {
    "schema_version": 1,
    "bsp": {"min_room_size": 5, "max_split_depth": 5, "room_margin": 1},
    "vault_fill_probability": 0.4,
    "corridor_room_id_prefix": "corridor",
}


def validate_worldgen_config(config: dict) -> list[str]:
    """Lightweight schema check for ``data/config/worldgen.json``
    (CONTRACTS.md §9). Lives here rather than ``engine/core/schemas/``
    since that directory is 00-foundation-core.md's exclusive ownership —
    same flagged CONTRACTS.md/00 conflict noted by 02-ai-system.md's own
    module docstring."""
    errors: list[str] = []
    bsp = config.get("bsp")
    if not isinstance(bsp, dict):
        errors.append("bsp must be an object")
    else:
        for field_name in ("min_room_size", "max_split_depth", "room_margin"):
            if not isinstance(bsp.get(field_name), int):
                errors.append(f"bsp.{field_name} must be an int")
    if not isinstance(config.get("vault_fill_probability"), (int, float)):
        errors.append("vault_fill_probability must be a number")
    if not isinstance(config.get("corridor_room_id_prefix"), str):
        errors.append("corridor_room_id_prefix must be a string")
    return errors


# ---------------------------------------------------------------------------
# Monster spawning (§2.1 step 4) + combat.set_monster_data_lookup wiring
# ---------------------------------------------------------------------------


def _monster_data_lookup(entity_id: int, world: World) -> dict | None:
    return _monster_data_by_entity.get(entity_id)


def _register_monster_data(entity_id: int, monster_data: dict) -> None:
    global _monster_lookup_registered
    _monster_data_by_entity[entity_id] = monster_data
    if not _monster_lookup_registered:
        try:
            from engine.systems import combat
        except ImportError:
            pass  # 01 not merged -- absence = zero cost, nothing to wire yet
        else:
            combat.set_monster_data_lookup(_monster_data_lookup)
            _monster_lookup_registered = True


def _build_monster_stats_base(monster_data: dict) -> dict[str, float]:
    stats_block = dict(monster_data.get("stats", {}))
    combat_block = monster_data.get("combat", {})
    base: dict[str, float] = dict(stats_block)
    if "dexterity" in stats_block and "dex" not in base:
        # 01-stats-combat.md's CombatSystem.resolve_hit reads the "dex" stat
        # key; the canonical monster schema (spec §4) authors "dexterity".
        # Alias both so real monster content actually drives hit chance --
        # flagged in this component's PR as a naming mismatch for
        # CONTRACTS.md to harmonize (not this component's schema to fix).
        base["dex"] = stats_block["dexterity"]
    base.setdefault("damage_min", combat_block.get("damage_min", 0))
    base.setdefault("damage_max", combat_block.get("damage_max", 0))
    return base


def _spawn_one_monster(world: World, event_bus: EventBus | None, monster_data: dict, pos: Position) -> int:
    entity_id = world.create_entity()
    world.add_component(entity_id, PositionComponent(pos[0], pos[1]))
    world.add_component(entity_id, StatsComponent(base=_build_monster_stats_base(monster_data), modifiers={}))

    ai_component = create_ai_component(monster_data, home_position=pos)
    if ai_component is not None:
        world.add_component(entity_id, ai_component)

    _register_monster_data(entity_id, monster_data)

    _emit(event_bus, "entity_spawned", {"entity_id": entity_id, "kind": monster_data.get("id"), "position": pos})
    return entity_id


def _eligible_positions(tilemap: TileMap) -> list[Position]:
    return [pos for pos, tile in tilemap.tiles.items() if tile.walkable]


def _pick_eligible_position(candidates: list[Position], spatial_hash: SpatialHash, rng: random.Random) -> Position | None:
    shuffled = list(candidates)
    rng.shuffle(shuffled)
    for pos in shuffled:
        if not spatial_hash.query_radius(pos, 0):
            return pos
    return None  # floor fully occupied -- degrade rather than raise


def _spawn_monsters(
    level_def: dict,
    tilemap: TileMap,
    world: World,
    event_bus: EventBus | None,
    rng: random.Random,
    spatial_hash: SpatialHash,
) -> list[int]:
    global _warned_no_monster_registry
    spawned: list[int] = []

    if _data_registry is None:
        if not _warned_no_monster_registry:
            logger.info(
                "Monster spawning is a no-op: no DataRegistry registered via "
                "worldgen.set_data_registry -- entity data can't be resolved."
            )
            _warned_no_monster_registry = True
    else:
        candidates = _eligible_positions(tilemap)
        for spawn_entry in level_def.get("monster_spawns", []):
            base_id = spawn_entry["entity_id"]
            low, high = spawn_entry.get("count", [0, 0])
            count = rng.randint(low, high) if high >= low else 0
            monster_data = _data_registry.get("entities", base_id)
            if monster_data is None:
                logger.warning("monster_spawns entry %r has no resolvable entity data; skipping.", base_id)
                continue
            for _ in range(count):
                pos = _pick_eligible_position(candidates, spatial_hash, rng)
                if pos is None:
                    break  # floor is full -- degrade rather than raise
                entity_id = _spawn_one_monster(world, event_bus, monster_data, pos)
                spatial_hash.insert(entity_id, pos)
                spawned.append(entity_id)

        # Realize vault-declared monster spawns (§5.2's entity_spawns,
        # translated to absolute positions by vaults.py's stamping step).
        for room in tilemap.rooms.values():
            for spawn in room.get("vault_entity_spawns", []):
                monster_data = _data_registry.get("entities", spawn["entity_id"])
                if monster_data is None:
                    logger.warning("Vault entity_spawns entry %r has no resolvable entity data; skipping.", spawn["entity_id"])
                    continue
                pos = spawn["position"]
                entity_id = _spawn_one_monster(world, event_bus, monster_data, pos)
                spatial_hash.insert(entity_id, pos)
                spawned.append(entity_id)

    return spawned


# ---------------------------------------------------------------------------
# Stair placement (§2.1 step 3)
# ---------------------------------------------------------------------------


def _room_center_walkable(tilemap: TileMap, room_id: str) -> Position:
    room = tilemap.rooms[room_id]
    rx, ry, rw, rh = room["bounds"]
    center = (rx + rw // 2, ry + rh // 2)
    tile = tilemap.tiles.get(center)
    if tile is not None and tile.walkable:
        return center
    # A vault may have overwritten the exact center with a wall tile --
    # fall back to any walkable tile in this room's original tile list.
    for pos in room.get("tiles", []):
        candidate = tilemap.tiles.get(pos)
        if candidate is not None and candidate.walkable:
            return pos
    return center  # degenerate: no walkable tile found; return *a* position rather than raising


def _place_stairs(tilemap: TileMap, level_def: dict, rng: random.Random) -> None:
    room_ids = list(tilemap.rooms.keys())
    if not room_ids:
        return  # degenerate floor -- absence = zero cost, never raise
    rng.shuffle(room_ids)

    tilemap.stairs_down = _room_center_walkable(tilemap, room_ids[0])
    if not level_def.get("is_first_floor"):
        up_room = room_ids[1] if len(room_ids) > 1 else room_ids[0]
        tilemap.stairs_up = _room_center_walkable(tilemap, up_room)


# ---------------------------------------------------------------------------
# Loot realization from vault templates (item side of the vault hand-off;
# monster side is _spawn_monsters above). place_floor_loot itself (04's
# ambient floor loot) is called separately in generate_floor.
# ---------------------------------------------------------------------------


def _realize_vault_loot_spawns(tilemap: TileMap, floor_id: str, depth: int, world: World) -> list[dict]:
    placements: list[dict] = []
    has_any = any(room.get("vault_loot_spawns") for room in tilemap.rooms.values())
    if not has_any:
        return placements
    try:
        from engine.systems.loot import spawn_item_instance
    except ImportError:
        logger.info(
            "Vault loot_spawns present but engine.systems.loot "
            "(04-inventory-items-loot.md) is not merged yet -- skipping (absence = "
            "zero cost); place_floor_loot's own fallback above already covers the "
            "ambient-loot wiring requirement independent of this."
        )
        return placements
    for room in tilemap.rooms.values():
        for spawn in room.get("vault_loot_spawns", []):
            pos = spawn["position"]
            instance_id = spawn_item_instance(spawn["item_id"], depth, pos, world)
            placements.append({"instance_id": instance_id, "item_base_id": spawn["item_id"], "position": pos})
    return placements


# ---------------------------------------------------------------------------
# Top-level entry point (§2.1)
# ---------------------------------------------------------------------------

_MAX_VAULT_RETRIES = 20


def generate_floor(level_def: dict, world: World, rng: random.Random, *, event_bus: EventBus | None = None) -> str:
    """Top-level entry point (§2.1): given one campaign level definition
    (§5.1), produces a fully-populated floor and returns its ``floor_id``
    (stably derived from ``level_def["id"]`` — this implementation uses it
    directly, since it's already unique per campaign).

    ``event_bus`` is an optional, backward-compatible keyword addition (the
    documented signature is exactly ``(level_def, world, rng) -> str``) —
    see this module's docstring for why one is needed to emit
    ``entity_spawned`` for real against an isolated test bus; omitted, it
    falls back to the process-wide default bus.
    """
    floor_id = level_def["id"]
    depth = level_def["depth"]
    width = level_def["width"]
    height = level_def["height"]

    config = _get_worldgen_config()
    bsp_cfg = config.get("bsp", {})
    min_room_size = bsp_cfg.get("min_room_size", 5)
    max_split_depth = bsp_cfg.get("max_split_depth", 5)
    room_margin = bsp_cfg.get("room_margin", 1)
    corridor_prefix = config.get("corridor_room_id_prefix", "corridor")
    fill_probability = config.get("vault_fill_probability", 0.4)

    vault_refs = level_def.get("vaults", [])

    from engine.systems import vaults

    tilemap: TileMap | None = None
    for attempt in range(1, _MAX_VAULT_RETRIES + 1):
        tilemap = _generate_bsp_layout(
            width, height, rng,
            min_room_size=min_room_size, max_depth=max_split_depth, room_margin=room_margin,
            corridor_room_id_prefix=corridor_prefix,
        )
        if vaults.inject(tilemap, vault_refs, depth, rng, vault_fill_probability=fill_probability):
            break
        logger.info("Floor %r: vault injection failed on attempt %d/%d, regenerating layout.", floor_id, attempt, _MAX_VAULT_RETRIES)
    else:
        # CONTRACTS.md §2 rule 7 ("absence = zero cost", never block) wins
        # over an unbounded retry loop for a genuinely unsatisfiable
        # guaranteed vault -- proceed with the last generated layout rather
        # than hang the process.
        logger.warning(
            "Floor %r: could not satisfy guaranteed vault placement after %d attempts; "
            "proceeding without it rather than blocking floor generation.",
            floor_id, _MAX_VAULT_RETRIES,
        )

    _place_stairs(tilemap, level_def, rng)

    spatial_hash = SpatialHash()
    # Sentinel non-entity occupants (World entity ids start at 1) so stair
    # tiles are excluded from monster/loot placement eligibility.
    if tilemap.stairs_down is not None:
        spatial_hash.insert(-1, tilemap.stairs_down)
    if tilemap.stairs_up is not None:
        spatial_hash.insert(-2, tilemap.stairs_up)

    # Register the tilemap/spatial hash BEFORE spawning/loot placement (not
    # just at the end, §2.1 step 6's "register with FloorManager's floor
    # registry") -- 04's real place_floor_loot is documented to read
    # "eligible floor tiles via SpatialHash/tilemap query (owned by 06)",
    # so both must already be discoverable via get_tilemap/get_spatial_hash
    # by the time that call happens below.
    register_tilemap(floor_id, tilemap)
    register_spatial_hash(floor_id, spatial_hash)

    _spawn_monsters(level_def, tilemap, world, event_bus, rng, spatial_hash)

    # §1.1's mandatory wiring call -- real function once 04 merges, a
    # logged no-op until then, but the call itself always happens for real.
    loot_placements = place_floor_loot(world, floor_id, depth)
    sentinel_id = -3  # -1/-2 already used by the stair tiles above
    for entry in loot_placements:
        pos = entry.get("position")
        if pos is not None:
            spatial_hash.insert(sentinel_id, tuple(pos))
            sentinel_id -= 1
    loot_placements = list(loot_placements) + _realize_vault_loot_spawns(tilemap, floor_id, depth, world)

    if _vision_system is not None:
        rooms_payload = [
            {"room_id": room_id, "lit": room.get("lit", False), "tiles": [list(p) for p in room.get("tiles", [])]}
            for room_id, room in tilemap.rooms.items()
        ]
        _vision_system.register_floor_rooms(floor_id, rooms_payload)

    return floor_id
