# Component 02 — AI System

**Wave:** 1 (parallel).
**Depends on:** Wave 0 (`00-foundation-core.md`) + `docs/CONTRACTS.md`.
Soft code-level dependency on `01-stats-combat.md` (for `resolve_hit`) and
`03-spells-status.md` (for `SpellSystem.cast`) — see §1.3 for how to build
and test without either merged yet.

## 1. Scope & Boundaries

You are building `AISystem` — the decision-making layer for every monster
entity. It has exactly four behaviors, sleep/wake gating so idle floors cost
nothing, A* pathfinding, and a phase-swap mechanism for bosses that reuses
the same four behaviors rather than special-casing anything.

In scope:
- Sleep/wake: monsters spawn asleep by default; wake on `check_wake` or
  `noise_emitted` if within range/radius of the source.
- Exactly 4 behaviors: `chaser`, `ambusher`, `patroller`, `coward`. **Do not
  add a fifth.** This is a spec-level constraint (CONTRACTS.md §10, spec
  §3), not a suggestion — combinatorial simplicity (4 behaviors × data
  variation) is a v1 win explicitly called out as worth preserving
  unchanged.
- A* pathfinding, 8-directional, Chebyshev heuristic (from foundation's
  `chebyshev_distance` — do not write a second distance function), cached
  per entity, invalidated on `entity_moved`/`player_moved`.
- Multi-phase bosses via `behavior_phases` — a condition list on monster
  data that swaps which of the 4 behaviors is currently active. No
  special-case boss code anywhere in `ai.py`; a "boss" is just a monster
  whose data has a non-empty `behavior_phases` list.
- Monster spellcasting: monsters with spells in their data call
  `SpellSystem.cast()` — the exact same entry point player input calls.
  See §1.3 for sequencing.
- `process_monster_turns()`'s per-entity decision function
  (`ai_system.take_turn(entity_id, world, event_bus)`), which `01`'s
  `CombatSystem.process_monster_turns` loop calls per monster in ascending
  entity-ID order. You do not own the loop or its ordering guarantee — `01`
  does — you own only what one monster decides to do on its turn.

