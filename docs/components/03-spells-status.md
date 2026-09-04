# Component 03 — Spells & Status Effects

**Wave:** 1 (parallel).
**Depends on:** Wave 0 (`00-foundation-core.md`) + `docs/CONTRACTS.md`.
Hard dependency on `01-stats-combat.md`'s `EffectResolver.apply_effect_list`
and `StatsSystem.get_stat` for effect resolution — see §1.1 for how to build
against the documented contract before `01` merges.

## 1. Scope & Boundaries

You are building `SpellSystem` (the targeting/casting state machine every
spell cast — player or monster — goes through) and `StatusEffectsSystem`
(duration tracking and per-tick effect application for buffs/debuffs/DoTs).

In scope:
- `SpellSystem`: IDLE/TARGETING state machine, five targeting modes, the
  MP → INT-gate → fail-chance pipeline, `backfire_effects` on fizzle, one
  pending cast at a time.
- `StatusEffectsSystem`: apply/tick/expire, unconditional per-instance
  stacking (same `effect_id` applied twice = two independent instances).
- The `SpellSystem.cast(entity_id, spell_id, target)` public entry point —
  **the single call site both player input (`07`/`08`) and monster AI
  (`02`) use.** This signature is itself a contract other Wave 1 components
  are coding against right now.

Out of scope:
- Actual effect execution (damage, heal, stat_modifier, etc.) — that's
  `01`'s `EffectResolver`. You build the spell/status *scaffolding* around
  it (targeting, gating, duration tracking) and call
  `EffectResolver.apply_effect_list` to actually do anything to the world.
  Do not reimplement any effect type here.
- AI's decision of *which* spell to cast and at *what* target — that's
  `02`. You only provide the `cast()` entry point it calls into.
- Rendering the targeting cursor overlay — that's `07`'s job, triggered by
  your `spell_cast_initiated` event; you emit the event and stop.

### 1.1 Building against `01` before it merges

Per CONTRACTS.md §8: write against the documented signature
(`apply_effect_list(effects, source_id, target_id, world, event_bus,
position=None)` and `get_stat(entity_id, stat_name, world)`) from
`01-stats-combat.md` §2. If `01` genuinely isn't merged when you write
tests, mock the `engine.core.events.emit`/`subscribe` boundary and a thin
stand-in for `apply_effect_list` that just records calls — do not import a
real `EffectResolver` that doesn't exist yet. Delete the mock only once the
integration test in §8 passes against the real merged `01`.

## 2. Provides

### 2.1 `SpellSystem`

```python
# engine/systems/spells.py

class SpellSystem:
    def __init__(self, world: World, event_bus: EventBus): ...

    def cast(self, entity_id: int, spell_id: str, target: dict | None) -> bool:
        """THE single entry point for initiating a spell cast — called by
        player input handling AND by 02's AISystem for monster casts.
        `target` shape depends on the spell's targeting_mode (see §5.1):
          - self / aoe_self: target is None (or ignored if provided).
          - single_enemy: {"entity_id": <int>}
          - targeted_tile / aoe_targeted: {"position": (x, y)}
        Returns False immediately (no state change) if: entity already has
        a pending cast (one pending cast at a time, system-wide per
        entity — see SpellCasterComponent below), spell_id not found in the
        `spells` registry namespace, or entity lacks enough MP for the
        spell's mp_cost (checked via get_stat before anything else happens
        — a failed MP check is not a "fizzle", it's a silent no-cast, no
        event emitted, matching "absence of resource = no effect" rather
        than a wasted-turn fizzle).
        For self/aoe_self (no further targeting needed): resolves
        immediately in this call (runs the full MP -> INT-gate ->
        fail-chance -> effect pipeline synchronously) and returns True.
        For single_enemy/targeted_tile/aoe_targeted: if target is already
        provided (e.g. AI already knows its target), resolves immediately,
        same as above. If target is None for a mode that requires one
        (typically true for the player-input path, which calls cast() with
        target=None to start targeting UI), transitions the caster to
        TARGETING state, emits spell_cast_initiated, and returns True
        (cast was successfully *initiated*, not yet *resolved* — resolution
        happens later via spell_target_chosen/target_confirmed).
        """

    def confirm_target(self, entity_id: int, target: dict) -> None:
        """Called by input/targeting handling (07/08) in response to
        spell_target_chosen or target_confirmed events (see §3) — resolves
        a pending TARGETING-state cast with the now-known target, running
        the full MP -> INT-gate -> fail-chance -> effect pipeline, then
        clears the caster back to IDLE."""

    def cancel_cast(self, entity_id: int) -> None:
        """Called in response to cancel_targeting — clears pending cast
        back to IDLE, refunds nothing (MP is only spent on actual
        resolution, not on entering TARGETING), emits spell_cast_cancelled."""
```

