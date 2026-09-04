# Component 05 — Progression & Vision

**Wave:** 1 (parallel).
**Depends on:** Wave 0 (`00-foundation-core.md`) + `docs/CONTRACTS.md`.
Soft dependency on `01-stats-combat.md`'s `death` event and `get_stat` (for
XP/level math) and on `00`'s `chebyshev_distance`/`SpatialHash` (for
vision) — both documented interfaces, no code import required to build.

## 1. Scope & Boundaries

You are building `ProgressionSystem` (XP, leveling, stat/spell rewards) and
`VisionSystem` (lit-room and personal-radius tile visibility).

In scope:
- `ProgressionSystem`: awards XP on `death`, deduplicated per killed entity
  so XP is never double-awarded even if `death` somehow fires more than
  once for the same kill; level-up thresholds with a repeating tail past
  the last explicitly-defined entry; automatic stat gains + bonus points +
  a 3-option random spell choice per level-up.
- `VisionSystem`: room-wide lighting for rooms flagged lit, personal
  `vision_range` Chebyshev-radius vision otherwise, permanent reveal of any
  tile once seen (fog-of-war style memory, not re-hidden), deliberately no
  shadow-casting/line-of-sight — simple and cheap by design, not a
  simplification you should "improve."
- A clearly-marked, intentionally inert **trap detection stub** — see
  §1.1. This is not a bug or an oversight; it is scoped exactly this way on
  purpose, per spec §12.

### 1.1 Trap detection — intentionally incomplete, per spec §12

Spec §11 (known gaps) and §12 (explicitly out of scope) are unambiguous:
trap detection was **designed but never finished** in v1 — `trap_detect_radius`
is a real derived stat (owned by `01`'s stat system, you just read it), and
v1's `VisionSystem` had a scan loop stubbed in, but **no `TrapComponent` and
no trigger system ever existed.** Spec §12 carries this forward as a
deliberate deferral for the v1 rebuild too — it is explicitly **not this
component's job to finish it.**

What you build: a scan-loop **placeholder** in `VisionSystem` that:
- Reads `trap_detect_radius` via `01`'s `get_stat` for the player (or any
  entity that has it — deriving from data, e.g. a `perception`/`wisdom`
  stat formula) as a real, non-fake number.
- Would emit `trap_revealed` `{position, trap_id}` for any `TrapComponent`
  found within that radius — **but since `TrapComponent` does not exist in
  this codebase**, the loop's query
  (`world.query(TrapComponent)` or equivalent) naturally returns nothing,
  and the function is correctly a no-op forever until some future component
  introduces `TrapComponent`. Do not fabricate a `TrapComponent` here to
  make the loop "do something" — that would be building the trap system,
  which is explicitly out of scope.
