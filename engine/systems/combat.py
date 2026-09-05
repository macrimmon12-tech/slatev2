"""CombatSystem — melee/ranged hit resolution and death handling
(docs/components/01-stats-combat.md §2.3).

Two module-level integration points exist here that aren't part of
``resolve_hit``/``handle_potential_death``/``process_monster_turns``'s
documented (and load-bearing, fixed) signatures, because the doc gives
those functions no way to receive a ``DataRegistry`` or a monster's content
definition directly:

- :func:`set_data_registry` -- lets whichever code wires up ``main.py``
  hand this module the loaded ``DataRegistry`` so ``data/config/
  difficulty.json`` actually drives the hit-chance formula and depth
  multipliers (CONTRACTS.md §2 rule 10 forbids reading that file directly
  from here). Until it's called, a Python-side default mirroring that same
  JSON file's contents is used instead, logged once.
- :func:`set_monster_data_lookup` -- lets whichever component links a
  spawned monster entity back to its ``entities`` namespace content id
  (nothing in the merged codebase does yet -- see the function's docstring)
  register how to fetch that dict, so ``handle_potential_death`` can read
  the monster's real ``xp_value``/``loot_table``. Until it's called,
  xp_value defaults to 0 and loot entries default to ``[]``, logged once.

Both follow the same "absence = zero cost" shape as the depth-accessor
default in 01-stats-combat.md §5.3/§9.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Callable

from engine.core.ecs import World, component
from engine.core.events import EventBus
from engine.core.formula import eval_formula
from engine.systems import effects
from engine.systems.stats import StatsComponent, StatsSystem

logger = logging.getLogger(__name__)

Position = tuple[int, int]


@component
@dataclass
class PlayerTagComponent:
    """Marks the player entity.

    01-stats-combat.md §2.3 refers to "the foundation's player-id
    convention" for distinguishing player vs. monster death handling, but
    no such convention exists anywhere in the merged codebase --
    00-foundation-core.md never defines one, and no other component doc
    defines a player tag either. This is the open-question default this
    component takes (documented in its PR): a minimal, additive marker
    component, attached to whichever entity is the player. Any later
    component that spawns/owns the player entity should attach this tag;
    ``handle_potential_death`` below checks for its presence and nothing
    else.
    """


# ---------------------------------------------------------------------------
# RNG (test-determinism hook -- resolve_hit's signature is fixed by
# 01-stats-combat.md §2.3 and has no rng parameter)
# ---------------------------------------------------------------------------

_rng = random.Random()


def set_rng(rng: random.Random) -> None:
    """Test hook: replace the module-level RNG used for hit/damage rolls."""
    global _rng
    _rng = rng


# ---------------------------------------------------------------------------
# Difficulty config (data/config/difficulty.json) -- see module docstring
# ---------------------------------------------------------------------------

_DEFAULT_DIFFICULTY_CONFIG = {
    "schema_version": 1,
    "hit_chance_formula": "0.75 + (ATTACKER_DEX - DEFENDER_DEX) * 0.02",
    "hit_chance_min": 0.05,
    "hit_chance_max": 0.95,
    "depth_multipliers": [
        {"min_depth": 1, "max_depth": 5, "monster_damage_mult": 1.0, "monster_hp_mult": 1.0},
        {"min_depth": 6, "max_depth": 10, "monster_damage_mult": 1.25, "monster_hp_mult": 1.3},
        {"min_depth": 11, "max_depth": 999, "monster_damage_mult": 1.6, "monster_hp_mult": 1.75},
    ],
}

_data_registry = None
_warned_no_data_registry = False


def set_data_registry(registry) -> None:
    """Registers the loaded ``DataRegistry`` so ``data/config/
    difficulty.json`` actually drives combat math. Pass ``None`` to clear
    (test teardown)."""
    global _data_registry
    _data_registry = registry


def validate_difficulty_config(config: dict) -> list[str]:
    """Lightweight schema check for ``data/config/difficulty.json``
    (CONTRACTS.md §9 -- every new JSON schema ships a validator; this lives
    here rather than under ``engine/core/schemas/`` since ``engine/core/``
    is 00-foundation-core.md's exclusive file ownership, not this
    component's to add to). Returns a list of human-readable error strings,
    empty if valid."""
    errors: list[str] = []
    if not isinstance(config.get("hit_chance_formula"), str):
        errors.append("hit_chance_formula must be a string")
    if not isinstance(config.get("hit_chance_min"), (int, float)):
        errors.append("hit_chance_min must be a number")
    if not isinstance(config.get("hit_chance_max"), (int, float)):
        errors.append("hit_chance_max must be a number")
    depth_multipliers = config.get("depth_multipliers")
    if not isinstance(depth_multipliers, list) or not depth_multipliers:
        errors.append("depth_multipliers must be a non-empty list")
    else:
        for i, entry in enumerate(depth_multipliers):
            for field in ("min_depth", "max_depth", "monster_damage_mult", "monster_hp_mult"):
                if field not in entry or not isinstance(entry[field], (int, float)):
                    errors.append(f"depth_multipliers[{i}].{field} must be a number")
    return errors


def _get_difficulty_config() -> dict:
    global _warned_no_data_registry
    if _data_registry is not None:
        config = _data_registry.get("configs", "difficulty")
        if config is not None:
            return config
    if not _warned_no_data_registry:
        logger.info(
            "data/config/difficulty.json not wired yet (no DataRegistry registered "
            "via combat.set_data_registry, or it has no 'difficulty' config) -- "
            "using the built-in default that mirrors that file's documented contents."
        )
        _warned_no_data_registry = True
    return _DEFAULT_DIFFICULTY_CONFIG


def depth_multipliers_for(depth: int, config: dict | None = None) -> tuple[float, float]:
    """``(monster_damage_mult, monster_hp_mult)`` for ``depth``, matched by
    ``min_depth <= depth <= max_depth``. This is the lookup the v1 lesson in
    01-stats-combat.md §5.3/§6 requires actually indexing by depth rather
    than hardcoding ``[0]``. Falls back to ``(1.0, 1.0)`` if no entry
    matches (absence = zero cost)."""
    cfg = config if config is not None else _get_difficulty_config()
    for entry in cfg.get("depth_multipliers", ()):
        if entry["min_depth"] <= depth <= entry["max_depth"]:
            return entry["monster_damage_mult"], entry["monster_hp_mult"]
    return 1.0, 1.0


# ---------------------------------------------------------------------------
# Current floor depth (06-worldgen-campaign.md's FloorManager isn't merged
# yet -- see 01-stats-combat.md §9's Open Questions default)
# ---------------------------------------------------------------------------

_DEFAULT_DEPTH = 1
_warned_depth_not_wired = False


def _current_depth(world: World) -> int:
    global _warned_depth_not_wired
    try:
        from engine.systems.worldgen import current_depth as real_current_depth
    except ImportError:
        if not _warned_depth_not_wired:
            logger.info(
                "Depth-aware difficulty scaling not wired yet (engine.systems.worldgen "
                "not merged) -- defaulting to depth=%d per 01-stats-combat.md §9.",
                _DEFAULT_DEPTH,
            )
            _warned_depth_not_wired = True
        return _DEFAULT_DEPTH
    return real_current_depth(world)


# ---------------------------------------------------------------------------
# Monster data-definition lookup (xp_value / loot_table) -- see module
# docstring
# ---------------------------------------------------------------------------

MonsterDataLookup = Callable[[int, World], "dict | None"]
_monster_data_lookup: MonsterDataLookup | None = None
_warned_no_monster_lookup = False


def set_monster_data_lookup(lookup: MonsterDataLookup | None) -> None:
    """Registers ``lookup(entity_id, world) -> raw 'entities' content dict
    | None``. No component in the merged codebase yet links a spawned
    monster entity back to its content id (that's 02-ai-system.md's or
    06-worldgen-campaign.md's spawn-flow to decide) -- until one registers
    a lookup here, ``xp_value`` defaults to ``0`` and loot entries default
    to ``[]``, logged once. Pass ``None`` to clear (test teardown)."""
    global _monster_data_lookup
    _monster_data_lookup = lookup


def _monster_data_for(entity_id: int, world: World) -> dict | None:
    global _warned_no_monster_lookup
    if _monster_data_lookup is None:
        if not _warned_no_monster_lookup:
            logger.info(
                "Monster data-definition lookup not wired yet (no lookup registered "
                "via combat.set_monster_data_lookup) -- xp_value/loot_table default "
                "to 0/[] on death."
            )
            _warned_no_monster_lookup = True
        return None
    return _monster_data_lookup(entity_id, world)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _is_player(entity_id: int, world: World) -> bool:
    return world.get_component(entity_id, PlayerTagComponent) is not None


def resolve_hit(attacker_id: int, defender_id: int, world: World, event_bus: EventBus) -> bool:
    """The single melee/ranged hit-resolution entry point. Returns ``True``
    on hit, ``False`` on miss."""
    stats_system = StatsSystem(world, event_bus)
    config = _get_difficulty_config()
    depth = _current_depth(world)

    context = {
        "ATTACKER_DEX": stats_system.get_stat(attacker_id, "dex", world),
        "DEFENDER_DEX": stats_system.get_stat(defender_id, "dex", world),
        "DEPTH": float(depth),
    }
    hit_chance = eval_formula(config["hit_chance_formula"], context)
    hit_chance = max(config["hit_chance_min"], min(config["hit_chance_max"], hit_chance))

    if _rng.random() >= hit_chance:
        event_bus.emit("miss", {"attacker_id": attacker_id, "defender_id": defender_id})
        return False

    damage_min = stats_system.get_stat(attacker_id, "damage_min", world)
    damage_max = stats_system.get_stat(attacker_id, "damage_max", world)
    raw_amount = damage_min if damage_max <= damage_min else _rng.uniform(damage_min, damage_max)

    if not _is_player(attacker_id, world):
        monster_damage_mult, _monster_hp_mult = depth_multipliers_for(depth, config)
        raw_amount *= monster_damage_mult

    damage_effect = {"type": "damage", "amount": raw_amount, "damage_type": "physical"}
    defender_position = effects.resolve_position(defender_id, world)
    effects.apply_effect_list(
        [damage_effect], attacker_id, defender_id, world, event_bus, position=defender_position
    )
    return True


def handle_potential_death(target_id: int, killer_id: int, world: World, event_bus: EventBus) -> None:
    """Checks ``target_id``'s current HP; no-ops if still alive. Player ->
    ``player_died``, entity intact. Monster -> ``death`` then
    ``entity_died`` then ``loot_drop``, entity destroyed."""
    stats_system = StatsSystem(world, event_bus)
    current_hp = stats_system.get_stat(target_id, "hp", world)
    if current_hp > 0:
        return

    if _is_player(target_id, world):
        event_bus.emit("player_died", {"entity_id": target_id})
        return

    monster_data = _monster_data_for(target_id, world) or {}
    xp_value = monster_data.get("xp_value", 0)
    loot_entries = monster_data.get("loot_table", {}).get("entries", [])
    position = effects.resolve_position(target_id, world)

    event_bus.emit("death", {"entity_id": target_id, "killer_id": killer_id, "xp_value": xp_value})
    event_bus.emit("entity_died", {"entity_id": target_id, "killer_id": killer_id})
    event_bus.emit("loot_drop", {"position": position, "entries": loot_entries})
    world.destroy_entity(target_id)


def _monster_ids(world: World) -> list[int]:
    """Every StatsComponent-bearing entity that isn't the player, ascending
    by entity ID (deterministic turn order per §2.3)."""
    return sorted(
        entity_id
        for entity_id, _stats in world.query(StatsComponent)
        if not _is_player(entity_id, world)
    )


_warned_no_ai_system = False


def process_monster_turns(world: World, event_bus: EventBus) -> None:
    """Runs one AI turn for every monster, ascending entity-ID order.
    CombatSystem owns the loop and turn-order guarantee; 02-ai-system.md
    owns what each monster decides to do (including doing nothing if
    asleep -- this loop does not filter by "awake" itself). Documented call
    this makes once 02 merges: ``ai_system.take_turn(entity_id, world,
    event_bus)``."""
    global _warned_no_ai_system
    try:
        from engine.systems import ai as ai_system
    except ImportError:
        if not _warned_no_ai_system:
            logger.info(
                "process_monster_turns is a no-op: engine.systems.ai "
                "(02-ai-system.md) not merged yet."
            )
            _warned_no_ai_system = True
        return

    for entity_id in _monster_ids(world):
        ai_system.take_turn(entity_id, world, event_bus)
