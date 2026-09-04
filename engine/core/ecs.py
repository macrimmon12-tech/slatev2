"""Entity-Component-System core.

Entities are bare integer IDs — no behavior, no identity beyond the int.
Components are data-only ``@dataclass`` instances; systems never call each
other directly, they read/write shared state through :meth:`World.query`
(or, for anything answering "what's near X", through
``engine.core.spatial_hash.SpatialHash`` — never a manual distance loop
over a position query; see that module's docstring).

Component marker
-----------------
Every component class anywhere in the codebase must be decorated with
:func:`component`. This does two things:

1. Marks the class so ``tests/unit/test_component_registry.py`` (and any
   future lint tooling) can discover every component type that exists and
   verify it has been added to
   ``engine.core.save._COMPONENT_REGISTRY`` — a component missing from
   that registry is silently dropped on save/load, which was a recurring
   v1 bug class.
2. Fills in ``to_dict``/``from_dict`` with dataclass-based defaults
   (``dataclasses.asdict`` / ``cls(**data)``) *unless* the class already
   defines its own — so a plain component only needs:

   .. code-block:: python

       @component
       @dataclass
       class PositionComponent:
           x: int
           y: int

Usage: ``@component`` must be applied to a ``@dataclass``, and applied
*after* ``@dataclass`` in decorator order (i.e. written above it), since it
inspects the dataclass fields.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Iterable, Iterator, TypeVar

_COMPONENT_MARKER = "__slate_component__"

C = TypeVar("C")


def is_component(cls: type) -> bool:
    """Return True if ``cls`` was decorated with :func:`component`."""
    return bool(getattr(cls, _COMPONENT_MARKER, False))


def component(cls: type[C]) -> type[C]:
    """Class decorator marking a dataclass as an ECS component.

    See module docstring for the exact contract. Raises ``TypeError`` if
    applied to something that isn't a dataclass — this is meant to be a
    (near) zero-cost marker, not a substitute for ``@dataclass``.
    """
    if not dataclasses.is_dataclass(cls):
        raise TypeError(
            f"@component can only decorate a @dataclass; {cls.__name__} is not one "
            "(apply @component above @dataclass)"
        )

    setattr(cls, _COMPONENT_MARKER, True)

    if "to_dict" not in cls.__dict__:
        def to_dict(self: Any) -> dict[str, Any]:
            return dataclasses.asdict(self)

        cls.to_dict = to_dict  # type: ignore[attr-defined]

    if "from_dict" not in cls.__dict__:
        def from_dict(cls_: type[C], data: dict[str, Any]) -> C:
            return cls_(**data)

        cls.from_dict = classmethod(from_dict)  # type: ignore[attr-defined]

    return cls


class World:
    """Owns entity IDs and component storage.

    Storage is a dict of component-type -> {entity_id: instance}. This
    makes :meth:`query` a straightforward set intersection over entity IDs.
    """

    def __init__(self) -> None:
        self._next_id: int = 1
        self._entities: set[int] = set()
        self._components: dict[type, dict[int, Any]] = {}

    # -- entity lifecycle -------------------------------------------------

    def create_entity(self, entity_id: int | None = None) -> int:
        """Allocate a new entity and return its ID.

        ``entity_id`` is normally omitted (auto-allocated). The save system
        passes it explicitly when reconstructing a world from serialized
        data, so restored entities keep the IDs other saved state (e.g. a
        floor snapshot's spatial data) refers to. Passing an ID already in
        use raises ``ValueError``.
        """
        if entity_id is None:
            entity_id = self._next_id
            self._next_id += 1
        else:
            if entity_id in self._entities:
                raise ValueError(f"entity_id {entity_id} already exists")
            if entity_id >= self._next_id:
                self._next_id = entity_id + 1
        self._entities.add(entity_id)
        return entity_id

    def destroy_entity(self, entity_id: int) -> None:
        """Remove an entity and all of its components.

        A no-op if the entity doesn't exist (or was already destroyed) —
        callers doing bulk cleanup shouldn't need to guard this.
        """
        self._entities.discard(entity_id)
        for store in self._components.values():
            store.pop(entity_id, None)

    def entities(self) -> list[int]:
        """All live entity IDs, snapshotted at call time."""
        return list(self._entities)

    # -- components ---------------------------------------------------------

    def add_component(self, entity_id: int, component: Any) -> None:
        if entity_id not in self._entities:
            raise ValueError(f"entity_id {entity_id} does not exist")
        comp_type = type(component)
        self._components.setdefault(comp_type, {})[entity_id] = component

    def remove_component(self, entity_id: int, component_type: type) -> None:
        store = self._components.get(component_type)
        if store is not None:
            store.pop(entity_id, None)

    def get_component(self, entity_id: int, component_type: type) -> Any | None:
        store = self._components.get(component_type)
        if store is None:
            return None
        return store.get(entity_id)

    def components_for(self, entity_id: int) -> dict[type, Any]:
        """All components currently attached to ``entity_id``, by type.

        Used by the save system to serialize an entity without needing to
        know its component types in advance.
        """
        result: dict[type, Any] = {}
        for comp_type, store in self._components.items():
            if entity_id in store:
                result[comp_type] = store[entity_id]
        return result

    def query(self, *component_types: type) -> Iterable[tuple[int, ...]]:
        """Yield ``(entity_id, comp1, comp2, ...)`` for every entity that has
        *all* of ``component_types``.

        Calling with no component types yields ``(entity_id,)`` for every
        live entity.

        The result is a fully materialized list, not a lazy view over live
        storage — so destroying an entity (or adding/removing components)
        while iterating over an already-obtained query result is safe and
        never raises ``RuntimeError: dict changed size during iteration``.
        """
        if not component_types:
            return [(entity_id,) for entity_id in self._entities]

        stores = []
        for comp_type in component_types:
            store = self._components.get(comp_type)
            if not store:
                return []
            stores.append(store)

        # Intersect over the smallest store first to minimize work.
        stores_by_size = sorted(stores, key=len)
        candidate_ids = set(stores_by_size[0].keys())
        for store in stores_by_size[1:]:
            candidate_ids &= store.keys()

        results: list[tuple[int, ...]] = []
        for entity_id in candidate_ids:
            if entity_id not in self._entities:
                # Defensive: shouldn't happen (destroy_entity clears
                # component stores too), but never surface a stale entity.
                continue
            row: tuple[Any, ...] = (entity_id,) + tuple(
                stores[i][entity_id] for i in range(len(component_types))
            )
            results.append(row)
        return results

    def __iter__(self) -> Iterator[int]:
        return iter(self.entities())
