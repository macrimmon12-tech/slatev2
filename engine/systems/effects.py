"""EffectResolver — the single pipeline every spell, potion, trap, equipment
proc, and status tick resolves through (docs/components/01-stats-combat.md
§2.2).

``apply_effect``/``apply_effect_list`` dispatch on ``effect["type"]``.
Unknown types are silently skipped (logged once at DEBUG, never raises) —
this is what lets later components add new effect types without touching
this dispatcher.

Formula-bearing fields are resolved via :func:`_resolve_amount`: pure dice
notation (``"2d6"``, ``"1d8+2"``) goes through ``engine.core.formula.
roll_dice``; anything else goes through ``engine.core.formula.eval_formula``
with a context built from ``StatsSystem.get_stat`` for every identifier the
formula references, uppercased (``"0.4 - (INT * 0.03)"``). The convention
(CONTRACTS.md §6): an identifier ``FOO`` in a formula maps to
``StatsSystem.get_stat(source_id, "foo", world)`` — i.e. the lowercased
identifier is the stat name as stored in ``StatsComponent.base``.
Note: ``engine.core.formula`` has exactly two entry points (dice-only, or
arithmetic-only via ``simpleeval``) — a single field combining dice *and*
stat arithmetic in one string (as 01-stats-combat.md §5.4's illustrative
``"2d6 + (INT * 0.5)"`` example reads) isn't parseable by either as-is;
content should use one style or the other until/unless foundation's
formula.py grows a combined grammar. A malformed/unsupported formula string
raises ``ValueError`` and is allowed to propagate — a bad formula is a
content-authoring bug, not a runtime "absence," and foundation's own
``eval_formula`` already fails fast and loud by design.

Several effect types are documented call-boundaries into components that
haven't merged yet (``identify_item``/``remove_curse`` -> 04's
InventorySystem, ``apply_status`` -> 03's StatusEffectsSystem, ``script`` ->
10's Lua host). Per CONTRACTS.md §8 (stubbing) and §2 rule 7 (absence =
zero cost), each of these is a no-op logged once at INFO until the real
module exists, and the exact call this module makes is documented in the
handler's docstring so the owning session can build to match without
needing to read this code.

Position resolution (``noise``'s "falls back to target's position",
``teleport_self``, the ``position`` on ``damage_dealt``) has the same
problem one level down: no ``PositionComponent`` was owned by any
component doc when this module was first written. 02-ai-system.md has
since defined one (``engine.systems.ai.PositionComponent``, its own
documented stand-in for the same gap) -- :func:`resolve_position` now
defaults to reading that component when no explicit lookup is registered.
:func:`set_position_lookup` still exists so a later, more authoritative
source (e.g. a real ``SpatialHash``-backed lookup) can override this
default without another code change here.
"""

from __future__ import annotations

import ast
import logging
import random
import uuid
from typing import Callable

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.formula import eval_formula, roll_dice
from engine.systems.stats import StatsComponent, StatsSystem

logger = logging.getLogger(__name__)

Position = tuple[int, int]

_ALLOWED_FORMULA_FUNCTIONS = {"abs", "round", "min", "max"}

_rng = random.Random()


def set_rng(rng: random.Random) -> None:
    """Test hook: replace the module-level RNG used for dice rolls so tests
    can seed determinism. Production code has no need to call this."""
    global _rng
    _rng = rng


# ---------------------------------------------------------------------------
# DataRegistry access (15-integration-verification.md fix -- see
# _handle_apply_status below)
# ---------------------------------------------------------------------------

_data_registry = None


def set_data_registry(registry) -> None:
    """Lets whichever code wires up ``main.py`` hand this module the loaded
    ``DataRegistry``, the same "call once at boot" convention
    ``combat.set_data_registry``/``loot.set_active_registry``/
    ``worldgen.set_data_registry`` already establish elsewhere in this
    codebase. Without this, :func:`_handle_apply_status` had no way to
    give the ``StatusEffectsSystem`` it constructs a registry at all
    (CONTRACTS.md §7.1's "wired, not just built" gate, generalized: a
    real ``apply_status`` effect resolved through this module always
    silently lost every status's ``on_apply``/``on_tick``/
    ``stat_modifiers`` -- e.g. poison never actually ticked damage or
    applied its DEX debuff -- because the ad hoc ``StatusEffectsSystem``
    it built every call had no registry to look the status up in)."""
    global _data_registry
    _data_registry = registry


# ---------------------------------------------------------------------------
# Position resolution (see module docstring)
# ---------------------------------------------------------------------------

PositionLookup = Callable[[int, World], "Position | None"]
_position_lookup: PositionLookup | None = None
_warned_no_position_lookup = False