`SpellCasterComponent` (new component, registered in `_COMPONENT_REGISTRY`):

```python
@component
@dataclass
class SpellCasterComponent:
    state: str = "IDLE"                    # "IDLE" | "TARGETING"
    pending_spell_id: str | None = None
    known_spells: list[str] = field(default_factory=list)
```

**Resolution pipeline (both the immediate-resolve and confirm_target paths
funnel through one private helper, `_resolve_cast(entity_id, spell_id,
target)`, so the two call sites never drift):**

1. MP check: `get_stat(entity_id, "mp", world) >= spell["mp_cost"]`. If
   false at the immediate-resolve point, abort silently (already checked
   earlier in `cast()`, but re-checked here defensively in case MP changed
   between initiation and confirmation — e.g. a DoT ticked in between).
   Deduct MP via a direct component write (MP current-value bookkeeping is
   not a derived stat under Option C modifiers — it's a resource pool field
   — so this is the one sanctioned place spell casting writes a pool value
   directly rather than through `add_modifier`; document this distinction
   clearly since it could otherwise look like a violation of "base fields
   never mutated directly": that rule is about *derived* stats like
   strength/armor, not consumable resource pools like current HP/MP, which
   both `01`'s damage/heal effects and this system's MP deduction write
   directly by design).
2. INT-gate: if `spell["int_requirement"]` is set and
   `get_stat(entity_id, "intelligence", world) < int_requirement`, the cast
   fails outright (no fail-chance roll, no backfire) — emit `message`
   (optional) and return; MP is still spent (attempting beyond your means
   costs the resource, matching classic roguelike conventions and giving
   INT-gating real weight).
3. Fail-chance roll: `eval_formula(spell["fail_chance_formula"], context)`
   (context includes `INT` at minimum; formula owns the rest) determines a
   probability; roll against it. On fail (fizzle): resolve
   `spell["backfire_effects"]` (a normal effect list) via
   `apply_effect_list` instead of the spell's real effects, targeting the
   caster itself unless a backfire effect explicitly targets otherwise.
   On success: resolve `spell["effects"]` via `apply_effect_list` against
   the real target(s).
4. AoE modes (`aoe_self`, `aoe_targeted`) resolve the effect list once per
   entity found within `spell["aoe_radius"]` (Chebyshev, via SpatialHash
   `query_radius`) of the appropriate center (caster's own position for
   `aoe_self`, the confirmed target position for `aoe_targeted`).

### 2.2 `StatusEffectsSystem`

```python
# engine/systems/status.py

class StatusEffectsSystem:
    def __init__(self, world: World, event_bus: EventBus): ...

    def apply(self, entity_id: int, effect_id: str, duration: int,
              magnitude: float | None, world: World, event_bus: EventBus) -> str:
        """Called DIRECTLY (not event-subscribed) by 01's EffectResolver
        (its apply_status effect type) and by anything else that needs to
        apply a status. Creates a brand-new independent instance_id (uuid4
        hex or an incrementing counter — implementation's choice, must be
        unique per call) even if effect_id matches an already-active
        instance on this entity — stacking is UNCONDITIONAL by instance,
        never merged/refreshed. Adds a StatusInstance to the entity's
        StatusEffectsComponent, applies the status definition's own effect
        list once immediately if it defines on_apply effects (see §5.2),
        registers any stat modifiers it carries via 01's
        StatsSystem.add_modifier with tag `{instance_id}:{stat}`. Emits
        status_applied {entity_id, effect_id, instance_id, duration}.
        Returns the new instance_id.
        """

    def tick(self, world: World, event_bus: EventBus) -> None:
        """Called directly by the main loop once per game turn (not
        event-subscribed — ticking must happen exactly once per turn in a
        well-defined place, not fan out to however many turn-boundary
        events happen to exist). For every active StatusInstance across
        every entity: decrement remaining duration by 1; if the status
        definition has on_tick effects, resolve them via
        01.apply_effect_list against the owning entity; if remaining
        duration reaches 0, remove the instance, call
        StatsSystem.remove_modifiers_by_tag_prefix(entity_id, f"{instance_id}:")
        to clear any stat modifiers it registered, and emit status_expired
        {entity_id, effect_id, instance_id}.
        """
```