- Is called from the same per-turn/per-move hook `VisionSystem` already
  uses for normal vision updates, so the hook point exists and is
  exercised (a unit test proves it's called and safely no-ops), without
  requiring you to build anything the trap system would need.

Document this in code with a comment block pointing back to spec §12 and
this doc, so nobody mistakes the no-op for a bug in a later review pass.

Out of scope:
- Trap component/trigger system itself (spec §12 deferral).
- Shadow-casting / true line-of-sight vision (deliberately simple per spec
  §3 — Chebyshev radius + lit-room only).
- Combat/XP-value sourcing logic beyond reading the `xp_value` field off
  the `death` event payload (owned by `01`).

## 2. Provides

### 2.1 `ProgressionSystem`

```python
# engine/systems/progression.py

class ProgressionSystem:
    def __init__(self, world: World, event_bus: EventBus): ...
    # subscribes to death, spell_chosen, stat_allocated in __init__

    def _on_death(self, payload: dict) -> None:
        """payload: {entity_id, killer_id, xp_value}. Awards xp_value to
        killer_id — but ONLY if this exact (entity_id, killer_id) death
        hasn't already been processed. Dedup key is the dying entity_id: a
        ProgressionSystem-internal set of "already-awarded" dying entity
        IDs (or a per-entity flag on the corpse's data before destruction —
        implementation's choice) ensures XP is awarded exactly once even if
        `death` fires more than once for the same kill (v1 had exactly this
        double-award risk called out in the spec). After the dedup check
        passes, adds xp_value to killer's XpComponent.current_xp, then
        checks for level-up (possibly multiple level-ups in one award if
        the XP gain crosses more than one threshold) — see level_up loop
        below."""

    def _check_level_up(self, entity_id: int, world: World, event_bus: EventBus) -> None:
        """Loops (not a single if) since one large XP award can cross
        multiple thresholds: while current_xp >= xp_required_for(next_level):
        increment level, apply automatic stat gains (data-driven, see §5.1),
        grant bonus_points (data-driven amount), emit level_up_pending
        {entity_id}, roll 3 random spell options eligible at this level
        (respecting any level/class gating in spell data, if present) and
        emit spell_choice_pending {entity_id, options}, and emit
        bonus_points_remaining {entity_id, remaining} reflecting the
        entity's total unspent bonus points after this level's grant."""

    def _on_spell_chosen(self, payload: dict) -> None:
        """payload: {entity_id, spell_id} — consumes the UI's response to
        spell_choice_pending. Grants the spell (adds to the entity's known
        spells — via 03's SpellCasterComponent.known_spells if 03 is
        available, else records it in a local pending-grants structure
        until 03 merges, per CONTRACTS.md §8 stubbing). Emits spell_learned
        {entity_id, spell_id}."""

    def _on_stat_allocated(self, payload: dict) -> None:
        """payload: {entity_id, stat, amount} — consumes a bonus-point
        spend request from UI. Validates the entity has >= amount unspent
        bonus points; if so, decrements the pool and calls 01's
        StatsSystem.add_modifier(entity_id, tag=f"levelup_bonus_{stat}_{n}",
        stat=stat, op="add", value=amount) — bonus points are permanent
        stat increases, applied the same way any other permanent modifier
        is (never a direct base-field mutation, consistent with
        CONTRACTS.md §2.9). Emits bonus_points_remaining with the updated
        count."""
```

`XpComponent` (new component, registered in `_COMPONENT_REGISTRY`):

```python
@component
@dataclass
class XpComponent:
    current_xp: int = 0
    level: int = 1
    unspent_bonus_points: int = 0
    awarded_death_ids: set[int] = field(default_factory=set)  # dedup tracking
```

### 2.2 `VisionSystem`

```python
# engine/systems/vision.py

class VisionSystem:
    def __init__(self, world: World, event_bus: EventBus): ...
    # subscribes to player_moved, floor_changed in __init__

    def update_visibility(self, entity_id: int, world: World) -> set[tuple[int, int]]:
        """Called on player_moved (and once on floor_changed / entity
        spawn) for the vision-holding entity (normally the player).
        Computes the visible tile set as the UNION of:
          1. every tile in the entity's current room, if the room is
             flagged `lit: true` in the floor's room data (populated by 06
             per room_id — see 06-worldgen-campaign.md), and
          2. every tile within get_stat(entity_id, "vision_range", world)
             Chebyshev distance of the entity's position, via
             SpatialHash-based tile iteration (no shadow-casting — a flat
             disc, deliberately).
        Every tile in the resulting set is marked permanently 'seen' in the
        floor's visibility record (once seen, always rendered from memory
        even outside current visibility — this system tracks 'seen' and
        'currently visible' as two separate sets, both exposed via
        get_visible_tiles/get_seen_tiles below, since the renderer (07)
        needs both: currently-visible tiles render at full brightness,
        seen-but-not-visible tiles render dimmed/memory-style).
        """

    def get_visible_tiles(self, floor_id: str) -> set[tuple[int, int]]: ...
    def get_seen_tiles(self, floor_id: str) -> set[tuple[int, int]]: ...

    def scan_for_traps(self, entity_id: int, world: World, event_bus: EventBus) -> None:
        """INTENTIONAL STUB — see §1.1. Reads trap_detect_radius via
        01.get_stat (real number, real formula), queries
        world.query(TrapComponent) within that radius via SpatialHash — a
        query against a component type that does not exist anywhere in
        this codebase, so this always returns an empty result and the
        function always no-ops. Would emit trap_revealed {position,
        trap_id} per found trap if TrapComponent ever existed. Called from
        the same hook as update_visibility so the call site exists and is
        exercised, without building any part of the trap system itself.
        DO NOT implement TrapComponent here — that's spec §12's deferred
        scope, not this component's.
        """
```

## 3. Consumes

| Event | Used for |
|---|---|
| `death` | XP award (from `01`, deduplicated per dying entity) |
| `spell_chosen` | consumes the level-up spell pick (from UI, same event `03`'s SpellSystem also consumes for normal casting — see note below) |
| `stat_allocated` | consumes a bonus-point spend request (from UI) |
| `player_moved` | vision recompute |
| `floor_changed` | vision reset for the new floor (seen-tiles memory is per-floor and should already be tracked/restored via `06`'s floor snapshot mechanism — this system just recomputes current visibility fresh on arrival) |

**Note on `spell_chosen` being consumed by two components:** `03`'s
`SpellSystem` also consumes `spell_chosen` for normal in-combat spell
selection (its payload there is the same shape,
`{entity_id, spell_id}`, but semantically means "cast this spell now").
This component's use is for the level-up spell-choice reward flow. Both
subscriptions are legitimate simultaneous consumers of the same event name
per the event bus's normal pub/sub semantics (CONTRACTS.md §3 — multiple
handlers, subscription order, no conflict) — **but** this is worth a
project-level flag: if in practice a single `spell_chosen` firing could
ever be ambiguous between "the player is casting a known spell" and "the
player just picked a level-up reward spell," that ambiguity should be
resolved via distinct event names or a payload discriminator field at the
CONTRACTS.md level, not silently in code. Document this exact concern in
your PR as an escalation candidate per ORCHESTRATION.md §5 rather than
resolving it unilaterally — the recommended default (do this unless told
otherwise) is that UI (`08`) is responsible for firing `spell_chosen` only
in the casting context and a distinctly-named event for the level-up
reward pick; if `08`'s doc doesn't already establish that distinction,
flag it there too.

Emits:
- `level_up_pending` `{entity_id}`
- `spell_choice_pending` `{entity_id, options}`
- `bonus_points_remaining` `{entity_id, remaining}`
- `spell_learned` `{entity_id, spell_id}`
- `trap_revealed` `{position, trap_id}` — documented as inert/deferred per
  §1.1; will never actually fire until a future component adds
  `TrapComponent`.

## 4. File/Directory Ownership

You own, exclusively:
- `engine/systems/progression.py`, `vision.py`
- `tests/unit/test_progression.py`, `test_vision.py`
- `tests/fixtures/progression/` (level threshold fixture config)
- `data/config/progression.json` (level thresholds, stat gains, bonus
  point amounts — see §5.1)

You add entries to:
- `engine/core/save.py:_COMPONENT_REGISTRY` — add `XpComponent`,
  alphabetically. (No new vision component needed if visibility state is
  stored per-floor in a plain dict the system owns internally rather than
  as an ECS component — see Open Questions §9 for whether that state needs
  to be saved/restored via the floor snapshot mechanism, which it does:
  document your chosen representation, e.g. `VisibilityComponent` attached
  to the floor's meta-entity or a dict keyed by floor_id living in save
  state, and register it if it's a component.)

## 5. Data Schemas

### 5.1 `data/config/progression.json` (you own this file)

```json
{
  "schema_version": 1,
  "xp_thresholds": [0, 100, 250, 450, 700, 1000],
  "repeating_tail_increment": 400,
  "stat_gains_per_level": {"max_hp": 8, "max_mp": 4, "strength": 1},
  "bonus_points_per_level": 2,
  "spell_choice_options_count": 3
}
```

- `xp_thresholds` — index `i` is the cumulative XP required to reach level
  `i + 2` (index 0 = XP to reach level 2, since level 1 starts at 0 XP).
  Explicitly-defined entries stop at whatever length the content author
  provides (6 in this example, so level 7 is the first "tail" level).
- `repeating_tail_increment` — once `current_level >= len(xp_thresholds) + 1`,
  each further level's threshold is the previous threshold plus this fixed
  increment, forever — this is the "repeating tail past the last defined
  entry" the task brief calls for, so designers never have to author an
  infinite table.
- `stat_gains_per_level` — flat automatic per-level stat increases, applied
  via `add_modifier` (tag `levelup_auto_{stat}_{level}`) exactly like bonus
  points, just not player-chosen.
- `bonus_points_per_level` — points added to `unspent_bonus_points` each
  level, spent later via `stat_allocated`.
- `spell_choice_options_count` — how many random eligible spells to offer
  per level-up (3, per spec).

### 5.2 Vision-relevant room data (read, not owned — populated by `06`)

```json
{"room_id": "r_04", "lit": true, "tiles": [[10,4], [10,5], [11,4], [11,5]]}
```

`VisionSystem` reads this shape off whatever room/tile structure `06`'s
worldgen populates on the floor (`room_id` per tile, `lit` flag per room —
per CONTRACTS.md/spec §11 lesson 4, `room_id` is a hard requirement on
every generated floor; if a handcrafted floor is missing it,
`VisionSystem` must degrade to personal-radius-only for that floor rather
than crash — mirror `02`'s AI noise-attenuation degradation pattern
exactly, same underlying gap, same graceful-fallback philosophy).

## 6. Lessons from v1 applied here

- **XP double-award risk** — spec doesn't call this out as an actual v1
  bug, but the task brief is explicit that `death` firing more than once
  for the same kill must never double-award XP; the `awarded_death_ids`
  dedup set (§2.1) is a hard requirement, not a nice-to-have, and must be
  unit tested with an explicit double-fire case.
- **Trap detection was designed but never finished (spec §11/§12)** — this
  is the one item in this whole doc that is deliberately incomplete by
  design, not an oversight to "helpfully" fix. Building `TrapComponent`
  here would violate ORCHESTRATION.md §6's rule that explicitly-deferred
  gaps "remain deferred — they were carried forward on purpose, not
  dropped by omission." Resist the temptation.
- **Shadow-casting was deliberately never built** (spec §3/§11.5) — a
  cheap Chebyshev-radius + lit-room model was a considered simplicity
  choice, not a corner cut; don't "improve" it into real line-of-sight.
- **`room_id` cross-cutting gap (spec §11 lesson 4)** — same graceful
  degradation requirement as `02`'s AI noise attenuation; both systems
  share this failure mode and both must degrade the same way (pure-radius
  fallback) rather than crash on a handcrafted map missing `room_id`.

## 7. Definition of Done

- [ ] `XpComponent` implemented, registered in `_COMPONENT_REGISTRY`
      alphabetically.
- [ ] `ProgressionSystem` awards XP on `death`, deduplicated — unit tested
      with a double-fired `death` payload for the same dying entity_id
      asserting XP is added only once.
- [ ] Level-up threshold lookup correctly uses the repeating tail once past
      the last explicitly-defined threshold — unit tested with a level
      beyond the defined table.
- [ ] A single large XP award correctly triggers multiple sequential
      level-ups in one call (looped, not single-if) — unit tested.
- [ ] Automatic stat gains, bonus points, and a 3-option spell choice are
      all applied/emitted on every level-up — unit tested.
- [ ] `stat_allocated` correctly spends bonus points via `add_modifier`
      (never a direct base-field write) and rejects overspend — unit
      tested.
- [ ] `VisionSystem.update_visibility` correctly unions lit-room tiles and
      personal Chebyshev-radius tiles, and tracks 'seen' as a permanent
      superset of 'currently visible' — unit tested including a case where
      the entity moves away and the previously-visible tile remains in
      `get_seen_tiles` but drops out of `get_visible_tiles`.
- [ ] `VisionSystem` degrades gracefully (personal-radius-only) on a
      fixture floor with no `room_id`/`lit` data — unit tested, doesn't
      raise.
- [ ] `scan_for_traps` exists, is called from the same hook as
      `update_visibility`, reads a real `trap_detect_radius` stat value,
      and safely no-ops (no `TrapComponent` exists) — unit tested that it
      runs without error and emits no `trap_revealed` events, with a code
      comment pointing to spec §12 explaining why.
- [ ] `pytest` green.

## 8. Test Plan

Unit tests (`tests/unit/test_progression.py`, `test_vision.py`): see each
Definition of Done bullet above.

Integration test (`tests/integration/test_progression_pipeline.py` —
required per CONTRACTS.md §7.3):
- Build a real `World` + event bus + a real (or `01`-boundary-mocked per
  CONTRACTS.md §8, re-verified once `01` merges) monster-kill scenario:
  spawn a killer entity with `XpComponent`, emit a real `death` payload
  with an `xp_value` large enough to cross two level thresholds at once,
  and assert: `current_xp`/`level` updated correctly, `level_up_pending`
  fired twice (once per crossed level), `spell_choice_pending` fired twice
  with real 3-option payloads drawn from a fixture `spells` registry (not
  a mocked options list), and `bonus_points_remaining` reflects the
  cumulative total after both level-ups.
- A second integration case for `VisionSystem`: build a small fixture
  floor with a lit room and a corridor outside it, place the vision-holder
  entity first inside the lit room (assert the whole room is visible
  regardless of distance to far corners) then move it into the corridor
  (assert only the Chebyshev-radius disc around it is now visible, and the
  lit room's tiles are in `get_seen_tiles` but not `get_visible_tiles`) —
  driven through the real `player_moved` event, not a direct internal call
  bypassing the subscription.

## 9. Open Questions & Defaults

- **`spell_chosen` ambiguity between casting and level-up reward** (§3) —
  flagged explicitly above as an escalation candidate per
  ORCHESTRATION.md §5; default behavior in the absence of a resolution:
  this system's handler only acts on a `spell_chosen` payload when the
  entity actually has a `spell_choice_pending` outstanding for it (track a
  small per-entity "awaiting choice" flag set when `spell_choice_pending`
  is emitted, cleared when consumed) — so an in-combat cast doesn't
  accidentally get misread as a level-up pick, and vice versa, without
  needing a CONTRACTS.md change. If `08`'s UI doc introduces a distinct
  event name instead, prefer that and drop this workaround.
- **Visibility state persistence representation** — default: a
  `VisibilityComponent` (per-floor `seen: set[tuple[int,int]]`) attached to
  a floor-scoped entity or stored in the floor snapshot's top-level dict
  (coordinate the exact save-system hook with `06`/foundation's
  `serialize_floor_snapshot`); register as a component if you go the
  entity-attached route. Either is acceptable — document your choice in
  your PR since `06` and `07` (renderer) both read this state.
- **Spell eligibility filtering for the 3-option level-up choice** (e.g.
  class/level gating) — default: no filtering beyond "spell exists in the
  registry and entity doesn't already know it," since the spec doesn't
  define classes. Revisit only if a future component introduces class
  gating explicitly.
