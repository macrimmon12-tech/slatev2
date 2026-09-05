"""Save system: full-world serialization, floor snapshots, and the shared
component registry every save-aware component dataclass must be listed in.

CONTRACTS.md §2 rule 3 / §5: every ``@component`` dataclass gameplay ever
attaches to an entity must be registered in ``_COMPONENT_REGISTRY`` by
name, or it is silently dropped on save/load. This dict is shared and
additive-only across every component that introduces a new component
type — expect merge churn here, keep entries alphabetical by component
name, and never remove another component's entry.

This module also owns the mechanism behind the component-registry lint
check (CONTRACTS.md §2 rule 3, this component's Definition of Done):
:func:`discover_component_modules` + :func:`find_unregistered_components`
walk the real package tree for ``@component``-marked classes and report
any that aren't in the registry. See ``tests/unit/test_component_registry.py``.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from types import ModuleType
from typing import Any, Iterable

from engine.core.ecs import World, is_component
from engine.systems.spells import SpellCasterComponent
from engine.systems.status import StatusEffectsComponent

# Component classes from other components, imported here solely to populate
# _COMPONENT_REGISTRY below. Alphabetical by component name, one import per
# line, so each new component's PR adds exactly one line here and one entry
# to the dict — the same low-conflict shape as the registry itself. This
# file is the one documented, shared exception to "don't import another
# component's module": every component's own module stays the source of
# truth for its component classes, this dict just points at them so
# save/load can find them by name.
#
# PlayerTagComponent/PositionComponent were independently introduced by
# 02-ai-system.md (engine/systems/ai.py) to fill the same "no player-id
# convention, no PositionComponent owner" gap 01-stats-combat.md also hit —
# ai.py's versions are the ones registered here since 02 merged first;
# engine/systems/combat.py and effects.py import them from ai.py rather
# than keeping a second, incompatible definition around.
from engine.systems.ai import AIComponent, PlayerTagComponent, PositionComponent
from engine.systems.progression import XpComponent
from engine.systems.stats import StatsComponent

logger = logging.getLogger(__name__)

# Each Wave 1+ component adds its own entries here in the same PR that
# introduces the dataclass (CONTRACTS.md §2 rule 3).
_COMPONENT_REGISTRY: dict[str, type] = {
    "AIComponent": AIComponent,
    "PlayerTagComponent": PlayerTagComponent,
    "PositionComponent": PositionComponent,
    "SpellCasterComponent": SpellCasterComponent,
    "StatsComponent": StatsComponent,
    "StatusEffectsComponent": StatusEffectsComponent,
    "XpComponent": XpComponent,
}

_warned_unregistered: set[str] = set()


def register_component(name: str, cls: type) -> None:
    """Add a component class to the shared registry.

    Most components should just add a literal entry to
    ``_COMPONENT_REGISTRY`` (it's meant to read as a flat, alphabetical
    dict at a glance, which is also what keeps merge conflicts there
    mechanical); this helper exists for tests and for any caller that
    builds the registry programmatically.
    """
    _COMPONENT_REGISTRY[name] = cls


# ---------------------------------------------------------------------------
# World / floor (de)serialization
# ---------------------------------------------------------------------------


def _serialize_entities(world: World, entity_ids: Iterable[int]) -> dict[str, list[dict[str, Any]]]:
    """Shared by :func:`serialize_world` and :func:`serialize_floor_snapshot`.

    A component instance attached to an entity whose type isn't in
    ``_COMPONENT_REGISTRY`` is skipped (logged once per type) rather than
    raising — CONTRACTS.md §2 rule 7 ("absence = zero cost") treats a
    missing registry entry as a silent-drop bug for the lint check to
    catch, not a reason to crash a save.
    """
    entities: dict[str, list[dict[str, Any]]] = {}
    for entity_id in entity_ids:
        component_list: list[dict[str, Any]] = []
        for comp_type, instance in world.components_for(entity_id).items():
            name = comp_type.__name__
            if name not in _COMPONENT_REGISTRY:
                if name not in _warned_unregistered:
                    logger.warning(
                        "Component %s is not in _COMPONENT_REGISTRY; skipping on save "
                        "(it will not round-trip). Register it in engine/core/save.py.",
                        name,
                    )
                    _warned_unregistered.add(name)
                continue
            component_list.append({"type": name, "data": instance.to_dict()})
        entities[str(entity_id)] = component_list
    return entities


def _deserialize_entities(entities_data: dict[str, list[dict[str, Any]]], world: World) -> None:
    for entity_id_str, component_list in entities_data.items():
        entity_id = int(entity_id_str)
        world.create_entity(entity_id=entity_id)
        for entry in component_list:
            name = entry["type"]
            comp_cls = _COMPONENT_REGISTRY.get(name)
            if comp_cls is None:
                # Save data references a component type this build doesn't
                # know about (renamed, removed, or never registered) — drop
                # it silently rather than fail the whole load.
                if name not in _warned_unregistered:
                    logger.warning(
                        "Save data references unregistered component %s; dropping.",
                        name,
                    )
                    _warned_unregistered.add(name)
                continue
            instance = comp_cls.from_dict(entry["data"])
            world.add_component(entity_id, instance)


def serialize_world(world: World, spawned_uniques: set[str] | None = None) -> dict:
    """Serialize every entity/component in ``world`` plus the top-level
    save state that isn't scoped to any one floor.

    ``spawned_uniques`` (the set of legendary item IDs already dropped
    this run) is part of top-level save state, not any per-floor snapshot
    (component doc §2.5) — pass it here when writing a full save; omitted,
    it round-trips as an empty set.
    """
    return {
        "entities": _serialize_entities(world, world.entities()),
        "spawned_uniques": sorted(spawned_uniques or ()),
    }


def deserialize_world(data: dict, world: World, spawned_uniques: set[str] | None = None) -> None:
    """Populate ``world`` from a dict produced by :func:`serialize_world`.

    ``world`` should be empty (or at least free of entity-ID collisions
    with the saved data) — entities are recreated with their original IDs
    via ``World.create_entity(entity_id=...)`` so other saved state (e.g.
    spatial data rebuilt by the caller) can still refer to them.

    If ``spawned_uniques`` is given, it is cleared and then updated in
    place with the saved set — the function's own return type stays
    ``None`` per the documented signature (CONTRACTS.md §2.5); use this
    out-parameter to recover the value.
    """
    _deserialize_entities(data.get("entities", {}), world)
    if spawned_uniques is not None:
        spawned_uniques.clear()
        spawned_uniques.update(data.get("spawned_uniques", []))


def serialize_floor_snapshot(
    world: World,
    floor_id: str,
    entity_ids: Iterable[int] | None = None,
    tilemap: Any | None = None,
) -> dict:
    """Serialize a floor snapshot: the non-player entities + tilemap for
    one visited floor (component doc §2.5 — player state persists across
    floors outside any snapshot, tracked by the caller instead).

    The save system has no notion of "the player" (that's a tag component
    a later component defines); callers pass ``entity_ids`` already
    excluding the player. Omitting it snapshots every entity currently in
    ``world``, which is only correct for a world that genuinely holds no
    player entity (e.g. a dedicated per-floor sub-world, or a test).

    ``tilemap`` is an opaque, JSON-serializable payload owned by whichever
    component defines the tile map shape (worldgen) — this module doesn't
    interpret it, just stores and returns it unchanged.
    """
    ids = list(entity_ids) if entity_ids is not None else world.entities()
    return {
        "floor_id": floor_id,
        "entities": _serialize_entities(world, ids),
        "tilemap": tilemap,
    }


def restore_floor_snapshot(world: World, data: dict) -> None:
    """Recreate the entities captured by :func:`serialize_floor_snapshot`
    into ``world``. The tilemap payload, if the caller needs it, is simply
    ``data["tilemap"]`` — this function doesn't mutate its input and
    doesn't interpret the tilemap itself."""
    _deserialize_entities(data.get("entities", {}), world)