`StatusEffectsComponent` (new component, registered in
`_COMPONENT_REGISTRY`):

```python
@component
@dataclass
class StatusEffectsComponent:
    instances: dict[str, "StatusInstance"] = field(default_factory=dict)
    # keyed by instance_id

@dataclass
class StatusInstance:
    effect_id: str
    instance_id: str
    remaining_duration: int
    magnitude: float | None
```

## 3. Consumes

| Event | Used for |
|---|---|
| `spell_chosen` | player UI selecting which spell to attempt (`entity_id, spell_id` payload) → calls `cast(entity_id, spell_id, target=None)` |
| `spell_target_chosen` | targeting UI reporting a chosen target mid-flow (`entity_id, spell_id, target`) → calls `confirm_target` |
| `target_confirmed` | alternate/final confirmation step from targeting UI (`target` only, per CONTRACTS.md's table — entity_id is tracked internally on the pending caster since only one entity can be mid-targeting through the player's input context at a time; if multiple simultaneous targeting sessions across entities are ever needed, that's a design change tracked as an Open Question, not assumed here) → calls `confirm_target` |
| `cancel_targeting` | (`entity_id`) → calls `cancel_cast` |

Calls into (direct, not events):
- `01`'s `EffectResolver.apply_effect_list` for all effect resolution.
- `01`'s `StatsSystem.get_stat` / `add_modifier` / `remove_modifiers_by_tag_prefix`.

**Clarifying the input-vs-spell-system emission split (per task brief,
cross-referencing `07`/`08`):** `spell_cast_initiated` is emitted by
*this* system (`SpellSystem.cast`, when transitioning to TARGETING).
`spell_target_chosen`/`target_confirmed`/`cancel_targeting` are emitted by
*input/UI handling* (`07`/`08`) in response to the player clicking a tile,
pressing confirm, or pressing cancel while the targeting cursor overlay
(triggered by `spell_cast_initiated`, per spec §6) is active — this system
only *consumes* those three, it never emits them. `spell_cast_cancelled`
is emitted by this system in response to consuming `cancel_targeting`.

## 4. File/Directory Ownership

You own, exclusively:
- `engine/systems/spells.py`
- `engine/systems/status.py`
- `tests/unit/test_spells.py`, `test_status.py`
- `tests/fixtures/spells/`, `tests/fixtures/effects_status/` (fixture
  spell/status JSON)
- `data/spells/` — enough real fixture spells to exercise all 5 targeting
  modes and a backfire case (content volume beyond that is out of scope
  per spec §12).
- `data/effects/` entries for status definitions your fixtures reference
  (coordinate naming with `01` if you both touch `data/effects/` — additive
  only, never edit another component's files there).

You add entries to:
- `engine/core/save.py:_COMPONENT_REGISTRY` — add `SpellCasterComponent`,
  `StatusEffectsComponent`, alphabetically.

## 5. Data Schemas

### 5.1 Spell JSON schema (spec doesn't give a full example — committing to
this shape, consistent with `01`'s effect list format)

```json
{
  "id": "firebolt",
  "display_name": "Firebolt",
  "targeting_mode": "single_enemy",
  "mp_cost": 6,
  "int_requirement": 3,
  "fail_chance_formula": "max(0.0, 0.15 - (INT * 0.01))",
  "aoe_radius": 0,
  "backfire_effects": [
    {"type": "damage", "amount": "1d4", "damage_type": "fire"},
    {"type": "noise", "radius": 6}
  ],
  "effects": [
    {"type": "damage", "amount": "2d6 + (INT * 0.4)", "damage_type": "fire"},
    {"type": "vfx", "vfx_id": "fire_impact"}
  ]
}
```

Field notes:
- `targeting_mode` — one of `self`, `aoe_self`, `single_enemy`,
  `targeted_tile`, `aoe_targeted`. Validated at load; unknown value is a
  load-time error (unlike effect types, targeting modes are a small closed
  set core to the state machine, not an additive dispatch table — treat a
  typo'd mode as a content bug worth failing loudly on, not skipping).
- `mp_cost` — flat number (formula strings are supported here too if a
  system wants scaling costs, evaluated the same way as any other formula
  field — but a plain number is valid JSON and treated as a formula that
  evaluates to itself).
- `int_requirement` — omit or `0` for no gate.
- `fail_chance_formula` — evaluated via `eval_formula`, context includes at
  minimum `INT` (per CONTRACTS.md §6, the caster's derived stats
  uppercased).
- `aoe_radius` — `0`/absent for non-AoE modes; required (`> 0`) for
  `aoe_self`/`aoe_targeted`.
- `backfire_effects` — normal effect list (`01`'s schema), resolved instead
  of `effects` on a failed fail-chance roll. Omit/empty list = a fizzle
  simply does nothing beyond consuming MP.
- `effects` — normal effect list (`01`'s schema, see `01-stats-combat.md`
  §5.4), resolved on success.

### 5.2 Status effect definition (lives in the `effects` namespace alongside
`01`'s effect fixtures — a status is just an `effects/` JSON entry with
`on_apply`/`on_tick` lists instead of being an inline effect dict)

```json
{
  "id": "poisoned",
  "display_name": "Poisoned",
  "on_apply": [],
  "on_tick": [
    {"type": "damage", "amount": "1d4", "damage_type": "poison"}
  ],
  "stat_modifiers": [
    {"stat": "dexterity", "operation": "add", "value": -2}
  ]
}
```

- `on_apply` — effect list resolved once, immediately, when `apply()` is
  called (e.g. an instant burst on application).
- `on_tick` — effect list resolved once per remaining turn of duration, via
  `StatusEffectsSystem.tick`.
- `stat_modifiers` — same `operation` vocabulary as `01`'s item schema
  (`add`/`multiply`); registered via `StatsSystem.add_modifier` with tag
  `{instance_id}:{stat}` on apply, cleared by prefix on expiry (§2.2).

## 6. Lessons from v1 applied here

- **"Everything is events" for UI-facing waits**: `spell_cast_initiated`
  exists specifically so the targeting cursor overlay is a pure listener
  with zero coupling back into this system beyond the three consumed
  events — don't add a direct call from this system into any rendering
  code.
- **`_pending` naming convention exception is not for you**: none of this
  component's events represent an async UI wait in the `level_up_pending`
  sense (targeting is a state machine, not a "waiting for UI to catch up"
  signal) — don't rename anything to `_pending` that isn't already in
  CONTRACTS.md's table.
- **Unconditional per-instance stacking** was a deliberate design choice
  (spec §3) — don't "improve" it into deduplicated/refreshed stacking; two
  `poisoned` instances on one entity tick and expire completely
  independently, by design.
- **"Built but not wired"**: `StatusEffectsSystem.tick()` must actually be
  called once per turn by the main loop — since `engine/main.py` is only a
  minimal skeleton from `00`, note explicitly in your PR the exact call
  site you expect (`status_system.tick(world, event_bus)` inside whatever
  becomes the turn-resolution step) so Wave 3 integration verification can
  confirm it's actually wired once more of the loop exists.

## 7. Definition of Done

- [ ] `SpellCasterComponent` / `StatusEffectsComponent` implemented,
      registered in `_COMPONENT_REGISTRY` alphabetically.
- [ ] `SpellSystem.cast` implements all 5 targeting modes; immediate-resolve
      path (self/aoe_self, and single_enemy/targeted_tile/aoe_targeted when
      target is pre-supplied) and TARGETING-transition path both tested.
- [ ] One-pending-cast-at-a-time enforced: a second `cast()` call while
      already TARGETING returns `False` without side effects, tested.
- [ ] MP → INT-gate → fail-chance pipeline implemented in that exact order,
      each stage independently tested (insufficient MP silently no-casts;
      INT gate fails outright with MP still spent; fail-chance roll
      resolves `backfire_effects` on fizzle, real `effects` on success).
- [ ] AoE resolution (`aoe_self`, `aoe_targeted`) correctly enumerates
      targets via `SpatialHash.query_radius` (Chebyshev), not a hand-rolled
      distance loop.
- [ ] `StatusEffectsSystem.apply` creates independent instances for
      repeated `apply()` calls with the same `effect_id` — tested with two
      simultaneous `poisoned` instances ticking/expiring independently
      (different durations, one expires before the other).
- [ ] `tick()` correctly resolves `on_tick` effects, decrements duration,
      expires and clears stat modifiers by `{instance_id}:` prefix at zero.
- [ ] Every event this component emits (`spell_cast_initiated`,
      `spell_cast_cancelled`, `status_applied`, `status_expired`) matches
      CONTRACTS.md §3.2 payload shapes exactly.
- [ ] `pytest` green.

## 8. Test Plan

Unit tests (`tests/unit/test_spells.py`, `test_status.py`):
- Each of the 5 targeting modes, immediate and deferred-target paths.
- MP/INT-gate/fail-chance stage isolation (mock the RNG/formula roll to
  force each branch deterministically).
- Backfire effect resolution on forced fizzle.
- One-pending-cast-at-a-time rejection.
- `cancel_cast` clears TARGETING state and emits `spell_cast_cancelled`.
- Status stacking: two instances of the same `effect_id`, independently
  ticking/expiring, correct `status_expired` payloads for each.
- Stat modifier cleanup on status expiry (modifier gone from
  `StatsComponent.modifiers` after `tick()` expires the instance).

Integration test (`tests/integration/test_spell_cast_pipeline.py` —
required per CONTRACTS.md §7.3):
- Build a real `World` + event bus + real `01` `StatsSystem`/`EffectResolver`
  instances (or, if `01` genuinely unmerged at write time, the documented
  fallback per §1.1 — re-run against the real thing before Wave 3
  sign-off, noted in your PR) + a caster entity with `SpellCasterComponent`
  + a target entity with `StatsComponent`.
- Call `spell_system.cast(caster_id, "firebolt", {"entity_id": target_id})`
  directly (real entry point, immediate-resolve path since target is
  pre-supplied) and assert: MP was actually deducted on the caster's real
  component, `damage_dealt` fired with the fire damage type, target's HP
  actually decreased (read back via `get_stat`), matching `01`'s real
  pipeline end-to-end rather than a mocked effect call.
- A second integration case exercising the deferred-target path: call
  `cast(caster_id, "some_targeted_tile_spell", target=None)`, assert
  `spell_cast_initiated` fired and state is `TARGETING`, then simulate the
  input layer by calling `confirm_target(caster_id, {"position": (x, y)})`
  directly, and assert the effect actually resolved against that position
  (e.g. an AoE noise effect reaching entities near that tile) and state
  returned to `IDLE`.
- A third case applying a status via `01`'s real `apply_status` effect type
  end-to-end: resolve a spell whose `effects` includes
  `{"type": "apply_status", "effect_id": "poisoned", "duration": 3}`
  through the real `EffectResolver`, and assert `StatusEffectsSystem.apply`
  was actually invoked (a real `StatusInstance` exists on the target
  afterward, not just that `status_applied` fired) — proving the
  `01` → `03` call-boundary described in `01-stats-combat.md` §2.2 actually
  connects.

## 9. Open Questions & Defaults

- **`target_confirmed`'s missing `entity_id`** (CONTRACTS.md's table lists
  its payload as `target` only): default assumption is a single
  globally-tracked "currently targeting" entity per input context (the
  player, in practice — AI never goes through this event path since it
  supplies `target` directly to `cast()`), tracked internally by whichever
  entity has `SpellCasterComponent.state == "TARGETING"` and is also the
  input-controlled entity. If a future need arises for multiple
  simultaneously-targeting entities through the UI event path, that's a
  CONTRACTS.md payload change, not something to improvise here — flag it
  if you hit a real conflict.
- **MP as a direct-write resource pool vs. Option C modifiers**: resolved
  explicitly in §2.1 step 1 — MP/HP current values are pool fields written
  directly (by combat damage, healing, and spell MP cost), while max_mp/
  max_hp and all other derived stats go through `get_stat`/modifiers. This
  mirrors how `01`'s damage/heal effects already work; documented here so
  it isn't independently rediscovered as an apparent rule violation.
- **AoE friendly fire**: default is AoE effects apply to every entity in
  radius indiscriminately (no faction/ally filtering) — the spec doesn't
  define factions, and monsters/player are the only entities with combat
  stats currently in scope. If a faction system is added later, that's a
  CONTRACTS.md-level addition, not assumed here.
