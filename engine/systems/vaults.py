"""VaultInjector — stamps hand-authored vault templates into procgen floors.

See ``docs/components/06-worldgen-campaign.md`` §2.2/§5.2 for the full
spec. Guaranteed vaults are placed first, each requiring an eligible room
(never causing a retry itself — :func:`inject` just reports failure via its
boolean return so ``worldgen.generate_floor`` can regenerate the whole
floor and try again, per §2.2). Weighted vaults then best-effort-fill
remaining eligible rooms and never cause a retry on failure to place.

``inject``'s documented signature (§2.2) is exactly
``inject(tilemap, vault_refs, depth, rng) -> bool`` — no ``world``
parameter, so a vault's own ``entity_spawns``/``loot_spawns`` (§5.2) can't
be realized as real entities from in here. Instead, a successfully stamped
vault records its translated *absolute-position* spawn requests onto
``tilemap.rooms[room_id]`` (``vault_entity_spawns``/``vault_loot_spawns``)
so ``worldgen.py`` (which does have ``world``) can realize them during its
own monster-spawning/loot-placement steps — see that module's
``_spawn_monsters``/``_realize_vault_loot_spawns``. This hand-off is a
documented resolution of a genuine gap in §2.2's signature (it never
threads ``world`` through), not a deviation from it.

``vault_fill_probability`` (§5.4) is likewise not part of the documented
positional signature; it's added as a backward-compatible optional keyword
default, the same extension precedent already used twice elsewhere in this
codebase (``02-ai-system.md``'s ``AISystem.__init__`` extra kwargs,
``01-stats-combat.md``'s module-level ``set_data_registry`` setters) —
``worldgen.generate_floor`` reads the real config value and passes it
explicitly; the bare 4-positional-arg call shape from the doc still works
unchanged.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from engine.systems.worldgen import Tile, TileMap

logger = logging.getLogger(__name__)

__all__ = ["inject", "set_data_registry", "validate_vault_data"]

_data_registry: Any | None = None
_warned_no_registry = False
_warned_invalid_vault: set[str] = set()

_DEFAULT_VAULT_FILL_PROBABILITY = 0.4


def set_data_registry(registry: Any | None) -> None:
    """Registers the loaded ``DataRegistry`` so ``vaults`` namespace lookups
    resolve real vault template data. Pass ``None`` to clear (test
    teardown)."""
    global _data_registry
    _data_registry = registry


def validate_vault_data(vault_data: dict) -> list[str]:
    """Lightweight schema check for a vault template (§5.2). Lives here
    rather than ``engine/core/schemas/`` since that directory is
    00-foundation-core.md's exclusive ownership — same flagged
    CONTRACTS.md/00 conflict 02-ai-system.md's own module docstring notes."""
    errors: list[str] = []
    width = vault_data.get("width")
    height = vault_data.get("height")
    tiles = vault_data.get("tiles")
    if not isinstance(width, int) or not isinstance(height, int):
        errors.append("width/height must be ints")
        return errors
    if not isinstance(tiles, list) or len(tiles) != height:
        errors.append(f"tiles must have exactly {height} rows (got {len(tiles) if isinstance(tiles, list) else 'non-list'})")
        return errors
    for row_index, row in enumerate(tiles):
        if not isinstance(row, str) or len(row) != width:
            errors.append(f"tiles[{row_index}] must be a {width}-character string")
    return errors


def _get_vault_data(vault_id: str) -> dict | None:
    global _warned_no_registry
    if _data_registry is None:
        if not _warned_no_registry:
            logger.info(
                "VaultInjector has no DataRegistry wired (call vaults.set_data_registry) -- "
                "vault injection is a no-op until one is provided."
            )
            _warned_no_registry = True
        return None
    vault_data = _data_registry.get("vaults", vault_id)
    if vault_data is None:
        return None
    errors = validate_vault_data(vault_data)
    if errors:
        if vault_id not in _warned_invalid_vault:
            logger.warning("Vault %r fails schema validation (%s); treating as unavailable.", vault_id, "; ".join(errors))
            _warned_invalid_vault.add(vault_id)
        return None
    return vault_data


def _depth_eligible(vault_data: dict, depth: int) -> bool:
    min_depth = vault_data.get("min_depth")
    max_depth = vault_data.get("max_depth")
    if min_depth is not None and depth < min_depth:
        return False
    if max_depth is not None and depth > max_depth:
        return False
    return True


