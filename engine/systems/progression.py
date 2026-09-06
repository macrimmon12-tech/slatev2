"""ProgressionSystem: XP awarding, leveling, and level-up rewards.

See ``docs/components/05-progression-vision.md`` for the full spec this
implements. Key behaviors (spec §2.1):

- Awards XP on ``death``, deduplicated per dying entity so a double-fired
  ``death`` for the same kill never double-awards (spec §6 / this doc's
  Definition of Done).
- Level-up thresholds come from ``data/config/progression.json``, with a
  repeating tail once past the last explicitly-defined entry.
- A single large XP award can cross multiple level thresholds in one call
  (looped, not a single ``if``).
- Each level-up grants automatic stat gains, bonus points, and a 3-option
  random spell choice.

CONTRACTS.md §8 stubbing notes (this component is Wave 1, built in
parallel with siblings that may not be merged yet):

- ``01-stats-combat.md``'s ``StatsSystem`` isn't merged at the time this
  was written. Permanent stat gains (automatic per-level gains and spent
  bonus points) are applied via ``StatsSystem.add_modifier`` — a documented
  direct call into another system's public API (CONTRACTS.md §2 rule 4,
  same precedent as ``04`` calling ``01`` directly). Since
  ``ProgressionSystem.__init__`` is spec'd with exactly ``(world,
  event_bus)`` and there's no global ``StatsSystem`` singleton anywhere in
  CONTRACTS.md, this module accepts an *additional optional* keyword-only
  dependency, ``stats_system``, defaulting to ``None``. When it's wired
  (main.py, once ``01`` merges) modifiers apply for real; when it's absent,
  gains are tracked (xp/level/bonus-point bookkeeping all still work) but
  not applied as real stat modifiers yet, logged once, never raising, per
  CONTRACTS.md §2 rule 7 ("absence = zero cost"). This is a conservative
  default taken in the absence of an explicit constructor-injection
  convention in CONTRACTS.md — noted in this component's PR.
- Similarly, there is no global ``DataRegistry`` singleton documented
  either, so ``registry`` (for reading ``data/config/progression.json`` and
  the ``spells`` namespace for level-up choices) is also an optional
  keyword dependency, defaulting to built-in fallback values/an empty spell
  pool when absent, rather than reading JSON directly (CONTRACTS.md §2 rule
  10 reserves disk I/O to ``DataRegistry`` alone).
- ``03-spells-status.md``'s ``SpellCasterComponent`` isn't merged either.
  ``_grant_spell`` looks it up via a *guarded, runtime* import (never a
  module-level import of an unmerged module, per CONTRACTS.md §8) and
  falls back to a local pending-grants structure until ``03`` lands, exactly
  as spec'd in this doc's §2.1 ``_on_spell_chosen`` note.
- ``rng`` is an optional injectable ``random.Random`` for deterministic
  tests (spell-choice sampling) — CONTRACTS.md doesn't specify this either,
  but it's necessary for reproducible unit tests and defaults to a fresh
  ``random.Random()`` if omitted.

Escalation flag carried over from the component doc (§3 / §9): a single
``spell_chosen`` event name is consumed both by this system (level-up
reward pick) and by ``03``'s SpellSystem (in-combat casting). Per the doc's
documented default, this system only acts on ``spell_chosen`` when the
target entity has an outstanding ``spell_choice_pending`` for it (tracked
in ``_awaiting_spell_choice``), so an in-combat cast is never misread as a
level-up pick. If ``08``'s UI doc introduces a distinct event name for the
level-up pick instead, this workaround should be dropped in favor of that.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any

from engine.core.ecs import World, component
from engine.core.events import EventBus

logger = logging.getLogger(__name__)

# Built-in fallbacks used only when no DataRegistry is wired in yet
# (CONTRACTS.md §8 stubbing) -- shape matches data/config/progression.json
# exactly (see §5.1 of the component doc), just with a first threshold that
# isn't 0 (see _load_config's docstring for why).
_DEFAULT_XP_THRESHOLDS = [100, 250, 450, 700, 1000, 1400]
_DEFAULT_REPEATING_TAIL_INCREMENT = 400
_DEFAULT_STAT_GAINS_PER_LEVEL = {"max_hp": 8, "max_mp": 4, "strength": 1}
_DEFAULT_BONUS_POINTS_PER_LEVEL = 2
_DEFAULT_SPELL_CHOICE_OPTIONS_COUNT = 3


@component
@dataclass
class XpComponent:
    """Per-entity progression state. Registered in
    ``engine.core.save._COMPONENT_REGISTRY`` (see that module).

    ``awarded_death_ids`` is a set, which isn't JSON-safe as-is (a plain
    ``dataclasses.asdict`` round trip would embed a Python ``set`` in the
    serialized dict) -- this class defines its own ``to_dict``/``from_dict``
    (allowed by the ``@component`` marker whenever a class provides its
    own) so the on-disk/round-tripped shape is a plain sorted list instead.
    """

    current_xp: int = 0
    level: int = 1
    unspent_bonus_points: int = 0
    awarded_death_ids: set[int] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_xp": self.current_xp,
            "level": self.level,
            "unspent_bonus_points": self.unspent_bonus_points,
            "awarded_death_ids": sorted(self.awarded_death_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "XpComponent":
        return cls(
            current_xp=data.get("current_xp", 0),
            level=data.get("level", 1),
            unspent_bonus_points=data.get("unspent_bonus_points", 0),
            awarded_death_ids=set(data.get("awarded_death_ids", [])),
        )


def validate_progression_config(data: dict[str, Any]) -> None:
    """Lightweight structural validation for ``data/config/progression.json``.

    CONTRACTS.md §9 asks every component introducing a new JSON schema to
    ship a ``jsonschema``-or-equivalent validator under
    ``engine/core/schemas/``. ``engine/core/`` is exclusively owned by
    ``00-foundation-core`` (CONTRACTS.md §1 and that component's own File
    Ownership section), which doesn't provide a ``schemas/`` directory --
    to avoid editing a file tree owned by another component, this
    component's validator lives next to the schema it actually owns
    instead. Flagged in this component's PR as a spec inconsistency worth
    reconciling at the CONTRACTS.md level.
    """
    required: dict[str, type] = {
        "xp_thresholds": list,
        "repeating_tail_increment": int,
        "stat_gains_per_level": dict,
        "bonus_points_per_level": int,
        "spell_choice_options_count": int,
    }
    for key, expected_type in required.items():
        if key not in data:
            raise ValueError(f"progression config missing required field {key!r}")
        if not isinstance(data[key], expected_type):
            raise ValueError(
                f"progression config field {key!r} must be a {expected_type.__name__}"
            )
    if not all(isinstance(v, (int, float)) for v in data["xp_thresholds"]):
        raise ValueError("xp_thresholds must be a list of numbers")
    if not all(isinstance(v, (int, float)) for v in data["stat_gains_per_level"].values()):
        raise ValueError("stat_gains_per_level values must be numbers")


class ProgressionSystem:
    def __init__(
        self,
        world: World,
        event_bus: EventBus,
        *,
        registry: Any | None = None,
        stats_system: Any | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.world = world
        self.event_bus = event_bus
        self._registry = registry
        self._stats_system = stats_system
        self._rng = rng if rng is not None else random.Random()

        # entity_id -> awaiting a level-up spell-choice reward pick. See
        # module docstring's "escalation flag" note.
        self._awaiting_spell_choice: set[int] = set()
        # entity_id -> spell_ids granted before a real SpellCasterComponent
        # exists for it (CONTRACTS.md §8 stubbing, spec §2.1 default).
        self._pending_spell_grants: dict[int, list[str]] = {}
        # (entity_id, stat) -> next bonus-allocation index, for the
        # levelup_bonus_{stat}_{n} tag convention (01-stats-combat.md §2.1).
        self._bonus_alloc_counts: dict[tuple[int, str], int] = {}
        self._warned: set[str] = set()

        self._config = self._load_config()

        event_bus.subscribe("death", self._on_death)
        event_bus.subscribe("spell_chosen", self._on_spell_chosen)
        event_bus.subscribe("stat_allocated", self._on_stat_allocated)

    # -- config -------------------------------------------------------------

    def _load_config(self) -> dict[str, Any]:
        """Reads ``data/config/progression.json`` via the injected registry.

        Falls back to built-in defaults (never reading JSON directly --
        CONTRACTS.md §2 rule 10) if no registry was wired in, logged once.
        """
        if self._registry is not None:
            data = self._registry.get("configs", "progression")
            if data is not None:
                validate_progression_config(data)
                return data
            self._warn_once(
                "progression_config_missing",
                "data/config/progression.json not found via the DataRegistry; "
                "using built-in level-threshold defaults.",
            )
        else:
            self._warn_once(
                "no_registry",
                "ProgressionSystem constructed without a DataRegistry "
                "(CONTRACTS.md §8 stubbing); using built-in level-threshold "
                "defaults and an empty spell-choice pool until one is wired.",
            )
        return {
            "schema_version": 1,
            "xp_thresholds": list(_DEFAULT_XP_THRESHOLDS),
            "repeating_tail_increment": _DEFAULT_REPEATING_TAIL_INCREMENT,
            "stat_gains_per_level": dict(_DEFAULT_STAT_GAINS_PER_LEVEL),
            "bonus_points_per_level": _DEFAULT_BONUS_POINTS_PER_LEVEL,
            "spell_choice_options_count": _DEFAULT_SPELL_CHOICE_OPTIONS_COUNT,
        }

    def _warn_once(self, key: str, message: str) -> None:
        if key not in self._warned:
            logger.warning(message)
            self._warned.add(key)

    def _xp_required_for(self, level: int) -> int:
        """Cumulative XP required to reach ``level``. Level 1 always
        requires 0 (spec §5.1: "index 0 = XP to reach level 2, since level
        1 starts at 0 XP").

        ``xp_thresholds[i]`` is the XP required to reach level ``i + 2``.
        Once ``level`` goes past the last explicitly-defined entry, each
        further level adds ``repeating_tail_increment`` to the last defined
        threshold, forever (spec §5.1's "repeating tail").
        """
        if level <= 1:
            return 0
        thresholds: list[float] = self._config["xp_thresholds"]
        index = level - 2
        if index < len(thresholds):
            return int(thresholds[index])
        overshoot = index - (len(thresholds) - 1)
        tail_increment = self._config["repeating_tail_increment"]
        return int(thresholds[-1] + overshoot * tail_increment)

    # -- event handlers -------------------------------------------------------

    def _on_death(self, payload: dict | None) -> None:
        """payload: {entity_id, killer_id, xp_value}. Awards xp_value to
        killer_id exactly once per dying entity_id, even if death fires
        more than once for the same kill (spec §2.1 / §6)."""
        if not payload:
            return
        entity_id = payload.get("entity_id")
        killer_id = payload.get("killer_id")
        xp_value = payload.get("xp_value", 0)
        if entity_id is None or killer_id is None:
            return

        xp = self.world.get_component(killer_id, XpComponent)
        if xp is None:
            # Killer has no progression component (e.g. a monster killed
            # another monster) -- absence = zero cost, not an error.
            return
        if entity_id in xp.awarded_death_ids:
            return
        xp.awarded_death_ids.add(entity_id)
        xp.current_xp += xp_value

        self._check_level_up(killer_id, self.world, self.event_bus)

    def _check_level_up(self, entity_id: int, world: World, event_bus: EventBus) -> None:
        """Loops (not a single if) since one large XP award can cross
        multiple thresholds at once."""
        xp = world.get_component(entity_id, XpComponent)
        if xp is None:
            return
        while xp.current_xp >= self._xp_required_for(xp.level + 1):
            xp.level += 1
            self._apply_automatic_stat_gains(entity_id, xp.level)
            xp.unspent_bonus_points += self._config.get(
                "bonus_points_per_level", _DEFAULT_BONUS_POINTS_PER_LEVEL
            )
            event_bus.emit("level_up_pending", {"entity_id": entity_id})

            options = self._roll_spell_options(entity_id)
            self._awaiting_spell_choice.add(entity_id)
            event_bus.emit(
                "spell_choice_pending", {"entity_id": entity_id, "options": options}
            )
            event_bus.emit(
                "bonus_points_remaining",
                {"entity_id": entity_id, "remaining": xp.unspent_bonus_points},
            )

    def _on_spell_chosen(self, payload: dict | None) -> None:
        """payload: {entity_id, spell_id}. Only acts if this entity has an
        outstanding level-up spell_choice_pending -- see module docstring's
        escalation-flag note (spec §9 Open Questions default)."""
        if not payload:
            return
        entity_id = payload.get("entity_id")
        spell_id = payload.get("spell_id")
        if entity_id is None or spell_id is None:
            return
        if entity_id not in self._awaiting_spell_choice:
            return
        self._awaiting_spell_choice.discard(entity_id)
        self._grant_spell(entity_id, spell_id)
        self.event_bus.emit("spell_learned", {"entity_id": entity_id, "spell_id": spell_id})

    def _on_stat_allocated(self, payload: dict | None) -> None:
        """payload: {entity_id, stat, amount}. Validates the entity has
        enough unspent bonus points; if so, spends them via add_modifier
        (never a direct base-field mutation, CONTRACTS.md §2 rule 9)."""
        if not payload:
            return
        entity_id = payload.get("entity_id")
        stat = payload.get("stat")
        amount = payload.get("amount")
        if entity_id is None or stat is None or amount is None:
            return
        if amount <= 0:
            return
        xp = self.world.get_component(entity_id, XpComponent)
        if xp is None or xp.unspent_bonus_points < amount:
            return  # unknown entity or overspend -- reject silently

        xp.unspent_bonus_points -= amount
        n = self._next_bonus_alloc_index(entity_id, stat)
        self._add_modifier(
            entity_id, tag=f"levelup_bonus_{stat}_{n}", stat=stat, op="add", value=amount
        )
        self.event_bus.emit(
            "bonus_points_remaining",
            {"entity_id": entity_id, "remaining": xp.unspent_bonus_points},
        )

    # -- helpers --------------------------------------------------------------

    def _next_bonus_alloc_index(self, entity_id: int, stat: str) -> int:
        key = (entity_id, stat)
        n = self._bonus_alloc_counts.get(key, 0)
        self._bonus_alloc_counts[key] = n + 1
        return n

    def _apply_automatic_stat_gains(self, entity_id: int, level: int) -> None:
        gains: dict[str, float] = self._config.get(
            "stat_gains_per_level", _DEFAULT_STAT_GAINS_PER_LEVEL
        )
        for stat, amount in gains.items():
            self._add_modifier(
                entity_id, tag=f"levelup_auto_{stat}_{level}", stat=stat, op="add", value=amount
            )

    def _add_modifier(self, entity_id: int, tag: str, stat: str, op: str, value: float) -> None:
        if self._stats_system is not None:
            self._stats_system.add_modifier(entity_id, tag=tag, stat=stat, op=op, value=value)
            return
        self._warn_once(
            "no_stats_system",
            "ProgressionSystem constructed without a StatsSystem "
            "(01-stats-combat.md not wired yet, CONTRACTS.md §8); stat "
            "gains and bonus-point spends are tracked but not applied as "
            "real modifiers until it is wired.",
        )

    def _roll_spell_options(self, entity_id: int) -> list[str]:
        count = self._config.get(
            "spell_choice_options_count", _DEFAULT_SPELL_CHOICE_OPTIONS_COUNT
        )
        if self._registry is None:
            self._warn_once(
                "no_registry_spells",
                "ProgressionSystem constructed without a DataRegistry; "
                "level-up spell choices will always be empty until one is wired.",
            )
            return []
        all_spells = self._registry.all("spells")
        known = self._known_spell_ids(entity_id)
        # Default per spec §9 Open Questions: no eligibility filtering
        # beyond "exists in the registry and not already known" -- no
        # class/level gating exists in this codebase yet.
        eligible = [spell_id for spell_id in all_spells if spell_id not in known]
        return self._rng.sample(eligible, min(count, len(eligible)))

    def _known_spell_ids(self, entity_id: int) -> set[str]:
        known: set[str] = set(self._pending_spell_grants.get(entity_id, []))
        caster = self._get_spell_caster_component(entity_id)
        if caster is not None:
            known |= set(getattr(caster, "known_spells", None) or [])
        return known

    def _get_spell_caster_component(self, entity_id: int) -> Any | None:
        """Guarded runtime lookup of 03's SpellCasterComponent -- never a
        module-level import of an unmerged component (CONTRACTS.md §8)."""
        try:
            from engine.systems.spells import SpellCasterComponent  # type: ignore
        except ImportError:
            return None
        return self.world.get_component(entity_id, SpellCasterComponent)

    def _grant_spell(self, entity_id: int, spell_id: str) -> None:
        caster = self._get_spell_caster_component(entity_id)
        if caster is not None and hasattr(caster, "known_spells"):
            if spell_id not in caster.known_spells:
                caster.known_spells.append(spell_id)
            return
        # 03 isn't merged yet -- record locally per spec §2.1's documented
        # default, so nothing is lost once it does.
        self._pending_spell_grants.setdefault(entity_id, []).append(spell_id)
