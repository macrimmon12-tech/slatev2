"""VisionSystem: lit-room + personal-radius tile visibility, plus the
intentionally inert trap-detection placeholder.

See ``docs/components/05-progression-vision.md`` for the full spec. Key
behaviors (spec §2.2):

- Visible tiles = union of (a) every tile in the entity's current room, if
  that room is flagged ``lit: true``, and (b) every tile within
  ``vision_range`` Chebyshev distance of the entity's position (a flat
  disc, deliberately no shadow-casting/line-of-sight -- spec §3/§11.5, do
  not "improve" this).
- Every visible tile is permanently marked 'seen' (fog-of-war memory) --
  'seen' is a strict superset of 'currently visible' that is never
  shrunk.
- Degrades to personal-radius-only, without raising, on a floor missing
  ``room_id``/``lit`` room data (spec §5.2 / §6 -- same graceful-degradation
  philosophy as 02's AI noise attenuation).

CONTRACTS.md §8 stubbing notes (Wave 1, built in parallel with siblings
that may not be merged yet):

- ``01-stats-combat.md``'s ``StatsSystem.get_stat`` isn't merged at the
  time this was written, and there's no global ``StatsSystem`` singleton
  documented anywhere in CONTRACTS.md. ``VisionSystem.__init__`` therefore
  accepts an *additional optional* keyword-only ``stats_system``
  dependency (``None`` by default); when absent, ``vision_range`` and
  ``trap_detect_radius`` fall back to small built-in defaults, logged
  once, never raising -- absence = zero cost (CONTRACTS.md §2 rule 7).
  This mirrors the same conservative default taken in
  ``engine/systems/progression.py``.
- Entity position: ``engine/core/spatial_hash.py`` documents ``SpatialHash``
  as authoritative, but CONTRACTS.md doesn't document a global
  ``SpatialHash`` singleton, and ``02-ai-system.md`` (merged after this
  component's initial implementation) resolved the same gap in practice by
  reading/writing a per-entity ``PositionComponent`` directly rather than
  through ``SpatialHash``. This module follows that precedent:
  ``update_visibility``/``scan_for_traps`` resolve position via
  ``world.get_component(entity_id, PositionComponent)`` first, falling
  back to an optional injected ``spatial_hash`` (kept for tests/callers
  that prefer it, and created empty if none is given) only when no
  ``PositionComponent`` is attached.
- ``06-worldgen-campaign.md``'s per-room ``lit``/``tiles`` data (spec §5.2)
  isn't produced by any merged component yet. ``VisionSystem`` exposes
  ``register_floor_rooms(floor_id, rooms)`` as the intended integration
  point -- ``06`` (or whatever wires floors up in the meantime) calls it
  once room data exists for a floor; a floor with nothing registered for
  it degrades to personal-radius-only automatically, which is exactly the
  required graceful-degradation behavior (spec §5.2/§6).
- "Foundation player-id convention": several component docs
  (``01-stats-combat.md``, ``10-lua-scripting-layer.md``) reference "00's
  foundation player-id convention", but no merged foundation code nor
  CONTRACTS.md actually defines one. ``02-ai-system.md`` (merged after
  this component's initial implementation) hit the same gap and defined a
  provisional ``PlayerTagComponent``/``PositionComponent`` pair in
  ``engine/systems/ai.py``, explicitly inviting reuse ("any later
  component should import and reuse this class rather than redefining
  it"). This module takes that invitation: entity position is resolved
  via ``PositionComponent`` first (matching how AI's own movement handling
  actually populates it), and the player entity id via
  ``world.query(PlayerTagComponent, PositionComponent)`` when
  ``player_moved``'s payload doesn't carry an explicit ``entity_id`` and
  none has been cached yet. ``player_moved``'s payload is documented in
  CONTRACTS.md §3.2 with key fields ``from``/``to`` only (no
  ``entity_id``); this system still reads ``entity_id`` from the payload
  when present (treating the table's "(key fields)" heading as
  non-exhaustive), falls back to the cached id, then to the
  ``PlayerTagComponent`` query, and only logs once and no-ops if none of
  the three resolve anything -- never guessing, never crashing.
- Visibility state persistence (spec §9 Open Questions): stored as a plain
  per-floor dict owned internally by this class (the doc's documented
  alternative to a ``VisibilityComponent`` -- see component doc §4), not
  as an ECS component. Wiring it into the floor-snapshot save mechanism is
  deferred: ``engine/core/save.py`` (owned exclusively by
  ``00-foundation-core``) doesn't currently expose an extension point for
  arbitrary per-floor system state beyond entities + tilemap, so this
  component doesn't attempt to bolt one on. Noted in this component's PR
  for `06`/foundation to coordinate a hook when floor snapshots need it.
"""

