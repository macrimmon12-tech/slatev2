"""Authoritative position store + the one canonical distance function.

**Read this before querying "what's near X" anywhere in the codebase.**
``SpatialHash`` is authoritative for entity position — not
``PositionComponent``. ``PositionComponent`` (defined by a later component)
is a convenience mirror other systems may query *alongside* other
components (e.g. ``world.query(PositionComponent, AIComponent)`` to get an
AI-bearing entity's other data), but any code answering "what's near X" —
vision, noise, AoE, pathfinding neighbor checks, wake triggers, trap
detection — **must** go through ``SpatialHash.query_radius``/``query_rect``,
never ``world.query(PositionComponent)`` plus a manual distance loop. A
manual loop will silently drift from Chebyshev distance the moment someone
writes ``dx*dx + dy*dy`` out of habit; going through this module is what
prevents that category of bug.

Distance is Chebyshev everywhere in this codebase, no exceptions:
``max(abs(dx), abs(dy))``. This matches 8-way movement where diagonal and
cardinal steps cost the same (CONTRACTS.md §2 rules 5-6).
"""

from __future__ import annotations

Position = tuple[int, int]

DEFAULT_CELL_SIZE = 16


def chebyshev_distance(a: Position, b: Position) -> int:
    """The one canonical distance function. ``max(|dx|, |dy|)`` — every
    system that measures distance between two tile positions imports this
    rather than inlining the formula."""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


class SpatialHash:
    """Grid-based spatial index of entity positions.

    Entities are bucketed into fixed-size cells; radius/rect queries only
    scan the cells overlapping the query area rather than every entity.
    """

    def __init__(self, cell_size: int = DEFAULT_CELL_SIZE) -> None:
        if cell_size <= 0:
            raise ValueError("cell_size must be positive")
        self._cell_size = cell_size
        self._positions: dict[int, Position] = {}
        self._cells: dict[tuple[int, int], set[int]] = {}

    def _cell_of(self, position: Position) -> tuple[int, int]:
        x, y = position
        return (x // self._cell_size, y // self._cell_size)

    def insert(self, entity_id: int, position: Position) -> None:
        """Add an entity at ``position``. If the entity is already tracked,
        this re-inserts it at the new position (equivalent to calling
        :meth:`move` from its old position)."""
        if entity_id in self._positions:
            self.remove(entity_id)
        self._positions[entity_id] = position
        self._cells.setdefault(self._cell_of(position), set()).add(entity_id)

    def move(self, entity_id: int, old_pos: Position, new_pos: Position) -> None:
        """Update a tracked entity's position.

        ``old_pos`` must match what the hash currently has on file for
        ``entity_id`` — callers are responsible for keeping their own
        bookkeeping (e.g. ``PositionComponent``) in sync with what they
        pass here; this is not re-derived from the entity's current cell to
        keep the contract explicit about who owns "where was this."
        """
        tracked = self._positions.get(entity_id)
        if tracked is None:
            # Not tracked yet — treat as a fresh insert rather than raising,
            # so "absence = zero cost" holds for callers that don't
            # distinguish first-placement from movement.
            self.insert(entity_id, new_pos)
            return
        if tracked != old_pos:
            raise ValueError(
                f"stale old_pos for entity {entity_id}: hash has {tracked}, caller passed {old_pos}"
            )
        old_cell = self._cell_of(old_pos)
        new_cell = self._cell_of(new_pos)
        if old_cell != new_cell:
            bucket = self._cells.get(old_cell)
            if bucket is not None:
                bucket.discard(entity_id)
                if not bucket:
                    del self._cells[old_cell]
            self._cells.setdefault(new_cell, set()).add(entity_id)
        self._positions[entity_id] = new_pos

    def remove(self, entity_id: int) -> None:
        """Untrack an entity. A no-op if it isn't tracked."""
        position = self._positions.pop(entity_id, None)
        if position is None:
            return
        cell = self._cell_of(position)
        bucket = self._cells.get(cell)
        if bucket is not None:
            bucket.discard(entity_id)
            if not bucket:
                del self._cells[cell]

    def position_of(self, entity_id: int) -> Position | None:
        return self._positions.get(entity_id)

    def query_radius(self, center: Position, radius: int) -> list[int]:
        """Entities within ``radius`` (Chebyshev) of ``center``, inclusive."""
        if radius < 0:
            return []
        cx, cy = center
        min_cell_x, min_cell_y = self._cell_of((cx - radius, cy - radius))
        max_cell_x, max_cell_y = self._cell_of((cx + radius, cy + radius))

        results: list[int] = []
        for cell_x in range(min_cell_x, max_cell_x + 1):
            for cell_y in range(min_cell_y, max_cell_y + 1):
                bucket = self._cells.get((cell_x, cell_y))
                if not bucket:
                    continue
                for entity_id in bucket:
                    if chebyshev_distance(self._positions[entity_id], center) <= radius:
                        results.append(entity_id)
        return results

    def query_rect(self, top_left: Position, bottom_right: Position) -> list[int]:
        """Entities within the inclusive axis-aligned rectangle
        ``[top_left, bottom_right]``. Coordinates need not be pre-sorted."""
        x0, y0 = top_left
        x1, y1 = bottom_right
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0

        min_cell_x, min_cell_y = self._cell_of((x0, y0))
        max_cell_x, max_cell_y = self._cell_of((x1, y1))

        results: list[int] = []
        for cell_x in range(min_cell_x, max_cell_x + 1):
            for cell_y in range(min_cell_y, max_cell_y + 1):
                bucket = self._cells.get((cell_x, cell_y))
                if not bucket:
                    continue
                for entity_id in bucket:
                    px, py = self._positions[entity_id]
                    if x0 <= px <= x1 and y0 <= py <= y1:
                        results.append(entity_id)
        return results
