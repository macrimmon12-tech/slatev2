"""``StatusEffectsSystem`` — duration tracking and per-tick effect
application for buffs/debuffs/DoTs. See
``docs/components/03-spells-status.md`` §2.2 for the full contract.

Same hard-dependency-on-01 situation as ``engine/systems/spells.py`` (see
that module's docstring) — ``apply_effect_list``/``StatsSystem.add_modifier``/
``StatsSystem.remove_modifiers_by_tag_prefix`` are injectable collaborators
with lazily-imported defaults, per CONTRACTS.md §8 and this doc's §1.1.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from engine.core.ecs import World, component
from engine.core.events import EventBus
from engine.core.registry import DataRegistry

logger = logging.getLogger(__name__)

_logged_once: set[str] = set()


def _log_once(key: str, message: str, *args: Any) -> None:
    if key not in _logged_once:
        _logged_once.add(key)
        logger.warning(message, *args)


@dataclass
class StatusInstance:
    effect_id: str
    instance_id: str
    remaining_duration: int
    magnitude: float | None


@component
@dataclass
class StatusEffectsComponent:
    instances: dict[str, StatusInstance] = field(default_factory=dict)

    # `to_dict` is left to @component's default (dataclasses.asdict), which
    # already recurses correctly into the nested StatusInstance dataclass
    # values. Only `from_dict` needs a custom override: `cls(**data)` would
    # otherwise leave `instances` as plain dicts instead of reconstructing
    # StatusInstance objects.
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StatusEffectsComponent":
        # Custom from_dict: @component's default (`cls(**data)`) would
        # leave `instances` as plain dicts rather than StatusInstance
        # objects, since dataclasses.asdict/cls(**data) doesn't know how to
        # reconstruct a nested dataclass value type on the way back in.
        instances = {
            iid: StatusInstance(**inst_data)
            for iid, inst_data in data.get("instances", {}).items()
        }
        return cls(instances=instances)


ApplyEffectList = Callable[..., None]
AddModifier = Callable[[int, str, str, str, float], None]
RemoveModifiersByTagPrefix = Callable[[int, str], int]
IdFactory = Callable[[], str]


class StatusEffectsSystem:
    """See module docstring and 03-spells-status.md §2.2 for the contract."""

    def __init__(
        self,
        world: World,
        event_bus: EventBus,
        *,
        registry: DataRegistry | None = None,
        apply_effect_list: ApplyEffectList | None = None,
        add_modifier: AddModifier | None = None,
        remove_modifiers_by_tag_prefix: RemoveModifiersByTagPrefix | None = None,
        id_factory: IdFactory | None = None,
    ) -> None:
        self.world = world
        self.event_bus = event_bus
        self._registry = registry
        self._apply_effect_list = apply_effect_list or self._lazy_apply_effect_list
        self._add_modifier = add_modifier or self._lazy_add_modifier
        self._remove_modifiers_by_tag_prefix = (
            remove_modifiers_by_tag_prefix or self._lazy_remove_modifiers_by_tag_prefix
        )
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._stats_system: Any | None = None

    # -- 01 stand-in / lazy-import boundary (CONTRACTS.md §8) --------------

    def _get_stats_system(self) -> Any:
        if self._stats_system is None:
            from engine.systems.stats import StatsSystem  # 01 — see module docstring

            self._stats_system = StatsSystem(self.world, self.event_bus)
        return self._stats_system

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

    def _lazy_add_modifier(self, entity_id: int, tag: str, stat: str, op: str, value: float) -> None:
        self._get_stats_system().add_modifier(entity_id, tag, stat, op, value)

    def _lazy_remove_modifiers_by_tag_prefix(self, entity_id: int, tag_prefix: str) -> int:
        return self._get_stats_system().remove_modifiers_by_tag_prefix(entity_id, tag_prefix)

    # -- content lookup -----------------------------------------------------

    def _lookup_status(self, effect_id: str) -> dict | None:
        if self._registry is None:
            _log_once(
                "status_system_no_registry",
                "StatusEffectsSystem has no DataRegistry configured; "
                "applied instances will carry no on_apply/on_tick/stat_modifiers.",
            )
            return None
        return self._registry.get("effects", effect_id)

    # -- public entry points -------------------------------------------------

    def apply(
        self,
        entity_id: int,
        effect_id: str,
        duration: int,
        magnitude: float | None,
        world: World,
        event_bus: EventBus,
    ) -> str:
        comp = world.get_component(entity_id, StatusEffectsComponent)
        if comp is None:
            comp = StatusEffectsComponent()
            world.add_component(entity_id, comp)

        instance_id = self._id_factory()
        # Unconditional per-instance stacking (spec §3, §6): a brand-new
        # independent instance is always created, even if effect_id
        # matches an already-active instance on this entity.
        comp.instances[instance_id] = StatusInstance(
            effect_id=effect_id,
            instance_id=instance_id,
            remaining_duration=duration,
            magnitude=magnitude,
        )

        status_def = self._lookup_status(effect_id)
        if status_def is not None:
            on_apply = status_def.get("on_apply") or []
            if on_apply:
                self._apply_effect_list(on_apply, entity_id, entity_id, world, event_bus)
            for modifier in status_def.get("stat_modifiers") or []:
                stat = modifier["stat"]
                self._add_modifier(
                    entity_id,
                    f"{instance_id}:{stat}",
                    stat,
                    modifier["operation"],
                    modifier["value"],
                )
        else:
            _log_once(
                f"status_undefined_{effect_id}",
                "Status effect %r not found in the effects registry; instance "
                "applied with no on_apply/on_tick/stat_modifiers.",
                effect_id,
            )

        event_bus.emit(
            "status_applied",
            {
                "entity_id": entity_id,
                "effect_id": effect_id,
                "instance_id": instance_id,
                "duration": duration,
            },
        )
        return instance_id

    def tick(self, world: World, event_bus: EventBus) -> None:
        for entity_id, comp in world.query(StatusEffectsComponent):
            expired: list[StatusInstance] = []
            for instance_id, instance in list(comp.instances.items()):
                instance.remaining_duration -= 1

                status_def = self._lookup_status(instance.effect_id)
                if status_def is not None:
                    on_tick = status_def.get("on_tick") or []
                    if on_tick:
                        self._apply_effect_list(on_tick, entity_id, entity_id, world, event_bus)

                if instance.remaining_duration <= 0:
                    del comp.instances[instance_id]
                    expired.append(instance)

            for instance in expired:
                self._remove_modifiers_by_tag_prefix(entity_id, f"{instance.instance_id}:")
                event_bus.emit(
                    "status_expired",
                    {
                        "entity_id": entity_id,
                        "effect_id": instance.effect_id,
                        "instance_id": instance.instance_id,
                    },
                )