Out of scope:
- The turn-order loop itself (owned by `01`'s `CombatSystem`).
- Melee hit resolution math (owned by `01`; you call
  `combat.resolve_hit(attacker_id, defender_id, world, event_bus)`).
- Spell effect resolution (owned by `03`/`01`; you call
  `spell_system.cast(entity_id, spell_id, target)` and stop there).
- Player-controlled movement/input (owned by `07-input-renderer-audio.md`).

### 1.3 Sequencing around the SpellSystem soft dependency

`03-spells-status.md` may not be merged when you start. Build and fully
test AI with monsters that only ever melee-attack via `CombatSystem` first
— that is a complete, mergeable component on its own. Monster spellcasting
behavior is an **additive integration test** you add once `03` lands (or,
if your PR merges first, a follow-up any later session can add without
touching your core decision logic — the call site is a single guarded call:
`if monster_data.get("spells") and self._spell_system is not None:
self._spell_system.cast(...)`). Do not block your PR on `03` merging. Do
not stub a fake `SpellSystem` — per CONTRACTS.md §8, treat the entry point
as documented-but-possibly-absent and skip the cast attempt (falling back
to melee) if the real one isn't wired into your constructor yet.

## 2. Provides

```python
# engine/systems/ai.py

class AISystem:
    def __init__(self, world: World, event_bus: EventBus,
                 combat_system: "CombatSystem",
                 spell_system: "SpellSystem | None" = None): ...

    def take_turn(self, entity_id: int, world: World, event_bus: EventBus) -> None:
        """Called once per awake monster per game turn, by 01's
        CombatSystem.process_monster_turns() loop, in ascending entity-ID
        order (guaranteed by the caller, not by this function). Reads the
        entity's AIComponent + monster data, evaluates behavior_phases (if
        any) to pick the active behavior, then dispatches to the matching
        behavior handler (see §2.2). A sleeping monster is never passed to
        this function at all — the caller/this system filters by
        AIComponent.state == "awake" before invoking take_turn; document
        this precisely: sleep filtering happens in a helper this system
        exposes, `get_awake_monster_ids(world) -> list[int]`, which 01's
        loop calls before iterating, keeping the "which monsters get a turn
        at all" question owned by 02, and "in what order" owned by 01.
        """

    def get_awake_monster_ids(self, world: World) -> list[int]:
        """Returns entity IDs of every monster with AIComponent.state ==
        'awake', in ascending entity-ID order (so callers that don't
        re-sort still get determinism)."""
```

### 2.1 `AIComponent` (new component, registered in `_COMPONENT_REGISTRY`)

```python
@component
@dataclass
class AIComponent:
    behavior: str                     # "chaser" | "ambusher" | "patroller" | "coward"
    state: str = "asleep"             # "asleep" | "awake"
    behavior_phases: list[dict] = field(default_factory=list)  # see §5.2
    current_phase_index: int = 0
    path_cache: list[tuple[int, int]] | None = None
    path_cache_target: tuple[int, int] | None = None
    home_position: tuple[int, int] | None = None   # for patroller/ambusher return-to-post
    turns_in_current_state: int = 0   # for turn-count phase triggers
```

### 2.2 The four behaviors (data-parameterized, no fifth)

All four read tunables from the monster's data `ai` block (§5.1) — e.g.
`aggro_range`, `patrol_points`, `flee_hp_threshold` — never from hardcoded
Python constants, per CONTRACTS.md §2.8 (data over code).

- **`chaser`** — on wake, path toward the player every turn using A*
  (§2.3); melee-attacks (`combat.resolve_hit`) when adjacent (Chebyshev
  distance 1); casts a spell instead of melee if the monster has spells and
  a valid targeted spell is off cooldown/affordable (delegated entirely to
  `SpellSystem.cast`'s own gating — AI doesn't re-check MP/fail chance
  itself, it just attempts the call).
- **`ambusher`** — stays asleep/stationary until woken (via `check_wake`
  proximity or `noise_emitted`), then behaves as `chaser` once awake. The
  distinguishing trait vs. `chaser` is purely the wake-radius tuning in
  data (typically a much smaller passive wake radius, relying on
  `check_wake` triggering only at very close range) — no separate code path
  beyond "start asleep and don't patrol."
- **`patroller`** — walks a fixed route (`patrol_points` list in data,
  cycled in order) while asleep-equivalent-but-moving (`state` can be
  `"awake"` with no player target yet — track this as a sub-state via
  `home_position`/route index stored in `path_cache`/`path_cache_target`
  reuse, or add a dedicated field if that reuse reads as a hack in your
  implementation — either is acceptable, document your choice), switches to
  chasing the player once woken by proximity/noise, same as chaser from
  that point on.
- **`coward`** — flees (paths away from the player, maximizing distance)
  once its HP (`get_stat` from `01`) drops below `flee_hp_threshold`
  (fraction of max_hp, data-configured); never initiates melee; may still
  cast ranged/debuff spells while fleeing if data allows
  (`can_cast_while_fleeing: true`).

### 2.3 A* pathfinding

```python
def find_path(start: tuple[int, int], goal: tuple[int, int],
               is_passable: Callable[[tuple[int, int]], bool],
               world: World) -> list[tuple[int, int]] | None:
    """8-directional (cardinal + diagonal, equal cost per CONTRACTS.md §2.6),
    heuristic = chebyshev_distance(node, goal) (imported from
    engine.core.spatial_hash — never reimplemented locally). Passability is
    destination-tile-only, no corner-cutting check, matching foundation
    movement rules exactly. Returns None if no path exists (goal
    unreachable) — caller falls back to standing still / behavior-specific
    fallback, never raises.
    """
```

Cache per entity in `AIComponent.path_cache` / `path_cache_target`.
Invalidate (clear both fields) whenever:
- The cached `path_cache_target` no longer matches the current target
  position (e.g. player moved) — subscribe to `entity_moved` and
  `player_moved`, and on either, clear the cache for any monster whose
  cached target was the moved entity's old position.
- `floor_changed` fires — clear **all** AI path caches and reset transient
  state (see §2.4), since a new floor invalidates every cached path and
  every sleep/wake state should reset to data-defined defaults for the new
  floor's monster instances (existing floor's monsters are snapshotted by
  `06`'s FloorManager, not reset in place — this reset applies to whichever
  floor becomes active, i.e. the monsters that exist on it after
  load/restore).

