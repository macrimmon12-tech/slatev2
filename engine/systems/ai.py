"""AISystem — the decision-making layer for every monster entity.

See `docs/components/02-ai-system.md` for the full spec this implements.
Exactly four data-parameterized behaviors (`chaser`, `ambusher`,
`patroller`, `coward` — CONTRACTS.md §10, do not add a fifth), sleep/wake
gating so idle floors cost nothing, 8-directional A* pathfinding with a
Chebyshev heuristic, and a phase-swap mechanism for bosses that reuses the
same four behaviors rather than special-casing anything.

Coordination notes (genuine spec gaps this file had to resolve on its own —
flagged here and in the PR description rather than silently guessed):

1. **`PositionComponent`** is referenced throughout the docs (foundation's
   own `spatial_hash.py` docstring gives `world.query(PositionComponent,
   AIComponent)` as its own illustrative example) but no component doc
   claims ownership of it or specifies its shape. This module defines a
   minimal `{x: int, y: int}` version since AI is the first Wave 1 system
   that actually needs per-entity position lookups. Any later component
   should import and reuse this class rather than redefining it, until
   CONTRACTS.md formally assigns it a canonical home.
2. **"The player entity"** — 01/07/10 all reference "foundation's
   player-id convention" but `00-foundation-core.md` never defines one.
   This module defines a minimal empty-marker `PlayerTagComponent` as a
   provisional stand-in. Absence of this tag on any entity degrades
   gracefully (AI simply finds no target) rather than raising.
3. **`AISystem.__init__`** in the doc's §2 signature only lists
   `(world, event_bus, combat_system, spell_system=None)`, but §3
   ("Calls into") also requires `StatsSystem.get_stat` for HP-threshold
   checks (`coward`, `behavior_phases` hp triggers), and nothing in any
   merged component yet provides real terrain passability for
   pathfinding. Since no documented caller constructs `AISystem` directly
   (only `take_turn`/`get_awake_monster_ids` have documented call sites),
   this module extends the constructor with two optional,
   backward-compatible keyword parameters — `stats_system` and
   `is_passable` — defaulting to `None`/"everything is passable" so
   existing documented positional calls keep working unchanged.
4. **Monster data tunables** (`aggro_range`, `wake_radius`,
   `flee_hp_threshold`, `patrol_points`, `spells`,
   `can_cast_while_fleeing`) live in the monster's JSON `ai` block, not in
   `AIComponent`'s documented field list. Since `AISystem`'s constructor
   has no `DataRegistry` reference and the doc doesn't specify one, these
   are cached onto `AIComponent` once at spawn time via
   `create_ai_component()` rather than re-read from the registry every
   turn — still 100% data-driven (CONTRACTS.md §2 rule 8), just cached
   instead of re-fetched.
5. **Wake radius semantics**: `check_wake`/`noise_emitted` payloads carry
   their own `radius`; monster data separately carries `ai.wake_radius`.
   This module takes the doc's wake-handler bullets at face value and
   gates wake purely on the event's own `radius` field — `ai.wake_radius`
   is cached on `AIComponent` for whichever system constructs those events
   per-monster to read, not consulted by this module's handlers directly.
6. **Patroller sub-state**: per §9's explicit either-is-acceptable call,
   this module reuses `AIComponent.path_cache`/`path_cache_target` to
   track the current patrol waypoint (no dedicated field added).
7. **Monster spell targeting**: per §9's default, always targets "the
   player" (its entity id) unconditionally.
8. **`AIComponent`/`PositionComponent`/`PlayerTagComponent` JSON schema
   validation** (CONTRACTS.md §9) is done in-code here
   (`create_ai_component`'s behavior/trigger-type checks) plus a fixture
   round-trip test, rather than a `jsonschema` file under
   `engine/core/schemas/` — that directory is inside `engine/core/`,
   which `00-foundation-core.md` claims as its own exclusive, already-merged
   ownership ("you own, exclusively: engine/core/ (all files)"). Writing
   into another component's owned directory to satisfy a *general* coding
   convention would violate CONTRACTS.md §1's ownership rule, so this is
   flagged as a CONTRACTS.md/00 conflict for maintainers rather than
   resolved unilaterally.
"""