# ---------------------------------------------------------------------------
# Component-registry lint check mechanism
# ---------------------------------------------------------------------------


def discover_component_modules(
    package_names: Iterable[str] = ("engine.core", "engine.systems"),
) -> list[ModuleType]:
    """Import and return every module under each package in
    ``package_names``.

    A package that doesn't exist yet (e.g. ``engine.systems`` before any
    Wave 1 component lands) is silently skipped rather than raising — this
    keeps the lint check usable at the foundation layer, where it's
    vacuously true, and automatically picks up new component modules as
    later components add them without needing this list edited.
    """
    modules: list[ModuleType] = []
    for package_name in package_names:
        try:
            package = importlib.import_module(package_name)
        except ModuleNotFoundError:
            continue
        modules.append(package)
        package_path = getattr(package, "__path__", None)
        if package_path is None:
            continue  # a plain module, not a package — nothing to walk
        for _finder, module_name, _is_pkg in pkgutil.walk_packages(
            package_path, prefix=f"{package_name}."
        ):
            modules.append(importlib.import_module(module_name))
    return modules


def find_unregistered_components(modules: Iterable[ModuleType]) -> list[type]:
    """Walk the top-level attributes of ``modules``, collect every class
    carrying the ``@component`` marker, and return those not present (by
    identity) among ``_COMPONENT_REGISTRY``'s values.

    This is the mechanism behind
    ``tests/unit/test_component_registry.py``'s lint check
    (CONTRACTS.md §2 rule 3): a component class existing somewhere in the
    codebase but missing from ``_COMPONENT_REGISTRY`` is exactly the v1
    "silently dropped on load" bug class this guards against.
    """
    registered = set(_COMPONENT_REGISTRY.values())
    unregistered: list[type] = []
    seen: set[type] = set()
    for module in modules:
        for value in vars(module).values():
            if isinstance(value, type) and is_component(value) and value not in seen:
                seen.add(value)
                if value not in registered:
                    unregistered.append(value)
    return unregistered