### 2.4 Sleep/wake

- Monsters spawn with `AIComponent.state = "asleep"` by default (from data,
  see §5.1 — a monster's `ai.state` field seeds the initial value).
- Subscribe to `check_wake` (`entity_id, source_pos, radius` payload per
  CONTRACTS.md §3.2): for every asleep monster within `radius` of
  `source_pos` (Chebyshev), flip to `"awake"`.
- Subscribe to `noise_emitted` (`position, radius, source_id`): same
  wake check, using the monster's own position vs. `position`/`radius`.
  (Noise attenuation by `room_id` — spec §11 lesson 4 — is `06`'s
  worldgen's responsibility to populate; if `room_id` is absent on a tile
  this system falls back to pure Chebyshev radius, degrading gracefully
  per that lesson rather than crashing on a missing field.)
- Subscribe to `floor_changed`: reset every monster's `path_cache`,
  `path_cache_target`, `turns_in_current_state`, and `current_phase_index`
  to defaults; sleep state itself comes from each monster instance's own
  data/save state, not forced back to asleep unconditionally (a monster
  already woken and saved mid-fight before a snapshot/restore should stay
  awake on restore — this is a save-system concern `06`/foundation own via
  floor snapshot round-trip, not something this system re-decides).

## 3. Consumes

| Event | Used for |
|---|---|
| `check_wake` | wake gating |
| `noise_emitted` | wake gating |
| `entity_moved` | path cache invalidation |
| `player_moved` | path cache invalidation, chaser/coward target tracking |
| `floor_changed` | reset AI state/path cache (see §2.4) |

Calls into (not events — direct public-API calls per CONTRACTS.md §2.4,
same pattern `01`/`03`/`04` use with `EffectResolver`):
- `01-stats-combat.md`'s `CombatSystem.resolve_hit(attacker_id, defender_id, world, event_bus)`
- `01`'s `StatsSystem.get_stat` (for `flee_hp_threshold` checks, HP reads)
- `03-spells-status.md`'s `SpellSystem.cast(entity_id, spell_id, target)`
  (soft dependency — see §1.3)

Emits:
- `entity_moved` `{entity_id, from, to}` for every monster move.

## 4. File/Directory Ownership

You own, exclusively:
- `engine/systems/ai.py`
- `tests/unit/test_ai.py`
- `tests/fixtures/monsters/` (fixture monster JSON including at least one
  of each of the 4 behaviors and one `behavior_phases` boss fixture)

You add entries to:
- `engine/core/save.py:_COMPONENT_REGISTRY` — add `AIComponent`.

## 5. Data Schemas

### 5.1 Monster `ai` block (spec §4 baseline, extended here since no other
doc owns this shape)

```json
{
  "id": "goblin_shaman",
  "display_name": "Goblin Shaman",
  "stats": {"hp": 14, "max_hp": 14, "strength": 5, "dexterity": 6, "vision_range": 6},
  "ai": {
    "behavior": "chaser",
    "state": "asleep",
    "aggro_range": 8,
    "wake_radius": 5,
    "flee_hp_threshold": 0.2,
    "can_cast_while_fleeing": true,
    "patrol_points": [[10, 4], [10, 8], [14, 8], [14, 4]],
    "spells": ["firebolt", "minor_heal_self"],
    "behavior_phases": []
  },
  "xp_value": 25,
  "noise_radius": 4,
  "loot_table": {"entries": []}
}
```