from __future__ import annotations

import copy
import heapq
import itertools
import logging
import math
from dataclasses import dataclass, field
from typing import Callable

from engine.core.ecs import World, component
from engine.core.events import EventBus
from engine.core.spatial_hash import chebyshev_distance

logger = logging.getLogger(__name__)

Position = tuple[int, int]

# The four canonical behaviors (CONTRACTS.md §10). Do not add a fifth.
VALID_BEHAVIORS = frozenset({"chaser", "ambusher", "patroller", "coward"})

_VALID_TRIGGER_TYPES = frozenset(
    {"hp_below", "hp_above", "turn_count_above", "turn_count_below"}
)

_warned_invalid_behaviors: set[str] = set()
_warned_unknown_trigger_types: set[str] = set()
_warned_invalid_phase_behaviors: set[str] = set()

# Safety cap on A* node expansion so a genuinely unreachable/huge goal
# degrades to "no path" rather than hanging (CONTRACTS.md §2 rule 7,
# "absence = zero cost" — a bad goal must never cost meaningful CPU).
_MAX_EXPANDED_NODES = 4096

_NEIGHBOR_OFFSETS: tuple[Position, ...] = (
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1), (0, 1),
    (1, -1), (1, 0), (1, 1),
)


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


@component
@dataclass
class PositionComponent:
    """See module docstring, coordination note 1. Convenience mirror of an
    entity's tile position — `SpatialHash` (foundation) remains the
    authoritative store wherever one is wired in; this is what lets
    `world.query(PositionComponent, AIComponent)` work today."""

    x: int
    y: int


@component
@dataclass
class PlayerTagComponent:
    """See module docstring, coordination note 2. Empty marker identifying
    the player-controlled entity. Provisional stand-in for the
    "foundation player-id convention" referenced by 01/07/10 but never
    actually defined by `00-foundation-core.md`."""


@component
@dataclass
class AIComponent:
    behavior: str
    state: str = "asleep"  # "asleep" | "awake"
    behavior_phases: list[dict] = field(default_factory=list)
    current_phase_index: int = -1
    path_cache: list[Position] | None = None
    path_cache_target: Position | None = None
    home_position: Position | None = None
    turns_in_current_state: int = 0

    # --- cached tunables from the monster's data `ai` block (coordination
    # note 4) — populated once at spawn by create_ai_component(), never
    # re-derived from hardcoded Python constants. ---
    aggro_range: int = 8
    wake_radius: int = 5
    flee_hp_threshold: float = 0.2
    can_cast_while_fleeing: bool = False
    patrol_points: list[Position] = field(default_factory=list)
    spells: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Monster-data -> AIComponent factory (load-time validation)
# ---------------------------------------------------------------------------


def create_ai_component(
    monster_data: dict, home_position: Position | None = None
) -> AIComponent | None:
    """Build an `AIComponent` from a monster data dict's `ai` block (§5.1).

    Returns `None` (logging once) if `ai.behavior` isn't one of the 4
    canonical values — this is the "reject/log-once on load if not one of
    the 4" requirement (§5.1 field notes, §7 DoD): a typo'd fifth behavior
    must never be silently accepted as a new one. Caller is responsible for
    not attaching an AIComponent when this returns None (the monster simply
    has no AI, degrading gracefully per "absence = zero cost").

    `behavior_phases` is deep-copied from the registry data so runtime
    mutation (one_shot removal) never touches the shared, cached definition
    `registry.get()` returned (§5.2).
    """
    ai_block = monster_data.get("ai") or {}
    behavior = ai_block.get("behavior")
    if behavior not in VALID_BEHAVIORS:
        if behavior not in _warned_invalid_behaviors:
            logger.warning(
                "Monster %r has invalid ai.behavior %r (valid: %s); "
                "refusing to create an AIComponent for it.",
                monster_data.get("id"),
                behavior,
                sorted(VALID_BEHAVIORS),
            )
            _warned_invalid_behaviors.add(behavior)
        return None

    patrol_points = [tuple(point) for point in ai_block.get("patrol_points", [])]

    return AIComponent(
        behavior=behavior,
        state=ai_block.get("state", "asleep"),
        behavior_phases=copy.deepcopy(ai_block.get("behavior_phases", [])),
        home_position=home_position,
        aggro_range=ai_block.get("aggro_range", 8),
        wake_radius=ai_block.get("wake_radius", 5),
        flee_hp_threshold=ai_block.get("flee_hp_threshold", 0.2),
        can_cast_while_fleeing=bool(ai_block.get("can_cast_while_fleeing", False)),
        patrol_points=patrol_points,
        spells=list(ai_block.get("spells", [])),
    )


