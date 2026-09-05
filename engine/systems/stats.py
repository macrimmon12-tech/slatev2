"""StatsSystem — the single read path for every derived stat in the
codebase (docs/components/01-stats-combat.md §2.1).

``StatsComponent.base`` fields are never mutated to reflect buffs, debuffs,
or equipment — only :meth:`StatsSystem.get_stat`'s *return value* reflects
those. Callers must not read ``StatsComponent.base[...]`` directly expecting
a "current" value for a buffable stat; they must call ``get_stat``.

One deliberate, documented exception: ``"hp"`` and ``"mp"`` entries in
``base`` represent an entity's literal current life/mana total, not a
buffable derived attribute — they are mutated directly by
``EffectResolver``'s damage/restore_hp/restore_mp/lifesteal handling
(``engine/systems/effects.py``), the same way a health bar's current value
changes every hit. This is not a violation of the "base is never mutated"
rule — that rule is about not bolting buff/debuff math onto a stat field
instead of using a modifier; current HP/MP isn't a buffable stat, it's
state. Max HP/MP (``"max_hp"``/``"max_mp"``) *are* ordinary buffable stats
and go through modifiers as usual.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from engine.core.ecs import World, component
from engine.core.events import EventBus

logger = logging.getLogger(__name__)

_VALID_OPS = {"add", "multiply"}
_warned_invalid_ops: set[str] = set()


@component
@dataclass
class StatsComponent:
    base: dict[str, float]
    modifiers: dict[str, tuple[str, str, float]]
    # modifiers key = instance-id tag (str), value = (stat_name, op, value).
    # op in {"add", "multiply"}. See 01-stats-combat.md §2.1's tag-convention
    # table — other components remove modifiers by exact tag or tag-prefix
    # depending on which convention produced them.


class StatsSystem:
    """The single read path for "what is this entity's current X"."""

    def __init__(self, world: World, event_bus: EventBus) -> None:
        self._world = world
        self._event_bus = event_bus

    def get_stat(self, entity_id: int, stat_name: str, world: World) -> float:
        """``result = (base + sum(add modifiers)) * product(multiply
        modifiers)`` — "Option C" math. Missing ``StatsComponent`` or a
        missing ``stat_name`` in ``base`` both resolve to ``0.0`` rather
        than raising (absence = zero cost, CONTRACTS.md §2 rule 7); order
        of modifier insertion never matters since both sum and product are
        commutative.
        """
        stats = world.get_component(entity_id, StatsComponent)
        if stats is None:
            return 0.0

        base_value = stats.base.get(stat_name, 0.0)
        add_total = 0.0
        multiply_total = 1.0
        for _tag, (mod_stat, op, value) in stats.modifiers.items():
            if mod_stat != stat_name:
                continue
            if op == "add":
                add_total += value
            elif op == "multiply":
                multiply_total *= value
            # Any other op string is rejected at add_modifier() time and
            # never lands in `modifiers`, so no branch is needed here.

        return (base_value + add_total) * multiply_total

    def add_modifier(
        self, entity_id: int, tag: str, stat: str, op: str, value: float
    ) -> None:
        """``op`` is ``"add"`` or ``"multiply"``. Emits
        ``stat_modifier_applied``. Any other ``op`` string is rejected and
        logged once at DEBUG rather than silently treated as ``"add"`` (per
        01-stats-combat.md §5.2) — the modifier is not stored.
        """
        if op not in _VALID_OPS:
            if op not in _warned_invalid_ops:
                logger.debug(
                    "add_modifier: ignoring invalid op %r for entity=%s tag=%r "
                    "stat=%r (must be 'add' or 'multiply')",
                    op, entity_id, tag, stat,
                )
                _warned_invalid_ops.add(op)
            return

        stats = self._world.get_component(entity_id, StatsComponent)
        if stats is None:
            # Absence = zero cost: an entity with no StatsComponent simply
            # can't carry modifiers.
            return

        stats.modifiers[tag] = (stat, op, value)
        self._event_bus.emit(
            "stat_modifier_applied",
            {"entity_id": entity_id, "tag": tag, "stat": stat, "op": op, "value": value},
        )

    def remove_modifiers_by_tag_prefix(self, entity_id: int, tag_prefix: str) -> int:
        """Removes every modifier whose tag starts with ``tag_prefix``.
        Returns the count removed. This is how equipment unequip, buff
        expiry, and set-bonus recompute all clear their modifiers."""
        stats = self._world.get_component(entity_id, StatsComponent)
        if stats is None:
            return 0

        to_remove = [tag for tag in stats.modifiers if tag.startswith(tag_prefix)]
        for tag in to_remove:
            del stats.modifiers[tag]
        return len(to_remove)