def _room_fits(room: dict, vault_data: dict) -> bool:
    bounds = room.get("bounds")
    if bounds is None:
        return False
    _rx, _ry, rw, rh = bounds
    return rw >= vault_data["width"] and rh >= vault_data["height"]


def _find_eligible_room(tilemap: TileMap, vault_data: dict, depth: int, used_room_ids: set[str]) -> str | None:
    if not _depth_eligible(vault_data, depth):
        return None
    for room_id, room in tilemap.rooms.items():
        if room_id in used_room_ids:
            continue
        if _room_fits(room, vault_data):
            return room_id
    return None


def _stamp_vault(tilemap: TileMap, room_id: str, vault_data: dict, vault_id: str) -> None:
    rx, ry, rw, rh = tilemap.rooms[room_id]["bounds"]
    vw, vh = vault_data["width"], vault_data["height"]
    origin_x = rx + max(0, (rw - vw) // 2)
    origin_y = ry + max(0, (rh - vh) // 2)

    for row_index, row in enumerate(vault_data["tiles"]):
        for col_index, char in enumerate(row):
            pos = (origin_x + col_index, origin_y + row_index)
            # Vault tiles inherit the target room's room_id, never their own
            # (a template has no room_id of its own -- the same template can
            # be stamped into different rooms across different floors).
            tilemap.tiles[pos] = Tile(walkable=(char != "#"), room_id=room_id)

    entity_spawns = [
        {"entity_id": spawn["entity_id"], "position": (origin_x + spawn["x"], origin_y + spawn["y"])}
        for spawn in vault_data.get("entity_spawns", [])
    ]
    loot_spawns = [
        {"item_id": spawn["item_id"], "position": (origin_x + spawn["x"], origin_y + spawn["y"])}
        for spawn in vault_data.get("loot_spawns", [])
    ]
    room = tilemap.rooms[room_id]
    room["vault_id"] = vault_id
    room["vault_origin"] = (origin_x, origin_y)
    room["vault_entity_spawns"] = entity_spawns
    room["vault_loot_spawns"] = loot_spawns


def inject(
    tilemap: TileMap,
    vault_refs: list[dict],
    depth: int,
    rng: random.Random,
    vault_fill_probability: float = _DEFAULT_VAULT_FILL_PROBABILITY,
) -> bool:
    """See module docstring / §2.2. Returns ``False`` iff a guaranteed
    vault has no eligible room (signaling the caller to regenerate the
    whole floor and retry) — weighted vaults are always best-effort."""
    guaranteed = [ref for ref in vault_refs if ref.get("guaranteed")]
    weighted = [ref for ref in vault_refs if not ref.get("guaranteed")]

    used_room_ids: set[str] = set()

    for ref in guaranteed:
        vault_data = _get_vault_data(ref["vault_id"])
        if vault_data is None:
            return False
        room_id = _find_eligible_room(tilemap, vault_data, depth, used_room_ids)
        if room_id is None:
            return False
        _stamp_vault(tilemap, room_id, vault_data, ref["vault_id"])
        used_room_ids.add(room_id)

    if weighted:
        for room_id in list(tilemap.rooms.keys()):
            if room_id in used_room_ids:
                continue
            if rng.random() >= vault_fill_probability:
                continue

            candidates: list[tuple[dict, dict]] = []
            for ref in weighted:
                vault_data = _get_vault_data(ref["vault_id"])
                if vault_data is None:
                    continue
                if not _depth_eligible(vault_data, depth):
                    continue
                if not _room_fits(tilemap.rooms[room_id], vault_data):
                    continue
                candidates.append((ref, vault_data))
            if not candidates:
                continue

            total_weight = sum(ref.get("weight", 1) for ref, _ in candidates)
            roll = rng.uniform(0, total_weight)
            running_total = 0.0
            chosen = candidates[-1]
            for ref, vault_data in candidates:
                running_total += ref.get("weight", 1)
                if roll <= running_total:
                    chosen = (ref, vault_data)
                    break

            chosen_ref, chosen_vault_data = chosen
            _stamp_vault(tilemap, room_id, chosen_vault_data, chosen_ref["vault_id"])
            used_room_ids.add(room_id)

    return True