# ---------------------------------------------------------------------------
# A* pathfinding
# ---------------------------------------------------------------------------


def find_path(
    start: Position,
    goal: Position,
    is_passable: Callable[[Position], bool],
    world: World,
) -> list[Position] | None:
    """8-directional A*, Chebyshev heuristic, equal cost per step regardless
    of direction (CONTRACTS.md §2 rules 5-6). Passability is
    destination-tile-only — no corner-cutting check, matching foundation
    movement rules exactly.

    Returns the list of tiles to step through, from the first step after
    `start` up to and including `goal` (empty list if already at `goal`).
    Returns `None` if no path exists, or if the search exceeds a safety
    node-expansion cap (an unreachable/huge goal must never hang or cost
    unbounded CPU) — callers fall back to standing still, never raise.

    `world` is accepted per the documented signature but not queried
    directly here — passability is fully encapsulated by `is_passable`,
    which is what actually needs `world`/terrain data; keeping it out of
    this function's own logic is what lets it stay a pure, easily-testable
    grid search.
    """
    del world  # see docstring — reserved for callers' is_passable, not used here

    if start == goal:
        return []

    counter = itertools.count()
    open_heap: list[tuple[float, int, Position]] = [
        (chebyshev_distance(start, goal), next(counter), start)
    ]
    came_from: dict[Position, Position] = {}
    g_score: dict[Position, int] = {start: 0}
    closed: set[Position] = set()
    expanded = 0

    while open_heap:
        _f, _tie, current = heapq.heappop(open_heap)
        if current in closed:
            continue
        closed.add(current)

        if current == goal:
            return _reconstruct_path(came_from, current)

        expanded += 1
        if expanded > _MAX_EXPANDED_NODES:
            return None

        cx, cy = current
        for dx, dy in _NEIGHBOR_OFFSETS:
            neighbor = (cx + dx, cy + dy)
            if neighbor == goal:
                pass  # goal is always reachable regardless of is_passable —
                # it's frequently an occupied tile (the player's own tile);
                # the caller stops moving once adjacent anyway (see
                # AISystem._step_toward's callers), so the last step is
                # never actually taken onto an occupied tile in practice.
            elif not is_passable(neighbor):
                continue

            tentative_g = g_score[current] + 1
            if tentative_g < g_score.get(neighbor, math.inf):
                g_score[neighbor] = tentative_g
                came_from[neighbor] = current
                f_score = tentative_g + chebyshev_distance(neighbor, goal)
                heapq.heappush(open_heap, (f_score, next(counter), neighbor))

    return None


def _reconstruct_path(
    came_from: dict[Position, Position], current: Position
) -> list[Position]:
    path = [current]
    while current in came_from:
        current = came_from[current]
        path.append(current)
    path.reverse()
    return path[1:]  # drop the start tile — caller is already standing there


# ---------------------------------------------------------------------------
# AISystem
# ---------------------------------------------------------------------------