from __future__ import annotations

import logging
from typing import Any

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.spatial_hash import SpatialHash, chebyshev_distance

# 02-ai-system.md's provisional stand-ins for the undocumented "foundation
# player-id convention" (see module docstring). ai.py explicitly invites
# reuse of these rather than redefining them.
from engine.systems.ai import PlayerTagComponent, PositionComponent

logger = logging.getLogger(__name__)

Position = tuple[int, int]

# Fallback defaults used only when no StatsSystem is wired in yet
# (CONTRACTS.md §8 stubbing).
_FALLBACK_STAT_DEFAULTS: dict[str, float] = {
    "vision_range": 6,
    "trap_detect_radius": 0,
}

# Placeholder starting floor id used before any floor_changed event has
# ever fired -- see module docstring's floor-id note. floor_changed's
# payload (CONTRACTS.md §3.2) carries integer depths, not a floor_id
# string, so floor_id here is just str(depth); this is a documented
# stand-in until 06 establishes a real floor identifier convention.
_INITIAL_FLOOR_ID = "1"


class VisionSystem:
    def __init__(
        self,
        world: World,
        event_bus: EventBus,
        *,
        stats_system: Any | None = None,
        spatial_hash: SpatialHash | None = None,
    ) -> None:
        self.world = world
        self.event_bus = event_bus
        self._stats_system = stats_system
        self._spatial_hash = spatial_hash if spatial_hash is not None else SpatialHash()

        self._visible: dict[str, set[Position]] = {}
        self._seen: dict[str, set[Position]] = {}
        # floor_id -> list of {"room_id", "lit", "tiles": [[x, y], ...]}
        # (spec §5.2 shape). Populated via register_floor_rooms.
        self._floor_rooms: dict[str, list[dict[str, Any]]] = {}

        self._current_floor_id: str = _INITIAL_FLOOR_ID
        self._player_entity_id: int | None = None
        self._warned: set[str] = set()

        event_bus.subscribe("player_moved", self._on_player_moved)
        event_bus.subscribe("floor_changed", self._on_floor_changed)

    # -- wiring / integration points ------------------------------------------

    def register_floor_rooms(self, floor_id: str, rooms: list[dict[str, Any]]) -> None:
        """Registers room/tile data for a floor (spec §5.2's shape, one
        entry per room: ``{"room_id", "lit", "tiles": [[x, y], ...]}``).

        Intended to be called by whatever wires up floor data (06's
        worldgen once merged, or a test/fixture in the meantime). A floor
        with nothing registered here degrades to personal-radius-only
        vision, per spec's graceful-degradation requirement.
        """
        self._floor_rooms[floor_id] = rooms

    # -- event handlers -------------------------------------------------------

    def _on_player_moved(self, payload: dict | None) -> None:
        entity_id = self._resolve_player_entity_id(payload)
        if entity_id is None:
            self._warn_once(
                "no_player_entity_id",
                "player_moved received without a resolvable entity_id "
                "(the 'foundation player-id convention' referenced by "
                "other component docs isn't actually defined anywhere yet "
                "-- see this component's PR); VisionSystem cannot update "
                "visibility until one is available.",
            )
            return
        self._player_entity_id = entity_id

        to_pos = payload.get("to") if payload else None
        if to_pos is not None and self.world.get_component(entity_id, PositionComponent) is None:
            # No PositionComponent to reflect the move (e.g. it's owned by
            # movement handling that isn't merged/wired yet) -- keep our
            # own SpatialHash fallback in sync from the payload instead.
            self._spatial_hash.insert(entity_id, tuple(to_pos))

        self.update_visibility(entity_id, self.world)
        self.scan_for_traps(entity_id, self.world, self.event_bus)

    def _on_floor_changed(self, payload: dict | None) -> None:
        to_depth = payload.get("to_depth") if payload else None
        if to_depth is not None:
            self._current_floor_id = str(to_depth)
        else:
            self._warn_once(
                "floor_changed_missing_to_depth",
                "floor_changed payload missing to_depth; keeping the "
                "current floor_id.",
            )

        if self._player_entity_id is None:
            return  # no known player yet -- nothing to recompute
        self.update_visibility(self._player_entity_id, self.world)
        self.scan_for_traps(self._player_entity_id, self.world, self.event_bus)

    def _resolve_player_entity_id(self, payload: dict | None) -> int | None:
        if payload and payload.get("entity_id") is not None:
            return payload["entity_id"]
        if self._player_entity_id is not None:
            return self._player_entity_id
        # Fall back to 02-ai-system.md's PlayerTagComponent convention
        # (see module docstring) before giving up.
        rows = self.world.query(PlayerTagComponent)
        if rows:
            return rows[0][0]
        return None

    # -- vision -----------------------------------------------------------------

    def update_visibility(self, entity_id: int, world: World) -> set[Position]:
        """Recomputes and returns the currently-visible tile set for
        ``entity_id`` on the current floor; also folds it into that
        floor's permanent 'seen' set."""
        position = self._position_of(entity_id, world)
        if position is None:
            self._warn_once(
                "no_position",
                "update_visibility called for an entity with no known "
                "position in the SpatialHash; skipping.",
            )
            return set()

        floor_id = self._current_floor_id
        vision_range = int(self._get_stat(entity_id, "vision_range", world))

        visible: set[Position] = self._personal_radius_tiles(position, vision_range)

        room = self._current_room(floor_id, position)
        if room is not None and room.get("lit"):
            visible |= {tuple(tile) for tile in room.get("tiles", [])}

        self._visible[floor_id] = visible
        self._seen.setdefault(floor_id, set()).update(visible)
        return visible

    def get_visible_tiles(self, floor_id: str) -> set[Position]:
        return set(self._visible.get(floor_id, set()))

    def get_seen_tiles(self, floor_id: str) -> set[Position]:
        return set(self._seen.get(floor_id, set()))

    @staticmethod
    def _personal_radius_tiles(center: Position, radius: int) -> set[Position]:
        if radius < 0:
            return set()
        cx, cy = center
        return {
            (x, y)
            for x in range(cx - radius, cx + radius + 1)
            for y in range(cy - radius, cy + radius + 1)
            if chebyshev_distance((x, y), center) <= radius
        }

    def _current_room(self, floor_id: str, position: Position) -> dict[str, Any] | None:
        for room in self._floor_rooms.get(floor_id, []):
            tiles = {tuple(tile) for tile in room.get("tiles", [])}
            if position in tiles:
                return room
        return None

    def _position_of(self, entity_id: int, world: World) -> Position | None:
        """Resolves an entity's position, preferring ``PositionComponent``
        (02-ai-system.md's convention -- see module docstring) and falling
        back to the injected/internal ``SpatialHash`` if the entity has no
        such component attached."""
        position = world.get_component(entity_id, PositionComponent)
        if position is not None:
            return (position.x, position.y)
        return self._spatial_hash.position_of(entity_id)

    # -- trap detection (intentional stub) -------------------------------------

    def scan_for_traps(self, entity_id: int, world: World, event_bus: EventBus) -> None:
        """INTENTIONAL STUB -- see spec §12 and component doc §1.1.

        Trap detection was designed but never finished in v1: a real,
        data-driven ``trap_detect_radius`` stat exists (owned by 01, just
        read here), but no ``TrapComponent`` and no trigger system has ever
        existed in this codebase, and building one here would be building
        the (explicitly out-of-scope) trap system, not this component's
        job. This method reads a real ``trap_detect_radius`` value and
        would emit ``trap_revealed`` per found trap *if* ``TrapComponent``
        existed -- since it doesn't, the guarded lookup below always fails
        and this is correctly a permanent no-op, called from the same
        per-move hook as ``update_visibility`` so the call site exists and
        is exercised without building any part of the trap system itself.

        DO NOT add a ``TrapComponent`` here to make this loop "do
        something" -- that is spec §12's deferred scope, not this
        component's (see component doc §1.1's explicit warning).
        """
        position = self._position_of(entity_id, world)
        if position is None:
            return
        trap_detect_radius = self._get_stat(entity_id, "trap_detect_radius", world)

        try:
            from engine.systems.traps import TrapComponent  # type: ignore
        except ImportError:
            # No TrapComponent exists anywhere in this codebase (spec §12
            # deferral) -- nothing to query, permanent no-op by design.
            return

        for trap_entity_id, trap in world.query(TrapComponent):  # pragma: no cover
            trap_position = self._position_of(trap_entity_id, world)
            if trap_position is None:
                continue
            if chebyshev_distance(trap_position, position) <= trap_detect_radius:
                event_bus.emit(
                    "trap_revealed",
                    {"position": trap_position, "trap_id": getattr(trap, "trap_id", None)},
                )

    # -- helpers ----------------------------------------------------------------

    def _get_stat(self, entity_id: int, stat_name: str, world: World) -> float:
        if self._stats_system is not None:
            return self._stats_system.get_stat(entity_id, stat_name, world)
        self._warn_once(
            "no_stats_system",
            "VisionSystem constructed without a StatsSystem "
            "(01-stats-combat.md not wired yet, CONTRACTS.md §8); falling "
            "back to built-in stat defaults.",
        )
        return _FALLBACK_STAT_DEFAULTS.get(stat_name, 0.0)

    def _warn_once(self, key: str, message: str) -> None:
        if key not in self._warned:
            logger.warning(message)
            self._warned.add(key)
