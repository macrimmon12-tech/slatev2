"""``SpellSystem`` — the targeting/casting state machine every spell cast
(player or monster) goes through. See ``docs/components/03-spells-status.md``
§2.1 for the full contract this implements.

``cast()`` is the single call site both player input (07/08) and monster AI
(02) use — see the doc's §1. Actual effect execution (damage, heal,
stat_modifier, apply_status, ...) is 01's ``EffectResolver`` job; this
module only builds the targeting/gating/duration scaffolding around it.

Hard dependency on 01 (``engine/systems/stats.py``/``effects.py``), which is
not merged as of this writing. Per CONTRACTS.md §8 and this doc's §1.1, we
do not import those not-yet-merged modules at module scope. Instead every
collaborator (``get_stat``, ``apply_effect_list``, MP deduction, a
``SpatialHash``, a ``DataRegistry``) is an injectable constructor
dependency; the defaults lazily import the real 01 modules **only when
actually invoked** (see the ``_lazy_*`` methods below), so importing this
module — and running its unit tests, which always inject stand-ins per
§1.1 — never requires 01 to exist. Once 01 merges, code that constructs
``SpellSystem(world, event_bus)`` with no overrides starts exercising the
real thing automatically. The integration test
(``tests/integration/test_spell_cast_pipeline.py``) documents this
explicitly and must be re-run against the real 01 before Wave 3 sign-off.
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from engine.core.ecs import World, component
from engine.core.events import EventBus
from engine.core.formula import eval_formula
from engine.core.registry import DataRegistry
from engine.core.spatial_hash import SpatialHash

logger = logging.getLogger(__name__)

VALID_TARGETING_MODES = frozenset(
    {"self", "aoe_self", "single_enemy", "targeted_tile", "aoe_targeted"}
)
_SELF_TARGETING_MODES = frozenset({"self", "aoe_self"})
_AOE_TARGETING_MODES = frozenset({"aoe_self", "aoe_targeted"})

# "Log once" bookkeeping per CONTRACTS.md §2 rule 7 / §9 — never spam.
_logged_once: set[str] = set()


def _log_once(key: str, message: str, *args: Any) -> None:
    if key not in _logged_once:
        _logged_once.add(key)
        logger.warning(message, *args)


# Small, explicit abbreviation table for formula context keys (CONTRACTS.md
# §6: "context keys are conventionally the entity's derived stat names in
# UPPERCASE (INT, STR, DEX, ...)"). CONTRACTS.md/01 do not nail down whether
# the underlying StatsComponent.base keys are themselves abbreviated
# ("str") or spelled out ("strength") — 01's own example uses "strength"
# while its formula examples use "STR"/"DEX"/"INT". Conservative choice
# taken here (see PR notes / Open Questions): map the common ability-score
# abbreviations to their spelled-out stat names, and fall back to a plain
# lowercase of whatever identifier appears in the formula for anything
# else (covers e.g. "MAX_HP" -> "max_hp"). Any system that later resolves
# this ambiguity with a different convention should update this table.
_STAT_ABBREVIATIONS = {
    "INT": "intelligence",
    "STR": "strength",
    "DEX": "dexterity",
    "CON": "constitution",
    "WIS": "wisdom",
    "CHA": "charisma",
    "LUK": "luck",
}

# Matches formula.py's own allowed function names — excluded from context
# building since they're callables (max(...), min(...), ...), not stat
# identifiers.
_FORMULA_FUNCTION_NAMES = frozenset({"abs", "round", "min", "max"})
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _stat_name_for_identifier(identifier: str) -> str:
    return _STAT_ABBREVIATIONS.get(identifier, identifier.lower())


@component
@dataclass
class SpellCasterComponent:
    state: str = "IDLE"  # "IDLE" | "TARGETING"
    pending_spell_id: str | None = None
    known_spells: list[str] = field(default_factory=list)


GetStat = Callable[[int, str, World], float]
ApplyEffectList = Callable[..., None]
DeductMp = Callable[[int, float, World], None]


class SpellSystem:
    """See module docstring and 03-spells-status.md §2.1 for the contract."""

    def __init__(
        self,
        world: World,
        event_bus: EventBus,
        *,
        registry: DataRegistry | None = None,
        spatial_hash: SpatialHash | None = None,
        get_stat: GetStat | None = None,
        apply_effect_list: ApplyEffectList | None = None,
        deduct_mp: DeductMp | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.world = world
        self.event_bus = event_bus
        self._registry = registry
        # A SpatialHash isn't documented as an injected dependency here
        # (CONTRACTS.md doesn't say who owns "the" spatial hash instance
        # yet — no component has landed that wires one process-wide). An
        # empty one is a safe absence-of-position fallback: AoE queries
        # against it simply find no entities rather than crashing, and
        # real wiring (main.py passing in the process's actual SpatialHash
        # once one exists) overrides this via the constructor param.
        self._spatial_hash = spatial_hash if spatial_hash is not None else SpatialHash()
        self._get_stat = get_stat or self._lazy_get_stat
        self._apply_effect_list = apply_effect_list or self._lazy_apply_effect_list
        self._deduct_mp = deduct_mp or self._lazy_deduct_mp
        self._rng = rng or random.Random()
        self._stats_system: Any | None = None

    # -- 01 stand-in / lazy-import boundary (CONTRACTS.md §8) --------------

    def _get_stats_system(self) -> Any:
        if self._stats_system is None:
            from engine.systems.stats import StatsSystem  # 01 — see module docstring

            self._stats_system = StatsSystem(self.world, self.event_bus)
        return self._stats_system

    def _lazy_get_stat(self, entity_id: int, stat_name: str, world: World) -> float:
        return self._get_stats_system().get_stat(entity_id, stat_name, world)

    def _lazy_apply_effect_list(
        self,
        effects: list[dict],
        source_id: int,
        target_id: int,
        world: World,
        event_bus: EventBus,
        position: tuple[int, int] | None = None,
    ) -> None:
        from engine.systems.effects import apply_effect_list  # 01 — see module docstring

        apply_effect_list(effects, source_id, target_id, world, event_bus, position=position)

    def _lazy_deduct_mp(self, entity_id: int, amount: float, world: World) -> None:
        # "Deduct MP via a direct component write" (spec §2.1 step 1) — MP
        # is a resource pool field, not a derived stat, so this bypasses
        # StatsSystem.add_modifier by design (see the doc's own note on
        # this distinction).
        from engine.systems.stats import StatsComponent  # 01 — see module docstring

        stats = world.get_component(entity_id, StatsComponent)
        if stats is None:
            return
        stats.base["mp"] = stats.base.get("mp", 0.0) - amount

    # -- content lookup -----------------------------------------------------

    def _lookup_spell(self, spell_id: str) -> dict | None:
        if self._registry is None:
            _log_once(
                "spell_system_no_registry",
                "SpellSystem has no DataRegistry configured; every cast() will silently no-cast.",
            )
            return None
        return self._registry.get("spells", spell_id)

    # -- formula context ------------------------------------------------

    def _formula_context(self, entity_id: int, formula_expr: str) -> dict[str, float]:
        names = set(_IDENTIFIER_RE.findall(formula_expr)) - _FORMULA_FUNCTION_NAMES
        names.add("INT")  # CONTRACTS.md §6 / spec §5.1: context includes INT at minimum
        context: dict[str, float] = {}
        for name in names:
            context[name] = self._get_stat(entity_id, _stat_name_for_identifier(name), self.world)
        return context

    def _eval_formula_field(self, entity_id: int, raw: Any) -> float:
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw)
        expr = str(raw)
        return eval_formula(expr, self._formula_context(entity_id, expr))

    def _compute_mp_cost(self, entity_id: int, spell: dict) -> float:
        return self._eval_formula_field(entity_id, spell.get("mp_cost", 0))

    def _compute_fail_chance(self, entity_id: int, spell: dict) -> float:
        raw = spell.get("fail_chance_formula", 0)
        chance = self._eval_formula_field(entity_id, raw)
        return max(0.0, min(1.0, chance))

    # -- public entry points -------------------------------------------------

    def cast(self, entity_id: int, spell_id: str, target: dict | None) -> bool:
        caster = self.world.get_component(entity_id, SpellCasterComponent)
        if caster is None:
            return False
        if caster.state != "IDLE":
            return False

        spell = self._lookup_spell(spell_id)
        if spell is None:
            return False

        mp_cost = self._compute_mp_cost(entity_id, spell)
        current_mp = self._get_stat(entity_id, "mp", self.world)
        if current_mp < mp_cost:
            # Absence of resource = no effect: silent no-cast, not a fizzle.
            return False

        targeting_mode = spell["targeting_mode"]

        if targeting_mode in _SELF_TARGETING_MODES:
            self._resolve_cast(entity_id, spell_id, spell, None)
            return True

        if target is not None:
            self._resolve_cast(entity_id, spell_id, spell, target)
            return True

        caster.state = "TARGETING"
        caster.pending_spell_id = spell_id
        self.event_bus.emit(
            "spell_cast_initiated",
            {"entity_id": entity_id, "spell_id": spell_id, "targeting_mode": targeting_mode},
        )
        return True

    def confirm_target(self, entity_id: int, target: dict) -> None:
        caster = self.world.get_component(entity_id, SpellCasterComponent)
        if caster is None or caster.state != "TARGETING":
            return

        spell_id = caster.pending_spell_id
        spell = self._lookup_spell(spell_id) if spell_id is not None else None
        if spell is not None:
            self._resolve_cast(entity_id, spell_id, spell, target)

        caster.state = "IDLE"
        caster.pending_spell_id = None

    def cancel_cast(self, entity_id: int) -> None:
        caster = self.world.get_component(entity_id, SpellCasterComponent)
        if caster is None or caster.state != "TARGETING":
            return

        spell_id = caster.pending_spell_id
        caster.state = "IDLE"
        caster.pending_spell_id = None
        self.event_bus.emit(
            "spell_cast_cancelled", {"entity_id": entity_id, "spell_id": spell_id}
        )

    # -- resolution pipeline (shared by cast()'s immediate path and
    #    confirm_target(), so the two call sites never drift) -------------

    def _resolve_cast(self, entity_id: int, spell_id: str, spell: dict, target: dict | None) -> None:
        world = self.world
        event_bus = self.event_bus

        # 1. MP check, re-checked defensively (already checked in cast(),
        #    but MP may have changed between initiation and confirmation —
        #    e.g. a DoT ticked in between).
        mp_cost = self._compute_mp_cost(entity_id, spell)
        current_mp = self._get_stat(entity_id, "mp", world)
        if current_mp < mp_cost:
            return
        self._deduct_mp(entity_id, mp_cost, world)

        # 2. INT-gate: fails outright, no fail-chance roll, no backfire —
        #    MP is still spent.
        int_requirement = spell.get("int_requirement") or 0
        if int_requirement:
            current_int = self._get_stat(entity_id, "intelligence", world)
            if current_int < int_requirement:
                event_bus.emit(
                    "message",
                    {
                        "text": f"You lack the intelligence to cast "
                        f"{spell.get('display_name', spell_id)}.",
                        "category": "spell",
                    },
                )
                return

        # 3. Fail-chance roll.
        fail_chance = self._compute_fail_chance(entity_id, spell)
        fizzled = self._rng.random() < fail_chance

        targeting_mode = spell["targeting_mode"]
        position, direct_target_id = self._resolve_target(entity_id, targeting_mode, target)

        if fizzled:
            backfire_effects = spell.get("backfire_effects") or []
            if backfire_effects:
                # Backfire always targets the caster itself unless an
                # individual backfire effect explicitly targets otherwise
                # (spec §2.1 step 3) — 01's documented apply_effect_list
                # signature takes one target_id for the whole list, so a
                # per-effect override isn't representable here; that's 01's
                # call to add if it's ever needed.
                self._apply_effect_list(
                    backfire_effects, entity_id, entity_id, world, event_bus, position=position
                )
            return

        # 4. Success: resolve real effects.
        effects = spell.get("effects") or []
        if not effects:
            return

        if targeting_mode in _AOE_TARGETING_MODES:
            aoe_radius = spell.get("aoe_radius") or 0
            if position is None:
                _log_once(
                    "spell_aoe_no_position",
                    "AoE spell %s resolved with no known position for entity %s; "
                    "skipping AoE enumeration.",
                    spell_id,
                    entity_id,
                )
                targets: list[int] = []
            else:
                targets = self._spatial_hash.query_radius(position, aoe_radius)
            for aoe_target_id in targets:
                self._apply_effect_list(
                    effects, entity_id, aoe_target_id, world, event_bus, position=position
                )
        elif direct_target_id is not None:
            self._apply_effect_list(
                effects, entity_id, direct_target_id, world, event_bus, position=position
            )
        # else: single_enemy resolved no real target (see _resolve_target's
        # live-play fix below) -- MP is already spent (step 1, same as a
        # fizzle), but there is no one to apply an enemy-targeted effect
        # list to. Silently skipping is the correct "miss," not falling
        # back to hitting the caster with what was meant for an enemy.

    def _resolve_target(
        self, entity_id: int, targeting_mode: str, target: dict | None
    ) -> tuple[tuple[int, int] | None, int | None]:
        if targeting_mode in _SELF_TARGETING_MODES:
            return self._spatial_hash.position_of(entity_id), entity_id
        if targeting_mode == "single_enemy":
            # Live-play fix: this used to be a bare `target["entity_id"]`,
            # which raised a raw KeyError the instant a real player
            # clicked an empty tile with a single_enemy spell selected —
            # exactly the kind of caller-supplied-but-incomplete input
            # CONTRACTS.md §2 rule 7 says must degrade silently, not
            # crash the process. No entity under the cursor is a normal,
            # expected outcome of mouse-driven targeting, not a caller
            # bug to raise over -- and it must not fall back to treating
            # the caster as the target (that would turn "I whiffed my
            # target" into "I hit myself with my own attack spell").
            target_entity_id = (target or {}).get("entity_id")
            if target_entity_id is None:
                return None, None
            return self._spatial_hash.position_of(target_entity_id), target_entity_id
        if targeting_mode in ("targeted_tile", "aoe_targeted"):
            raw_position = (target or {}).get("position")
            position = tuple(raw_position) if raw_position is not None else None
            # No single target entity for a tile-targeted mode — effects
            # here act on position (noise, vfx, teleport_self, ...); the
            # caster itself is the closest thing to a "target_id" 01's
            # apply_effect_list signature requires. aoe_targeted overrides
            # this per-entity during AoE enumeration above.
            return position, entity_id
        raise ValueError(f"unknown targeting_mode {targeting_mode!r}")