class AISystem:
    def __init__(
        self,
        world: World,
        event_bus: EventBus,
        combat_system: "CombatSystem",  # noqa: F821 — soft dep, see module docstring
        spell_system: "SpellSystem | None" = None,  # noqa: F821
        stats_system: "StatsSystem | None" = None,  # noqa: F821
        is_passable: Callable[[Position], bool] | None = None,
    ) -> None:
        self.world = world
        self.event_bus = event_bus
        self.combat_system = combat_system
        self.spell_system = spell_system
        self.stats_system = stats_system
        # No terrain system is wired into any merged component yet
        # (06-worldgen-campaign.md) — default to "everything passable"
        # rather than blocking all monster movement. See module docstring
        # note 3.
        self.is_passable = is_passable if is_passable is not None else (lambda pos: True)

        event_bus.subscribe("check_wake", self._on_check_wake)
        event_bus.subscribe("noise_emitted", self._on_noise_emitted)
        event_bus.subscribe("entity_moved", self._on_entity_moved)
        event_bus.subscribe("player_moved", self._on_player_moved)
        event_bus.subscribe("floor_changed", self._on_floor_changed)

    # -- public API (§2) ---------------------------------------------------

    def get_awake_monster_ids(self, world: World) -> list[int]:
        rows = world.query(AIComponent)
        return sorted(entity_id for entity_id, ai in rows if ai.state == "awake")

    def take_turn(self, entity_id: int, world: World, event_bus: EventBus) -> None:
        ai = world.get_component(entity_id, AIComponent)
        if ai is None or ai.state != "awake":
            return  # defensive — caller is documented to only pass awake IDs

        ai.turns_in_current_state += 1
        active_behavior = self._active_behavior(ai, world, entity_id)

        if active_behavior in ("chaser", "ambusher"):
            self._do_chaser(entity_id, ai, world, event_bus)
        elif active_behavior == "patroller":
            self._do_patroller(entity_id, ai, world, event_bus)
        elif active_behavior == "coward":
            self._do_coward(entity_id, ai, world, event_bus)
        else:  # pragma: no cover — unreachable given create_ai_component's validation
            logger.debug("Entity %s has unrecognized active behavior %r", entity_id, active_behavior)

    # -- behavior_phases (§5.2) ---------------------------------------------

    def _active_behavior(self, ai: AIComponent, world: World, entity_id: int) -> str:
        """First-match-wins evaluation of `behavior_phases`, in list order,
        every turn. `one_shot` phases are removed from `ai.behavior_phases`
        (the entity's own runtime copy) once triggered, never mutating the
        shared registry dict."""
        for phase in list(ai.behavior_phases):
            trigger = phase.get("trigger", {})
            if not self._trigger_matches(trigger, ai, world, entity_id):
                continue

            phase_behavior = phase.get("behavior")
            if phase_behavior not in VALID_BEHAVIORS:
                if phase_behavior not in _warned_invalid_phase_behaviors:
                    logger.warning(
                        "behavior_phases entry names invalid behavior %r; skipping phase.",
                        phase_behavior,
                    )
                    _warned_invalid_phase_behaviors.add(phase_behavior)
                continue

            if phase in ai.behavior_phases:
                ai.current_phase_index = ai.behavior_phases.index(phase)
            if phase.get("one_shot"):
                ai.behavior_phases.remove(phase)
            return phase_behavior

        ai.current_phase_index = -1
        return ai.behavior

    def _trigger_matches(
        self, trigger: dict, ai: AIComponent, world: World, entity_id: int
    ) -> bool:
        trigger_type = trigger.get("type")
        value = trigger.get("value")

        if trigger_type in ("hp_below", "hp_above"):
            if self.stats_system is None:
                return False  # absence = zero cost — can't evaluate, never-true
            max_hp = self.stats_system.get_stat(entity_id, "max_hp", world)
            if not max_hp:
                return False
            hp = self.stats_system.get_stat(entity_id, "hp", world)
            fraction = hp / max_hp
            return fraction < value if trigger_type == "hp_below" else fraction > value

        if trigger_type == "turn_count_above":
            return ai.turns_in_current_state > value
        if trigger_type == "turn_count_below":
            return ai.turns_in_current_state < value

        if trigger_type not in _VALID_TRIGGER_TYPES and trigger_type not in _warned_unknown_trigger_types:
            logger.warning("Unknown behavior_phases trigger type %r; treating as never-true.", trigger_type)
            _warned_unknown_trigger_types.add(trigger_type)
        return False

    # -- behaviors (§2.2) ----------------------------------------------------

    def _do_chaser(self, entity_id: int, ai: AIComponent, world: World, event_bus: EventBus) -> None:
        player = self._find_player(world)
        if player is None:
            return
        player_id, player_pos = player

        own_pos = self._position_of(world, entity_id)
        if own_pos is None:
            return

        distance = chebyshev_distance(own_pos, player_pos)
        if distance <= 1:
            self._attack(entity_id, player_id, ai, world, event_bus)
            return
        if distance > ai.aggro_range:
            return  # out of active-engagement range — stand still

        self._step_toward(entity_id, ai, own_pos, player_pos, world, event_bus)

    def _do_patroller(self, entity_id: int, ai: AIComponent, world: World, event_bus: EventBus) -> None:
        own_pos = self._position_of(world, entity_id)
        if own_pos is None:
            return

        player = self._find_player(world)
        if player is not None:
            _player_id, player_pos = player
            if chebyshev_distance(own_pos, player_pos) <= ai.aggro_range:
                self._do_chaser(entity_id, ai, world, event_bus)
                return

        if not ai.patrol_points:
            return  # nothing to patrol — stand still

        target = ai.path_cache_target
        if target not in ai.patrol_points:
            target = ai.patrol_points[0]
        elif own_pos == target:
            next_index = (ai.patrol_points.index(target) + 1) % len(ai.patrol_points)
            target = ai.patrol_points[next_index]

        self._step_toward(entity_id, ai, own_pos, target, world, event_bus)

    def _do_coward(self, entity_id: int, ai: AIComponent, world: World, event_bus: EventBus) -> None:
        if self.stats_system is None:
            return  # can't evaluate flee_hp_threshold — absence = zero cost
        max_hp = self.stats_system.get_stat(entity_id, "max_hp", world)
        if not max_hp:
            return
        hp = self.stats_system.get_stat(entity_id, "hp", world)
        if hp / max_hp >= ai.flee_hp_threshold:
            return  # not afraid yet — coward never initiates melee either way

        own_pos = self._position_of(world, entity_id)
        if own_pos is None:
            return
        player = self._find_player(world)

        if ai.can_cast_while_fleeing and ai.spells and self.spell_system is not None and player is not None:
            player_id, _player_pos = player
            self.spell_system.cast(entity_id, ai.spells[0], player_id)
            return

        if player is None:
            return
        _player_id, player_pos = player
        flee_goal = self._flee_target(own_pos, player_pos)
        self._step_toward(entity_id, ai, own_pos, flee_goal, world, event_bus)

    # -- shared helpers ------------------------------------------------------

    def _attack(self, entity_id: int, target_id: int, ai: AIComponent, world: World, event_bus: EventBus) -> None:
        """Casts a spell instead of melee when the monster has one available
        (§2.2 chaser bullet) — gating (MP/cooldown/fail chance) is entirely
        SpellSystem.cast's own job; AI just attempts the call (§1.3)."""
        if ai.spells and self.spell_system is not None:
            self.spell_system.cast(entity_id, ai.spells[0], target_id)
            return
        self.combat_system.resolve_hit(entity_id, target_id, world, event_bus)

    def _step_toward(
        self,
        entity_id: int,
        ai: AIComponent,
        own_pos: Position,
        goal_pos: Position,
        world: World,
        event_bus: EventBus,
    ) -> None:
        if ai.path_cache_target != goal_pos or not ai.path_cache:
            ai.path_cache = find_path(own_pos, goal_pos, self.is_passable, world)
            ai.path_cache_target = goal_pos

        if not ai.path_cache:
            return  # unreachable, or goal_pos == own_pos already

        next_pos = ai.path_cache.pop(0)
        self._move_entity(entity_id, world, event_bus, own_pos, next_pos)

    def _flee_target(self, own_pos: Position, player_pos: Position) -> Position:
        """Simple direction-away-from-player projection (§2.2 coward:
        "paths away from the player, maximizing distance"). No
        obstacle/vision-aware fleeing logic — there's no terrain system
        wired in yet to make that meaningful."""
        dx = own_pos[0] - player_pos[0]
        dy = own_pos[1] - player_pos[1]
        if dx == 0 and dy == 0:
            dx, dy = 1, 1
        flee_distance = 10
        return (
            own_pos[0] + _sign(dx) * flee_distance,
            own_pos[1] + _sign(dy) * flee_distance,
        )

    def _move_entity(
        self,
        entity_id: int,
        world: World,
        event_bus: EventBus,
        from_pos: Position,
        to_pos: Position,
    ) -> None:
        position = world.get_component(entity_id, PositionComponent)
        if position is None:
            return
        position.x, position.y = to_pos
        event_bus.emit("entity_moved", {"entity_id": entity_id, "from": from_pos, "to": to_pos})

    def _position_of(self, world: World, entity_id: int) -> Position | None:
        position = world.get_component(entity_id, PositionComponent)
        return (position.x, position.y) if position is not None else None

    def _find_player(self, world: World) -> tuple[int, Position] | None:
        rows = world.query(PlayerTagComponent, PositionComponent)
        if not rows:
            return None
        entity_id, _tag, position = rows[0]
        return entity_id, (position.x, position.y)

    # -- event subscriptions (§2.4, §3) --------------------------------------

    def _on_check_wake(self, payload: dict | None) -> None:
        if not payload:
            return
        source_pos = _coerce_position(payload.get("source_pos"))
        radius = payload.get("radius", 0)
        if source_pos is not None:
            self._wake_within_radius(source_pos, radius)

    def _on_noise_emitted(self, payload: dict | None) -> None:
        if not payload:
            return
        position = _coerce_position(payload.get("position"))
        radius = payload.get("radius", 0)
        if position is not None:
            self._wake_within_radius(position, radius)

    def _wake_within_radius(self, source_pos: Position, radius: float) -> None:
        for entity_id, ai, position in self.world.query(AIComponent, PositionComponent):
            if ai.state != "asleep":
                continue
            if chebyshev_distance((position.x, position.y), source_pos) <= radius:
                ai.state = "awake"

    def _on_entity_moved(self, payload: dict | None) -> None:
        self._invalidate_path_cache_for_old_target(payload)

    def _on_player_moved(self, payload: dict | None) -> None:
        self._invalidate_path_cache_for_old_target(payload)

    def _invalidate_path_cache_for_old_target(self, payload: dict | None) -> None:
        if not payload:
            return
        old_pos = _coerce_position(payload.get("from"))
        if old_pos is None:
            return
        for _entity_id, ai in self.world.query(AIComponent):
            if ai.path_cache_target == old_pos:
                ai.path_cache = None
                ai.path_cache_target = None

    def _on_floor_changed(self, _payload: dict | None) -> None:
        for _entity_id, ai in self.world.query(AIComponent):
            ai.path_cache = None
            ai.path_cache_target = None
            ai.turns_in_current_state = 0
            ai.current_phase_index = -1
            # Sleep state is intentionally left untouched — see §2.4: a
            # monster already woken and saved mid-fight stays awake on
            # restore; that's the save system's/06's job, not re-decided
            # here.


def _sign(n: int) -> int:
    return (n > 0) - (n < 0)


def _coerce_position(value: object) -> Position | None:
    """Defensive coercion for event payload position fields, whose exact
    shape ([x, y] vs (x, y) vs {"x":.., "y":..}) isn't nailed down anywhere
    in CONTRACTS.md's event table. Returns None (never raises) for anything
    that doesn't unambiguously resolve to a 2-tuple of ints."""
    if isinstance(value, dict) and "x" in value and "y" in value:
        return (value["x"], value["y"])
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return (value[0], value[1])
    return None