Field notes:
- `behavior` — one of the 4 canonical values; validated at load time
  (reject/log-once on load if not one of the 4 — do not silently accept a
  typo'd fifth value as a new behavior).
- `state` — initial sleep state, `"asleep"` or `"awake"`; defaults to
  `"asleep"` if absent (default = zero cost, per foundation principle).
- `aggro_range` — Chebyshev radius at which an already-awake chaser
  actively pursues (distinct from `wake_radius`, which only matters while
  still asleep).
- `wake_radius` — used by `check_wake`/`noise_emitted` handling.
- `flee_hp_threshold` — fraction of max_hp; only meaningful for `coward`.
- `patrol_points` — list of `[x, y]` tile coords; only meaningful for
  `patroller`.
- `spells` — list of spell IDs (namespace `spells`, owned by `03`) this
  monster may cast; empty/absent = melee-only, no code path attempts a
  cast (guards the "absence = zero cost" rule — a spell-less monster never
  even checks whether `SpellSystem` is wired).
- `behavior_phases` — see §5.2; empty list = not a multi-phase boss, the
  monster's `behavior` field is used unconditionally forever.

### 5.2 `behavior_phases` schema (boss phase-swap — no other doc defines this,
committing to this shape here)

```json
"behavior_phases": [
  {
    "trigger": {"type": "hp_below", "value": 0.5},
    "behavior": "coward",
    "one_shot": true
  },
  {
    "trigger": {"type": "turn_count_above", "value": 20},
    "behavior": "chaser",
    "one_shot": false
  }
]
```

- Evaluated in list order, every turn, before dispatching to a behavior
  handler; the **first** entry whose `trigger` currently evaluates true
  becomes `AIComponent.current_phase_index` and its `behavior` overrides
  `ai.behavior` for that turn's decision. If no entry matches, fall back to
  the monster's base `ai.behavior`.
- `trigger.type` values: `"hp_below"` (fraction of max_hp, via
  `StatsSystem.get_stat`), `"hp_above"`, `"turn_count_above"` (compares
  against `AIComponent.turns_in_current_state`, incremented once per
  `take_turn` call), `"turn_count_below"`. Unknown trigger types are
  treated as never-true (skip, log once) — consistent with the project's
  "unknown = silently skipped" convention used by `EffectResolver`.
- `one_shot: true` — once triggered, this phase entry is removed from
  future evaluation for that entity (simulate via a per-entity "already
  fired" set, or by mutating a runtime copy of the phase list stored on
  `AIComponent` rather than the shared registry dict — **never mutate the
  dict returned by `registry.get()`**, since that's the shared, cached
  definition; copy before mutating). `one_shot: false` — re-evaluated every
  turn, can flip back and forth (e.g. an HP threshold phase that ends if
  healed back above it, if a later entry's trigger stops matching and an
  earlier one starts).
- No special-case boss code anywhere else in `ai.py` — a boss is simply a
  monster data file with a non-empty `behavior_phases` list; the dispatch
  logic is identical for every monster.

## 6. Lessons from v1 applied here

- **Combinatorial-simplicity bet** (4 behaviors × data variation) is an
  explicit v1 win (spec §11.5) — resist any temptation to add a 5th
  behavior or a special boss-only code path; `behavior_phases` swapping
  between the same 4 is the sanctioned way to get boss-like variety.
- **Chebyshev distance discipline**: every distance/heuristic/radius check
  in this file must go through `chebyshev_distance` or `SpatialHash`, never
  a hand-rolled `sqrt`/Euclidean calc or a second heuristic function.
- **Room/corridor `room_id` dependency (spec §11 lesson 4)**: this system
  depends on `room_id` per tile for noise attenuation, but must degrade
  gracefully (pure-radius fallback) if it's absent rather than crash — see
  §2.4. This is `06`'s and eventually `13`'s (map editor) responsibility to
  populate; document but don't attempt to fix it here.
- **"Built but not wired"**: don't let `take_turn` exist without
  `01`'s `process_monster_turns` loop actually calling it — coordinate via
  the documented call signature in §2, and cover it with the integration
  test in §8, not just unit tests of `find_path`/wake logic in isolation.

## 7. Definition of Done

- [ ] `AIComponent` implemented, registered in `_COMPONENT_REGISTRY`
      alphabetically.
- [ ] Exactly 4 behaviors implemented (`chaser`, `ambusher`, `patroller`,
      `coward`); load-time validation rejects/logs a 5th value rather than
      silently accepting it.
