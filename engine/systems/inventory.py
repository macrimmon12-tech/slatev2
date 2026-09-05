"""`InventorySystem` — pickup/drop/use/equip/unequip/identify
(component doc `04-inventory-items-loot.md` §2.1).

Two component types own item state:

- ``ItemInstanceComponent``: attached to every item entity (an item is a
  real entity — see the doc's §9 Open Questions default, adopted here so
  floor pickups/inventory items/equipped items are represented uniformly).
- ``InventoryComponent``: attached to any entity that can carry items
  (references item entities by their ``instance_id`` string, not their
  integer entity id, matching every event payload in CONTRACTS.md §3.2).

Cross-component dependency note (CONTRACTS.md §8 stubbing): ``equip``/
``unequip``/``use`` call into ``01-stats-combat.md``'s ``StatsSystem`` /
``EffectResolver``, which are not merged yet at the time this component was
built. Rather than importing a module that doesn't exist, every system in
this file accepts its collaborators via optional constructor injection
(``stats_system``, ``effect_resolver``) — tests inject fakes/doubles
exercising the documented call boundary (CONTRACTS.md §8: "mock the
function boundary... instead of importing the real implementation"), and
production callers may inject the real instances once ``01`` lands. If
nothing is injected, a lazy import is attempted on first use so this code
"just works" without edits once ``01`` merges; if the import still fails
(not merged / not on the path), the call is a logged-once no-op — absence
of a dependency is zero cost (CONTRACTS.md §2 rule 7), never a crash.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from engine.core.ecs import World, component
from engine.core.events import EventBus
from engine.core.registry import DataRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


@component
@dataclass
class ItemInstanceComponent:
    base_id: str  # e.g. "long_sword" — registry key into `items`/`legendaries`
    instance_id: str  # unique per rolled instance
    rarity: str  # rolled at spawn, "common".."legendary" — NEVER read from the item def
    affixes: list[dict] = field(default_factory=list)  # rolled affix entries, see §5.2
    identified_type: bool = False
    cursed: bool = False
    curse_identified: bool = False
    locked: bool = False  # true once a cursed item is equipped+revealed
    quantity: int = 1


@component
@dataclass
class InventoryComponent:
    item_instance_ids: list[str] = field(default_factory=list)
    equipped: dict[str, str | None] = field(default_factory=dict)  # slot -> instance_id


@component
@dataclass
class ItemPositionComponent:
    """Position of an item entity while it sits, unowned, on the current
    floor. Present only on item entities that are on the floor (removed on
    pickup, (re)added on drop / ambient placement).

    A distinct component rather than reusing a generic "PositionComponent"
    on purpose: no such component is defined anywhere in the merged
    codebase yet (CONTRACTS.md/spatial_hash.py both describe one as
    "defined by a later component" without naming which). Introducing our
    own narrowly-scoped component avoids guessing at, and possibly
    colliding with, whatever shape a future component picks for general
    entity positioning — see the PR description for this note.

    No ``floor_id`` field: per `06-worldgen-campaign.md`'s
    `FloorManager.transition_to`, only the *current* floor's entities are
    ever live in `world` at once (other floors are serialized out to
    snapshots) — so a bare (x, y) is unambiguous.
    """

    x: int
    y: int


_logged_missing_dependency: set[str] = set()


def _log_missing_once(name: str) -> None:
    if name not in _logged_missing_dependency:
        logger.info(
            "%s is not available yet (not merged / not injected) — no-op per "
            "CONTRACTS.md §2 rule 7 (absence = zero cost).",
            name,
        )
        _logged_missing_dependency.add(name)


def assert_no_rarity_field(item_def: dict, item_id: str, namespace: str) -> None:
    """Rarity is never stored on an item definition (CONTRACTS.md §10) —
    it's rolled at spawn time. Raises loudly if content violates this, per
    component doc §5.1/§6."""
    if "rarity" in item_def:
        raise ValueError(
            f"Content error: {namespace}/{item_id} carries a 'rarity' field. "
            "Rarity is rolled at spawn time and must never appear on an item "
            "definition (CONTRACTS.md §10)."
        )


# ---------------------------------------------------------------------------
# Global "identify_type" state
# ---------------------------------------------------------------------------
#
# "Identify one potion, know them all" is a run-global fact about a base
# item id, not a per-instance flag (component doc §2.1) — module-level so
# that `loot.spawn_item_instance` (a different module in this same
# component) can consult it when a *new* instance of an already-identified
# base id is spawned later ("future drops" per the doc), without needing a
# live InventorySystem reference threaded through the loot pipeline.

_identified_base_ids: set[str] = set()


def mark_base_id_identified(item_base_id: str) -> None:
    _identified_base_ids.add(item_base_id)


def is_base_id_identified(item_base_id: str) -> bool:
    return item_base_id in _identified_base_ids


def reset_identified_base_ids() -> None:
    """Test-only convenience — production code never needs to un-identify
    a base type mid-run."""
    _identified_base_ids.clear()


class InventorySystem:
    def __init__(
        self,
        world: World,
        event_bus: EventBus,
        registry: DataRegistry,
        stats_system: Any | None = None,
        effect_resolver: Callable[..., None] | None = None,
    ) -> None:
        self.world = world
        self.event_bus = event_bus
        self.registry = registry
        self._stats_system = stats_system
        self._effect_resolver = effect_resolver
        event_bus.subscribe("identify_type_request", self._on_identify_type_request)

    # -- event handlers -----------------------------------------------------

    def _on_identify_type_request(self, payload: dict | None) -> None:
        if not payload:
            return
        item_base_id = payload.get("item_base_id")
        if item_base_id:
            self.identify_type(item_base_id)

    # -- lookups --------------------------------------------------------------

    def _find_item(self, item_instance_id: str) -> tuple[int | None, ItemInstanceComponent | None]:
        for entity_id, item in self.world.query(ItemInstanceComponent):
            if item.instance_id == item_instance_id:
                return entity_id, item
        return None, None

    def _item_definition(self, base_id: str) -> dict | None:
        if self.registry is None:
            return None
        item_def = self.registry.get("items", base_id)
        namespace = "items"
        if item_def is None:
            item_def = self.registry.get("legendaries", base_id)
            namespace = "legendaries"
        if item_def is not None:
            assert_no_rarity_field(item_def, base_id, namespace)
        return item_def

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

    def _resolve_effect_resolver(self) -> Callable[..., None] | None:
        if self._effect_resolver is not None:
            return self._effect_resolver
        try:
            from engine.systems.effects import apply_effect_list
        except ImportError:
            _log_missing_once("EffectResolver.apply_effect_list")
            return None
        self._effect_resolver = apply_effect_list
        return self._effect_resolver

    # -- public API -----------------------------------------------------------

    def pickup(self, entity_id: int, item_instance_id: str) -> bool:
        item_entity_id, item = self._find_item(item_instance_id)
        if item_entity_id is None:
            return False
        inventory = self.world.get_component(entity_id, InventoryComponent)
        if inventory is None:
            return False
        if item_instance_id in inventory.item_instance_ids:
            return False

        self.world.remove_component(item_entity_id, ItemPositionComponent)
        inventory.item_instance_ids.append(item_instance_id)
        self.event_bus.emit(
            "item_pickup", {"entity_id": entity_id, "item_instance_id": item_instance_id}
        )
        return True

    def drop(self, entity_id: int, item_instance_id: str, position: tuple[int, int]) -> bool:
        inventory = self.world.get_component(entity_id, InventoryComponent)
        if inventory is None or item_instance_id not in inventory.item_instance_ids:
            return False
        if item_instance_id in inventory.equipped.values():
            return False  # must unequip first
        item_entity_id, _item = self._find_item(item_instance_id)
        if item_entity_id is None:
            return False

        inventory.item_instance_ids.remove(item_instance_id)
        self.world.add_component(item_entity_id, ItemPositionComponent(x=position[0], y=position[1]))
        self.event_bus.emit(
            "item_dropped",
            {"entity_id": entity_id, "item_instance_id": item_instance_id, "position": tuple(position)},
        )
        return True

    def use(self, entity_id: int, item_instance_id: str) -> bool:
        inventory = self.world.get_component(entity_id, InventoryComponent)
        if inventory is None or item_instance_id not in inventory.item_instance_ids:
            return False
        item_entity_id, item = self._find_item(item_instance_id)
        if item_entity_id is None or item is None:
            return False
        item_def = self._item_definition(item.base_id)
        if item_def is None:
            return False

        effects = item_def.get("effects") or []
        if effects:
            resolver = self._resolve_effect_resolver()
            if resolver is not None:
                resolver(effects, entity_id, entity_id, self.world, self.event_bus)

        self.event_bus.emit("item_used", {"entity_id": entity_id, "item_instance_id": item_instance_id})

        if item_def.get("consumable"):
            if item.quantity > 1:
                item.quantity -= 1
            else:
                inventory.item_instance_ids.remove(item_instance_id)
                self.world.destroy_entity(item_entity_id)
        return True

    def equip(self, entity_id: int, item_instance_id: str, slot: str) -> bool:
        inventory = self.world.get_component(entity_id, InventoryComponent)
        if inventory is None or item_instance_id not in inventory.item_instance_ids:
            return False
        item_entity_id, item = self._find_item(item_instance_id)
        if item_entity_id is None or item is None:
            return False

        currently_equipped = inventory.equipped.get(slot)
        if currently_equipped and currently_equipped != item_instance_id:
            # Swap: clear whatever's already in this slot first. Refused
            # (same as a bare unequip) if that item is cursed+locked — you
            # can't equip over a locked item any more than you can remove it
            # directly.
            if not self.unequip(entity_id, slot):
                return False

        item_def = self._item_definition(item.base_id) or {}

        modifiers: list[dict] = list(item_def.get("stat_modifiers", []))
        for affix in item.affixes:
            modifiers.extend(affix.get("stat_modifiers", []))

        stats_system = self._resolve_stats_system()
        if stats_system is not None:
            for i, mod in enumerate(modifiers):
                stats_system.add_modifier(
                    entity_id, f"item_{item_instance_id}_{i}", mod["stat"], mod["operation"], mod["value"]
                )

        inventory.equipped[slot] = item_instance_id

        if item.cursed:
            self.identify_instance(item_instance_id)
            item.locked = True

        self.event_bus.emit(
            "equip_changed", {"entity_id": entity_id, "slot": slot, "item_instance_id": item_instance_id}
        )
        return True

    def unequip(self, entity_id: int, slot: str) -> bool:
        inventory = self.world.get_component(entity_id, InventoryComponent)
        if inventory is None:
            return False
        item_instance_id = inventory.equipped.get(slot)
        if not item_instance_id:
            return False
        _item_entity_id, item = self._find_item(item_instance_id)
        if item is not None and item.locked:
            return False  # cursed + revealed — refused, no state change

        stats_system = self._resolve_stats_system()
        if stats_system is not None:
            stats_system.remove_modifiers_by_tag_prefix(entity_id, f"item_{item_instance_id}_")

        inventory.equipped[slot] = None
        self.event_bus.emit("equip_changed", {"entity_id": entity_id, "slot": slot, "item_instance_id": None})
        return True

    def identify_type(self, item_base_id: str) -> None:
        mark_base_id_identified(item_base_id)
        for _entity_id, item in self.world.query(ItemInstanceComponent):
            if item.base_id == item_base_id:
                item.identified_type = True

    def identify_instance(self, item_instance_id: str) -> None:
        _entity_id, item = self._find_item(item_instance_id)
        if item is None:
            return
        item.curse_identified = True

    def is_type_identified(self, item_base_id: str) -> bool:
        return is_base_id_identified(item_base_id)