def set_position_lookup(lookup: PositionLookup | None) -> None:
    """Registers ``lookup(entity_id, world) -> (x, y) | None``, overriding
    the default (see :func:`resolve_position`). Pass ``None`` to clear
    (test teardown) and fall back to the default again."""
    global _position_lookup
    _position_lookup = lookup


def resolve_position(entity_id: int, world: World) -> Position | None:
    """Best-effort "where is this entity" lookup.

    Uses :func:`set_position_lookup`'s registered override if one exists;
    otherwise defaults to reading ``engine.systems.ai.PositionComponent``
    (02-ai-system.md's stand-in for the position-component gap neither
    00-foundation-core.md nor this component's own doc resolved -- see
    module docstring). Returns ``None`` if the entity simply has no
    position (never spawned with one) or -- logged once -- if even that
    default component isn't importable."""
    global _warned_no_position_lookup
    if _position_lookup is not None:
        return _position_lookup(entity_id, world)

    try:
        from engine.systems.ai import PositionComponent
    except ImportError:
        if not _warned_no_position_lookup:
            logger.info(
                "Position resolution not wired yet (no PositionComponent lookup "
                "registered via effects.set_position_lookup, and engine.systems.ai "
                "isn't importable either) — defaulting to None."
            )
            _warned_no_position_lookup = True
        return None

    position_component = world.get_component(entity_id, PositionComponent)
    if position_component is None:
        return None
    return (position_component.x, position_component.y)


# ---------------------------------------------------------------------------
# "Not merged yet" call-boundary helpers
# ---------------------------------------------------------------------------

_warned_missing_dependency: set[str] = set()


def _log_missing_dependency_once(key: str, message: str) -> None:
    if key not in _warned_missing_dependency:
        logger.info(message)
        _warned_missing_dependency.add(key)


# ---------------------------------------------------------------------------
# Formula / dice resolution glue
# ---------------------------------------------------------------------------


# CONTRACTS.md §6 left open whether a formula identifier's stat name is
# abbreviated ("dex") or spelled out ("dexterity"). Resolved by
# 15-integration-verification.md in favor of spelled out -- 03's
# SpellSystem and 05's ProgressionSystem both already settled there (see
# spells.py's own `_STAT_ABBREVIATIONS`/`_stat_name_for_identifier`,
# duplicated here rather than imported since CONTRACTS.md §2 rule 4 keeps
# systems from importing each other's modules for plain logic), matching
# the canonical monster/item content schema (spec §4/§5) too. A bare
# lowercase (e.g. "MAX_HP" -> "max_hp") is still the fallback for anything
# with no ability-score abbreviation entry.
_STAT_ABBREVIATIONS: dict[str, str] = {
    "INT": "intelligence",
    "STR": "strength",
    "DEX": "dexterity",
    "CON": "constitution",
    "WIS": "wisdom",
    "CHA": "charisma",
    "LUK": "luck",
}


def _stat_name_for_identifier(identifier: str) -> str:
    return _STAT_ABBREVIATIONS.get(identifier, identifier.lower())


def _extract_formula_identifiers(expr: str) -> set[str]:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return set()
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id not in _ALLOWED_FORMULA_FUNCTIONS
    }


def _build_formula_context(
    expr: str, source_id: int, world: World, stats_system: StatsSystem
) -> dict[str, float]:
    return {
        name: stats_system.get_stat(source_id, _stat_name_for_identifier(name), world)
        for name in _extract_formula_identifiers(expr)
    }


