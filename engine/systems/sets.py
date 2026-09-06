"""`SetTrackerSystem` — equipment set bonus tracking (component doc
`04-inventory-items-loot.md` §2.3).

Reacts to `equip_changed`, counts currently-equipped pieces per `set_id`
(an equipped item's base def may carry a `set_id` field, §5.1), and applies
the highest-threshold tier's `stat_modifiers` as a **full replacement
list** — never additive across tiers. See §5's set schema (this component
invents it; CONTRACTS.md/the doc don't give one beyond "tiers keyed by
`pieces_required`"):

```json
{
  "id": "ironclad_set",
  "display_name": "Ironclad",
  "tiers": [
    {"tier": 2, "pieces_required": 2, "stat_modifiers": [...]},
    {"tier": 4, "pieces_required": 4, "stat_modifiers": [...]}
  ]
}
```
"""

from __future__ import annotations

import logging
from typing import Any

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.registry import DataRegistry

from engine.systems.inventory import InventoryComponent, ItemInstanceComponent

logger = logging.getLogger(__name__)

_logged_missing_dependency: set[str] = set()


def _log_missing_once(name: str) -> None:
    if name not in _logged_missing_dependency:
        logger.info(
            "%s is not available yet (not merged / not injected) — no-op per "
            "CONTRACTS.md §2 rule 7 (absence = zero cost).",
            name,
        )
        _logged_missing_dependency.add(name)


class SetTrackerSystem:
    def __init__(
        self,
        world: World,
        event_bus: EventBus,
        registry: DataRegistry,
        stats_system: Any | None = None,
    ) -> None:
        self.world = world
        self.event_bus = event_bus
        self.registry = registry
        self._stats_system = stats_system
        # entity_id -> set_ids that had >=1 equipped piece last time we
        # recomputed — needed so dropping a set's last piece to 0 still
        # clears its modifiers (it won't appear in the *new* count at all).
        self._known_set_ids_per_entity: dict[int, set[str]] = {}
        event_bus.subscribe("equip_changed", self._on_equip_changed)

    def _resolve_stats_system(self) -> Any | None:
        if self._stats_system is not None:
            return self._stats_system
        try:
            from engine.systems.stats import StatsSystem
        except ImportError:
            _log_missing_once("StatsSystem")
            return None
        self._stats_system = StatsSystem(self.world, self.event_bus)
        return self._stats_system

    def _on_equip_changed(self, payload: dict | None) -> None:
        if not payload:
            return
        entity_id = payload.get("entity_id")
        if entity_id is None:
            return
        self._recompute(entity_id, self.world, self.event_bus)

    def _item_def_for(self, base_id: str) -> dict | None:
        if self.registry is None:
            return None
        item_def = self.registry.get("items", base_id)
        if item_def is None:
            item_def = self.registry.get("legendaries", base_id)
        return item_def

    def _find_item(self, item_instance_id: str) -> ItemInstanceComponent | None:
        for _entity_id, item in self.world.query(ItemInstanceComponent):
            if item.instance_id == item_instance_id:
                return item
        return None

    def _recompute(self, entity_id: int, world: World, event_bus: EventBus) -> None:
        inventory = world.get_component(entity_id, InventoryComponent)
        if inventory is None:
            return

        counts: dict[str, int] = {}
        for item_instance_id in inventory.equipped.values():
            if not item_instance_id:
                continue
            item = self._find_item(item_instance_id)
            if item is None:
                continue
            item_def = self._item_def_for(item.base_id) or {}
            set_id = item_def.get("set_id")
            if set_id:
                counts[set_id] = counts.get(set_id, 0) + 1

        previously_known = self._known_set_ids_per_entity.get(entity_id, set())
        relevant_set_ids = previously_known | set(counts.keys())

        stats_system = self._resolve_stats_system()

        for set_id in relevant_set_ids:
            count = counts.get(set_id, 0)
            set_def = self.registry.get("sets", set_id) if self.registry else None
            tiers = (set_def or {}).get("tiers", [])

            matched_tier: dict | None = None
            for tier in sorted(tiers, key=lambda t: t.get("pieces_required", 0)):
                if count >= tier.get("pieces_required", 0):
                    matched_tier = tier

            if stats_system is not None:
                stats_system.remove_modifiers_by_tag_prefix(entity_id, f"set_{set_id}_")
                if matched_tier is not None:
                    for i, mod in enumerate(matched_tier.get("stat_modifiers", [])):
                        stats_system.add_modifier(
                            entity_id,
                            f"set_{set_id}_{matched_tier.get('tier')}_{i}",
                            mod["stat"],
                            mod["operation"],
                            mod["value"],
                        )

            event_bus.emit(
                "set_bonus_changed",
                {
                    "entity_id": entity_id,
                    "set_id": set_id,
                    "active_tier": matched_tier.get("tier") if matched_tier else None,
                },
            )

        self._known_set_ids_per_entity[entity_id] = {sid for sid, c in counts.items() if c > 0}