- [ ] Sleep/wake via `check_wake` and `noise_emitted` implemented and unit
      tested (in-radius wakes, out-of-radius doesn't).
- [ ] A* pathfinding implemented using `chebyshev_distance` as heuristic,
      8-directional, equal-cost diagonal, destination-tile-only passability
      — unit tested including a case where Euclidean and Chebyshev
      heuristics would produce a different path, proving Chebyshev is
      actually in effect (mirrors foundation's own test pattern).
- [ ] Path cache invalidation on `entity_moved`/`player_moved` and full
      reset on `floor_changed`, unit tested.
- [ ] `behavior_phases` evaluated in order, first-match-wins, `one_shot`
      handled without mutating the shared registry dict — unit tested with
      a 2-phase boss fixture (HP threshold + turn count).
- [ ] `get_awake_monster_ids` returns ascending entity-ID order.
- [ ] Monster melee path (`chaser`/`ambusher`/awakened `patroller` attacking
      via `combat.resolve_hit`) works end-to-end without `03` merged.
- [ ] Monster spellcasting call site exists, guarded so it no-ops cleanly
      if `spell_system` is `None` (constructor default) — does not raise,
      does not block PR merge on `03`.
- [ ] `entity_moved` emitted for every monster move with correct
      `{entity_id, from, to}` payload.
- [ ] `pytest` green.

## 8. Test Plan

Unit tests (`tests/unit/test_ai.py`):
- Each of the 4 behaviors' decision logic in isolation against a small
  fixture world (chaser paths toward player; ambusher stays put until
  woken; patroller cycles `patrol_points`; coward flees below threshold).
- Wake gating: `check_wake`/`noise_emitted` in-radius vs. out-of-radius.
- A* correctness including the Chebyshev-vs-Euclidean distinguishing case.
- Path cache invalidation on movement events and floor change.
- `behavior_phases` trigger evaluation (`hp_below`, `hp_above`,
  `turn_count_above`, `turn_count_below`, unknown-type-skipped, `one_shot`
  firing once).
- Load-time rejection of an invalid 5th `behavior` value.

Integration test (`tests/integration/test_ai_combat_pipeline.py` — required
per CONTRACTS.md §7.3):
- Build a real `World` + event bus + a real `01`-style `CombatSystem`
  instance (import it — `01` is Wave 1 alongside you; if genuinely unmerged
  when you write this, mock only the `events.emit`/`subscribe` boundary per
  CONTRACTS.md §8, not `CombatSystem` itself, and note in your PR that this
  test should be re-run against the real merged `CombatSystem` before
  Wave 3 sign-off).
- Place an awake `chaser` monster adjacent to a player-controlled entity
  fixture with `AIComponent`/`StatsComponent` set up, call
  `get_awake_monster_ids` then `take_turn` for it, and assert
  `combat.resolve_hit` was actually invoked (via a real hit/miss event
  observed on the bus — `damage_dealt` or `miss` fired) — not a mock
  assertion that `take_turn` "would have" attacked.
- A second case: place the same monster several tiles away from the
  player with obstacles between them, call `take_turn` across several
  simulated turns, and assert the monster's position actually advances
  toward the player tile-by-tile using the real `find_path`/movement path,
  emitting real `entity_moved` events each step.

## 9. Open Questions & Defaults

- **Patroller's "awake but not chasing" sub-state representation**: default
  is to reuse `AIComponent.path_cache`/`path_cache_target` to track patrol
  route progress (store the next patrol point as the cache target) rather
  than add a new field, to keep the component small; if your implementation
  finds this confusing in practice, add a dedicated `patrol_index: int`
  field instead — either is acceptable, just be internally consistent and
  document which you picked in your PR.
- **Monster spell target selection** (which enemy/tile a monster targets
  when casting): default to "the player entity" for `single_enemy`/
  `targeted_tile` modes (multi-monster-party targeting is not a v1/spec
  requirement) — pass the player's current position/id as `target` to
  `SpellSystem.cast` unconditionally until a real need for smarter targeting
  surfaces.
- **Depth-aware monster stat scaling** (from `01`'s `depth_multipliers`):
  this system reads final stats via `get_stat`, which already reflects any
  modifiers `01`/`04` applied — AI never applies its own scaling, so no
  open question here in practice, just noted for clarity.