def _resolve_amount(
    value: float | int | str, source_id: int, world: World, stats_system: StatsSystem
) -> float:
    """Resolves a formula-bearing field to a number: numeric values pass
    through, pure dice notation rolls via ``roll_dice``, anything else goes
    through ``eval_formula`` with a stat-derived context."""
    if isinstance(value, bool):
        raise ValueError(f"formula value must be a number or string, got bool {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise ValueError(f"formula value must be a number or string, got {type(value).__name__}")

    try:
        return float(roll_dice(value, rng=_rng))
    except ValueError:
        pass

    context = _build_formula_context(value, source_id, world, stats_system)
    return eval_formula(value, context)


def _clamp_to_max(current: float, delta: float, max_value: float) -> float:
    new_value = current + delta
    if max_value > 0:
        new_value = min(new_value, max_value)
    return new_value


# ---------------------------------------------------------------------------
# Effect dispatch table
# ---------------------------------------------------------------------------

Handler = Callable[[dict, int, int, "World", "EventBus", "Position | None", dict], None]
_HANDLERS: dict[str, Handler] = {}


def _handler(effect_type: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        _HANDLERS[effect_type] = fn
        return fn

    return register


@_handler("damage")
def _handle_damage(effect, source_id, target_id, world, event_bus, position, context) -> None:
    stats_system = StatsSystem(world, event_bus)
    stats = world.get_component(target_id, StatsComponent)
    if stats is None:
        return

    amount = _resolve_amount(effect.get("amount", 0), source_id, world, stats_system)
    damage_type = effect.get("damage_type", "physical")

    stats.base["hp"] = stats.base.get("hp", 0.0) - amount
    context["last_damage_amount"] = amount
    context["last_damage_target_id"] = target_id

    resolved_position = position if position is not None else resolve_position(target_id, world)
    event_bus.emit(
        "damage_dealt",
        {
            "target_id": target_id,
            "amount": amount,
            "damage_type": damage_type,
            "source_id": source_id,
            "position": resolved_position,
        },
    )

    if stats.base["hp"] <= 0:
        # Death handling is CombatSystem's, regardless of what dealt the
        # killing blow (01-stats-combat.md §2.3) — imported locally to
        # avoid a module-load-time circular import (combat.py imports this
        # module at the top level to call apply_effect_list).
        from engine.systems import combat

        combat.handle_potential_death(target_id, source_id, world, event_bus)


@_handler("restore_hp")
def _handle_restore_hp(effect, source_id, target_id, world, event_bus, position, context) -> None:
    stats_system = StatsSystem(world, event_bus)
    stats = world.get_component(target_id, StatsComponent)
    if stats is None:
        return

    amount = _resolve_amount(effect.get("amount", 0), source_id, world, stats_system)
    max_hp = stats_system.get_stat(target_id, "max_hp", world)
    stats.base["hp"] = _clamp_to_max(stats.base.get("hp", 0.0), amount, max_hp)
    event_bus.emit("message", {"text": f"Restored {amount:g} HP.", "category": "heal"})


@_handler("restore_mp")
def _handle_restore_mp(effect, source_id, target_id, world, event_bus, position, context) -> None:
    stats_system = StatsSystem(world, event_bus)
    stats = world.get_component(target_id, StatsComponent)
    if stats is None:
        return

    amount = _resolve_amount(effect.get("amount", 0), source_id, world, stats_system)
    max_mp = stats_system.get_stat(target_id, "max_mp", world)
    stats.base["mp"] = _clamp_to_max(stats.base.get("mp", 0.0), amount, max_mp)


@_handler("stat_modifier")
def _handle_stat_modifier(effect, source_id, target_id, world, event_bus, position, context) -> None:
    stats_system = StatsSystem(world, event_bus)
    stat = effect["stat"]
    op = effect.get("op", "add")
    value = effect.get("value", 0)
    duration = effect.get("duration", 0)
    tag = effect.get("tag") or f"{uuid.uuid4()}:{stat}"

    if duration:
        # Steering note per 01-stats-combat.md §2.2/§9: a timed
        # stat_modifier that isn't wrapped in apply_status has nowhere to
        # register its own expiry in this component's scope (that's
        # StatusEffectsSystem's ticking, out of scope here) — content
        # authors should wrap a timed stat change in an `apply_status`
        # effect instead. We still apply it (as a permanent modifier)
        # rather than silently dropping it, and log once so the anti-
        # pattern is discoverable rather than a silent balance bug.
        _log_missing_dependency_once(
            "timed_stat_modifier",
            "stat_modifier effect used with duration>0 outside apply_status: "
            "this is a content-authoring anti-pattern (01-stats-combat.md §2.2) "
            "-- applying as a permanent modifier since this component doesn't "
            "track standalone timed-modifier expiry. Wrap it in an apply_status "
            "effect instead if it needs to expire.",
        )

    stats_system.add_modifier(target_id, tag, stat, op, value)


@_handler("noise")
def _handle_noise(effect, source_id, target_id, world, event_bus, position, context) -> None:
    radius = effect.get("radius", 0)
    resolved_position = position if position is not None else resolve_position(target_id, world)
    event_bus.emit(
        "noise_emitted",
        {"position": resolved_position, "radius": radius, "source_id": source_id},
    )


@_handler("vfx")
def _handle_vfx(effect, source_id, target_id, world, event_bus, position, context) -> None:
    resolved_position = position if position is not None else resolve_position(target_id, world)
    event_bus.emit(
        "vfx_play",
        {
            "vfx_id": effect.get("vfx_id"),
            "position": resolved_position,
            "data": effect.get("data"),
        },
    )


@_handler("script")
def _handle_script(effect, source_id, target_id, world, event_bus, position, context) -> None:
    """Documented call this makes once 10-lua-scripting-layer.md merges:
    ``engine.lua.lua_host.run_script(script_ref, source_id, target_id, world,
    event_bus) -> None``."""
    try:
        from engine.lua import lua_host
    except ImportError:
        _log_missing_dependency_once(
            "lua_host",
            "script effect type is a no-op: engine.lua.lua_host "
            "(10-lua-scripting-layer.md) not merged yet.",
        )
        return
    lua_host.run_script(effect.get("script_ref"), source_id, target_id, world, event_bus)


@_handler("identify_item")
def _handle_identify_item(effect, source_id, target_id, world, event_bus, position, context) -> None:
    """Documented call this makes once 04-inventory-items-loot.md merges:
    ``engine.systems.inventory.identify_instance(item_instance_id, world,
    event_bus) -> None``. ``item_instance_id`` may be ``None`` -- resolving
    "a sensible target" when omitted is InventorySystem's call, not ours."""
    try:
        from engine.systems import inventory
    except ImportError:
        _log_missing_dependency_once(
            "inventory_identify",
            "identify_item effect type is a no-op: engine.systems.inventory "
            "(04-inventory-items-loot.md) not merged yet.",
        )
        return
    inventory.identify_instance(effect.get("item_instance_id"), world, event_bus)


@_handler("remove_curse")
def _handle_remove_curse(effect, source_id, target_id, world, event_bus, position, context) -> None:
    """Documented call this makes once 04-inventory-items-loot.md merges:
    ``engine.systems.inventory.remove_curse(item_instance_id, world,
    event_bus) -> None``."""
    try:
        from engine.systems import inventory
    except ImportError:
        _log_missing_dependency_once(
            "inventory_remove_curse",
            "remove_curse effect type is a no-op: engine.systems.inventory "
            "(04-inventory-items-loot.md) not merged yet.",
        )
        return
    inventory.remove_curse(effect.get("item_instance_id"), world, event_bus)


@_handler("teleport_self")
def _handle_teleport_self(effect, source_id, target_id, world, event_bus, position, context) -> None:
    """Moves ``source_id`` via SpatialHash + PositionComponent per
    01-stats-combat.md §2.2. Neither a shared ``SpatialHash`` instance nor
    ``PositionComponent`` is threaded through this function's documented
    signature, and no later component has registered a position lookup yet
    (see module docstring) -- until one does, this is a documented no-op,
    logged once, rather than inventing floor-tile-picking or spatial-hash
    wiring here."""
    _log_missing_dependency_once(
        "teleport_self",
        "teleport_self effect type is a no-op: no SpatialHash/PositionComponent "
        "wiring is available to this module yet.",
    )


@_handler("apply_status")
def _handle_apply_status(effect, source_id, target_id, world, event_bus, position, context) -> None:
    """Documented call this makes once 03-spells-status.md merges. Per
    01-stats-combat.md §2.2 the entry point is
    ``StatusEffectsSystem.apply(entity_id, effect_id, duration, magnitude,
    world, event_bus) -> instance_id``; assumed constructed the same way
    every other Wave 1 system in this doc is (``StatusEffectsSystem(world,
    event_bus)``) since that's this wave's established convention
    (``StatsSystem.__init__``) and 03's doc doesn't specify otherwise.
    ``status_applied`` is emitted by StatusEffectsSystem itself -- this
    resolver does not double-emit it."""
    try:
        from engine.systems.status import StatusEffectsSystem
    except ImportError:
        _log_missing_dependency_once(
            "status_apply",
            "apply_status effect type is a no-op: engine.systems.status "
            "(03-spells-status.md) not merged yet.",
        )
        return
    effect_id = effect.get("effect_id")
    duration = effect.get("duration", 0)
    magnitude = effect.get("magnitude")
    StatusEffectsSystem(world, event_bus, registry=_data_registry).apply(
        target_id, effect_id, duration, magnitude, world, event_bus
    )


@_handler("lifesteal")
def _handle_lifesteal(effect, source_id, target_id, world, event_bus, position, context) -> None:
    percent = effect.get("percent", 0)
    last_amount = context.get("last_damage_amount")
    if last_amount is None:
        logger.debug(
            "lifesteal effect with no preceding damage effect in this "
            "apply_effect_list call; skipping (percent=%r)", percent,
        )
        return

    heal_amount = last_amount * (percent / 100.0)
    stats_system = StatsSystem(world, event_bus)
    stats = world.get_component(source_id, StatsComponent)
    if stats is None:
        return

    max_hp = stats_system.get_stat(source_id, "max_hp", world)
    stats.base["hp"] = _clamp_to_max(stats.base.get("hp", 0.0), heal_amount, max_hp)
    event_bus.emit(
        "message", {"text": f"Lifesteal restored {heal_amount:g} HP.", "category": "heal"}
    )


@_handler("reduce_armor")
def _handle_reduce_armor(effect, source_id, target_id, world, event_bus, position, context) -> None:
    """Sugar for a ``stat_modifier`` on ``armor``, ``op: "add"``, negative
    value."""
    amount = effect.get("amount", 0)
    duration = effect.get("duration", 0)
    _handle_stat_modifier(
        {"stat": "armor", "op": "add", "value": -abs(amount), "duration": duration},
        source_id, target_id, world, event_bus, position, context,
    )


@_handler("trigger_level_complete")
def _handle_trigger_level_complete(effect, source_id, target_id, world, event_bus, position, context) -> None:
    event_bus.emit("trigger_level_complete", {"entity_id": source_id})


@_handler("learn_spell")
def _handle_learn_spell(effect, source_id, target_id, world, event_bus, position, context) -> None:
    """Implementation default per 01-stats-combat.md §2.2: emit and let
    05-progression-vision.md's ProgressionSystem own the actual grant."""
    event_bus.emit("spell_learned", {"entity_id": target_id, "spell_id": effect.get("spell_id")})


_warned_unknown_types: set[str] = set()


def _dispatch(
    effect: dict,
    source_id: int,
    target_id: int,
    world: World,
    event_bus: EventBus,
    position: Position | None,
    context: dict,
) -> None:
    effect_type = effect.get("type")
    handler = _HANDLERS.get(effect_type)
    if handler is None:
        if effect_type not in _warned_unknown_types:
            logger.debug("Unknown effect type %r; skipping.", effect_type)
            _warned_unknown_types.add(effect_type)
        return
    handler(effect, source_id, target_id, world, event_bus, position, context)


def apply_effect(
    effect: dict,
    source_id: int,
    target_id: int,
    world: World,
    event_bus: EventBus,
    position: Position | None = None,
) -> None:
    """The single entry point for resolving one effect dict ad hoc, outside
    of a list (see :func:`apply_effect_list` for the list form 03/04
    actually call in practice)."""
    _dispatch(effect, source_id, target_id, world, event_bus, position, {})


def apply_effect_list(
    effects: list[dict],
    source_id: int,
    target_id: int,
    world: World,
    event_bus: EventBus,
    position: Position | None = None,
) -> None:
    """Resolves ``effects`` in list order, sharing one resolution context —
    needed for ``lifesteal`` reading the preceding ``damage`` roll."""
    context: dict = {}
    for effect in effects:
        _dispatch(effect, source_id, target_id, world, event_bus, position, context)


# ---------------------------------------------------------------------------
# Effect-dict schema validation (CONTRACTS.md §9 -- every new JSON schema
# ships a lightweight validator; this lives here rather than under
# ``engine/core/schemas/`` since ``engine/core/`` is 00-foundation-core.md's
# exclusive file ownership, not this component's to add to)
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "damage": ("amount", "damage_type"),
    "restore_hp": ("amount",),
    "restore_mp": ("amount",),
    "stat_modifier": ("stat", "op", "value"),
    "noise": ("radius",),
    "vfx": ("vfx_id",),
    "script": ("script_ref",),
    "identify_item": (),
    "remove_curse": ("item_instance_id",),
    "teleport_self": ("destination",),
    "apply_status": ("effect_id", "duration"),
    "lifesteal": ("percent",),
    "reduce_armor": ("amount", "duration"),
    "trigger_level_complete": (),
    "learn_spell": ("spell_id",),
}


def validate_effect_dict(effect: dict) -> list[str]:
    """Lightweight schema check for one effect dict (01-stats-combat.md
    §2.2/§5.4). Returns a list of human-readable error strings, empty if
    valid. An unknown ``type`` is *not* an error here -- the dispatcher
    silently skips unknown types by design (§1), so a content file
    referencing a type this build doesn't know about yet isn't a schema
    bug; only a *known* type missing its required fields is."""
    errors: list[str] = []
    if not isinstance(effect, dict):
        return [f"effect must be a JSON object, got {type(effect).__name__}"]

    effect_type = effect.get("type")
    if not isinstance(effect_type, str) or not effect_type:
        errors.append("effect.type must be a non-empty string")
        return errors

    required = _REQUIRED_FIELDS_BY_TYPE.get(effect_type)
    if required is None:
        return errors  # unknown type: not this validator's business (see above)

    for field in required:
        if field not in effect:
            errors.append(f"effect type {effect_type!r} is missing required field {field!r}")
    return errors
