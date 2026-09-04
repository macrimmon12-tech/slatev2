# Component 09 — Animation & VFX

**Wave:** 1 (parallel).
**Depends on:** Wave 0 (`00-foundation-core.md`) only, plus a **contract**
(not code) dependency on the `damage_dealt`/`miss`/`status_applied`/
`status_expired`/`vfx_play` payload shapes documented in `CONTRACTS.md`
§3.2. No code dependency on `01-stats-combat.md` or `03-spells-status.md` —
build entirely against the documented payloads.

## 1. Scope & Boundaries

You are a **render-layer-only terminal event subscriber**. `anim_system.py`
listens to combat/status events and turns them into transient visuals —
projectile, swoosh, impact-flash, aura-pulse, area-burst, tile-fade. You
never mutate game state, never emit a gameplay-relevant event, and never
require a new field on any payload. If you find yourself wanting a field
combat doesn't already emit, that is a signal to **flag a gap against
CONTRACTS.md**, not to quietly plumb a new field through combat's code
(you don't own that code anyway — you'd be asking `01-stats-combat.md` or
`03-spells-status.md` to add it, and documenting why in this doc and your
PR).

The heuristics that decide *how* something looks (damage type → color,
damage amount → scale) are **Python configuration living in this module**,
not JSON data. This is deliberate, per the original spec (§8) — don't "fix"
it into a data file. It's presentation-layer tuning, not gameplay content;
nothing outside this module ever needs to read or override it, and keeping
it in code keeps this component genuinely removable (see §1.1) without
touching `data/`.

### 1.1 The removability constraint

This is the load-bearing design rule for the whole component: **deleting
`engine/ui/anim_system.py` (and un-registering its subscriptions) must
leave the rest of the engine and every JSON file completely untouched.**
No event this component emits is consumed anywhere else (it emits nothing
gameplay-relevant at all — see §4). No combat/status system checks whether
this component exists. This must be a literal, executable test in your
Definition of Done, not just an assertion in prose.

In scope:
- `engine/ui/anim_system.py`: subscribes to the five events in §3, resolves
  each to one of the six visual types, hands off transient visual state to
  the renderer (`07-input-renderer-audio.md`'s `Renderer`) via whatever
  drawing hook that component exposes for transient/overlay draws — you do
  not own `renderer.py`, but you may need a small, documented "draw
  transient effects" extension point on it (coordinate via this doc's
  wording: you consume renderer's `world_to_screen`/`draw_frame` hook
  points as documented in `07`'s doc; if no such hook exists yet, maintain
  your own list of active transient visuals and expose a
  `anim_system.draw(surface)` method the main loop calls directly,
  independent of `Renderer` internals — this keeps you decoupled and is the
  recommended default, see §9).
- The damage-type→color and damage-amount→scale heuristic tables (Python
  dicts/functions in this module, not JSON).
- Auditing `CONTRACTS.md`'s `damage_dealt`/`miss`/`status_applied`/
  `status_expired`/`vfx_play` payload shapes against all six required
  visual types and documenting the result (§5).

Out of scope:
- Any sound (`07-input-renderer-audio.md`'s `AudioSystem` handles sound;
  you draw nothing that plays audio, and you never emit `play_sound`).
- Actual tile/HUD rendering (`07-input-renderer-audio.md`).
- Deciding gameplay outcomes of any kind.
- Adding fields to combat/status payloads yourself — flag, don't patch.

## 2. Provides

```python
class AnimSystem:
    def __init__(self, event_bus, screen_rect_provider=None): ...
    def draw(self, surface, world_to_screen: Callable[[tuple[int,int]], tuple[int,int]]) -> None: ...
    def tick(self, dt: float) -> None: ...  # advances/expires transient visuals
```

- Purely additive: constructing an `AnimSystem` subscribes handlers;
  destroying/not-constructing it (or simply never calling `.draw()`/`.tick()`)
  is entirely safe — no other system reads any state this class owns.
- Each subscribed event handler creates a `Transient` record (internal
  dataclass — not a `@component`, not attached to any entity, not saved,
  purely a local render-thread list) with a `kind` (one of the six visual
  types), start time, duration, resolved color/scale from the heuristic
  tables, and source/dest screen positions resolved at draw time via the
  passed-in `world_to_screen` callable (never cached as absolute screen
  pixels, since the window can resize between event and draw).
- `tick(dt)` ages and prunes expired transients; `draw(surface, world_to_screen)`
  renders the live set. The main loop (`engine/main.py`, extended by
  whichever component wires the full loop — likely `07`'s renderer
  integration point) calls both once per frame, after the tile viewport is
  drawn and before/after the HUD per `08-ui-runtime.md`'s documented draw
  order (recommended: tile viewport → anim transients → targeting overlay
  → HUD panels, since transients are world-space and should sit under any
  world-space cursor per `08`'s ordering note, but this is a minor ordering
  call — document your actual choice).

### 2.1 Event → visual type resolution

| Event | Visual type | Heuristic inputs used |
|---|---|---|
| `damage_dealt` | `impact-flash` at `position`, plus `projectile` from `source_id`'s last known position to `position` if `source_id` is a ranged/spell attacker (heuristic: if source and target were >1 tile apart per `SpatialHash`/last `entity_moved`, treat as projectile; else melee swoosh) | `damage_type` → color, `amount` → scale bucket |
| `miss` | `swoosh` between `attacker_id` and `defender_id` positions, no impact-flash | neutral/gray color, fixed small scale |
| `status_applied` | `aura-pulse` centered on `entity_id` | `effect_id` → color (a small effect_id→color heuristic table, e.g. poison=green, burning=orange; falls back to a neutral default color for unmapped ids — never an error) |
| `status_expired` | `tile-fade` (a brief fade-out ring) on `entity_id`'s position | same color table as `status_applied`, faded/desaturated |
| `vfx_play` | `area-burst` at `position`, styled by `vfx_id` (a small vfx_id→(color,scale) table analogous to the damage-type one; unmapped `vfx_id` falls back to a generic burst, never an error) | `vfx_id`, `data` (read only for any positional/radius hint it documents — see §5 audit; treat unknown `data` fields permissively, ignore rather than error) |
| `entity_moved` | feeds `projectile` source/dest resolution for `damage_dealt` above; does not independently trigger a visual on its own | position tracking only |

Six visual types are covered: `projectile`, `swoosh`, `impact-flash`,
`aura-pulse`, `area-burst`, `tile-fade`. Each is a small, independent
drawing routine (a handful of primitives — lines, circles, rects with alpha
falloff) — no sprite assets required, consistent with "absence = zero cost"
extended to the animation layer itself.

## 3. Consumes

| Event | Payload fields used |
|---|---|
| `damage_dealt` | `target_id, amount, damage_type, source_id, position` |
| `miss` | `attacker_id, defender_id` |
| `status_applied` | `entity_id, effect_id, instance_id, duration` |
| `status_expired` | `entity_id, effect_id, instance_id` |
| `vfx_play` | `vfx_id, position, data` |
| `entity_moved` | `entity_id, from, to` — tracked in a small local position cache purely to resolve projectile source points; not the authoritative position store (that's `SpatialHash`, owned by foundation) |

## 4. Emits

**Nothing gameplay-relevant.** This is a terminal listener by design. It
does not emit `play_sound` (that overlap belongs to
`07-input-renderer-audio.md`'s `AudioSystem`, which independently
subscribes to the same combat/status events — no coordination needed, see
`07`'s doc §3 boundary note) and does not emit any new event of its own.

## 5. Payload sufficiency audit (required output of this component)

Per the original spec §8's explicit lesson — design the animation/VFX
contract deliberately alongside combat rather than discovering sufficiency
after the fact — this component's job includes **auditing** whether
CONTRACTS.md's existing payloads are actually sufficient for all six visual
types, and documenting the result here rather than silently threading new
fields through combat's payload if a gap is found.

Audit table (fill in/confirm during implementation; the columns below are
the expected outcome given CONTRACTS.md as written — if implementation
reveals a real gap, replace the relevant row's conclusion and flag it in
your PR as a CONTRACTS.md discussion item, do not just add a field to
combat's code):

| Visual type | Needs | Available from documented payload? |
|---|---|---|
| `impact-flash` | target position, a color hint | `damage_dealt.position`, `damage_dealt.damage_type` → yes |
| `projectile` | source position, dest position | `damage_dealt.source_id` + `damage_dealt.position`, resolved via this component's own `entity_moved`-fed position cache (not a new payload field — a local resolution technique) → yes, no gap |
| `swoosh` | attacker/defender positions | `miss.attacker_id`/`miss.defender_id` resolved via the same local position cache (or `damage_dealt` for a hit-swoosh variant) → yes |
| `aura-pulse` | entity position, effect identity | `status_applied.entity_id`, `status_applied.effect_id` → yes |
| `tile-fade` | entity/tile position, effect identity | `status_expired.entity_id`, `status_expired.effect_id` → yes |
| `area-burst` | position, style hint | `vfx_play.position`, `vfx_play.vfx_id` → yes |

**Conclusion to record in your PR:** if all six rows resolve to "yes" using
only documented fields plus this component's own local position-cache
technique (no new payload fields), state that explicitly — it confirms the
rebuild's `01-stats-combat.md` payload design succeeded at the lesson from
spec §8. If any row does not resolve, do not add the field to combat's
payload yourself; write the specific gap here, propose the minimal field
addition, and raise it as a CONTRACTS.md amendment for the orchestrator/
`01-stats-combat.md` session to reconcile, exactly as CONTRACTS.md §1's
"flag the conflict" instruction directs.

## 6. File/Directory Ownership

You own, exclusively:
- `engine/ui/anim_system.py`
- `tests/unit/test_anim_system.py`
- `tests/fixtures/anim_events_sample.json` (a fixture list of sample
  combat/status event payloads used to drive tests without depending on
  `01-stats-combat.md`'s actual implementation)

You do **not** own `engine/ui/ui_runtime.py` (`08-ui-runtime.md`) or
`engine/render/renderer.py` (`07-input-renderer-audio.md`) — if your draw
hook needs a small addition to either, document the exact minimal addition
needed in your PR rather than editing those files yourself; default to the
decoupled `anim_system.draw(surface, world_to_screen)` approach in §2 that
needs no changes to either file at all.

## 7. Data Schemas

None owned. This component introduces no new JSON schema and no new
`data/` files — the heuristic tables (§2.1, damage-type→color,
damage-amount→scale, effect_id→color, vfx_id→style) are Python
dicts/functions inside `anim_system.py`, e.g.:

```python
# Illustrative shape only — lives in anim_system.py, not JSON.
DAMAGE_TYPE_COLOR = {
    "physical": (200, 200, 200), "fire": (240, 90, 40),
    "cold": (120, 200, 240), "lightning": (240, 240, 120),
    "poison": (120, 200, 80), "holy": (250, 240, 200),
    "arcane": (170, 110, 240), "necrotic": (110, 40, 110),
}
DEFAULT_DAMAGE_COLOR = (255, 255, 255)

def scale_for_amount(amount: int) -> float:
    if amount <= 3: return 0.6
    if amount <= 8: return 1.0
    if amount <= 15: return 1.4
    return 1.8
```

All eight canonical damage types (CONTRACTS.md §10) get an explicit entry;
an unmapped/future damage type falls back to `DEFAULT_DAMAGE_COLOR` rather
than a `KeyError` — absence = zero cost applies to this component's own
internal lookups too.

## 8. Lessons from v1 applied here

- **This system's whole existence is the worked example of "everything is
  events" paying off** (spec §8) — it validates that a pure listener with
  zero new payload fields is possible when combat's payload design is done
  right the first time. §5's audit is this doc's way of not letting that be
  "a lucky consequence" a second time, per the spec's own stated lesson —
  it should be a deliberate, checked outcome here, not a retrospective
  observation.
- **Removability is the test, not a slogan.** §1.1 requires an executable
  test that disables this component and asserts nothing else breaks —
  this is the closest thing this rebuild has to a regression guard against
  this component quietly growing a second responsibility over time (e.g.
  accidentally becoming load-bearing by emitting something combat starts
  depending on).

## 9. Definition of Done

- [ ] All six visual types (`projectile`, `swoosh`, `impact-flash`,
      `aura-pulse`, `area-burst`, `tile-fade`) implemented with a unit test
      each asserting the correct `Transient` record is created from a
      fixture event payload.
- [ ] Damage-type→color, damage-amount→scale, effect_id→color, and
      vfx_id→style heuristic tables cover all 8 canonical damage types
      (CONTRACTS.md §10) plus an explicit fallback default for unmapped
      values, with a test proving the fallback path (an intentionally
      unmapped damage type/effect_id/vfx_id still renders without error).
- [ ] §5's payload-sufficiency audit is completed and recorded in this doc
      (or amended with the actual finding) and restated in the PR
      description; any real gap found is flagged as a CONTRACTS.md
      discussion item, not silently patched into combat's code.
- [ ] `entity_moved`-fed local position cache correctly resolves
      projectile/swoosh source-dest pairs without depending on
      `SpatialHash` directly (test constructs the cache from synthetic
      `entity_moved` events only).
- [ ] **Removability test**: a test builds a real event bus + minimal
      world, constructs `AnimSystem`, emits all five consumed events, and
      asserts no exception and no state change outside `AnimSystem`'s own
      internal transient list; a second variant of the test **never
      constructs `AnimSystem` at all**, emits the same five events on the
      same bus, and asserts the engine/event bus itself raises nothing and
      no other subscriber's behavior differs — proving removal is inert at
      the system level, not just that this class is well-behaved.
- [ ] `AnimSystem.draw()`/`tick()` require no import of
      `engine/ui/ui_runtime.py` or `engine/render/renderer.py` internals —
      a test constructs and drives `AnimSystem` fully standalone (a bare
      event bus + a stub `world_to_screen` lambda) with no renderer/UI
      object instantiated at all.
- [ ] `pytest` green, including the integration test in §10.

## 10. Test Plan

Unit tests per §9. Integration test (CONTRACTS.md §7.3 — must exercise the
real event bus, not a mock of it):

- `tests/integration/test_anim_system_pipeline.py`: build a real event bus;
  construct `AnimSystem` against it; emit a realistic sequence mirroring an
  actual combat exchange — `entity_moved` (attacker approaches),
  `damage_dealt` (hit), `miss` (a second swing), `status_applied` (poison),
  `status_expired` (poison wears off), `vfx_play` (an AoE spell) — all as
  real `event_bus.emit()` calls with fixture payloads matching
  CONTRACTS.md's documented shapes; assert after each emit that the
  correct `Transient` kind was added to `AnimSystem`'s internal list (via a
  test-only accessor, not by reaching into private state destructively);
  call `tick()` past each transient's duration and assert it's pruned; call
  `draw()` against a real `pygame.Surface` (headless/dummy video driver)
  and assert it completes without error for every visual type in one pass.
  Additionally include the removability variant from §9's checklist in
  this same integration file, since it's the component's defining
  guarantee.

## 11. Open Questions & Defaults

- **Draw hook integration point**: default to a fully standalone
  `draw(surface, world_to_screen)` called directly by `engine/main.py`'s
  frame loop (wherever that gets wired — likely extended by
  `07-input-renderer-audio.md`'s session since it owns the render loop
  shape), rather than this component reaching into `Renderer`. If `07`'s
  doc documents a formal "transient overlay" hook instead, prefer that and
  note the switch in your PR — but do not import `renderer.py` internals
  either way.
- **Projectile heuristic threshold** (">1 tile apart per last known
  position" as the melee-vs-ranged signal): a reasonable default absent a
  `combat.attack_kind` field in the payload; if `01-stats-combat.md`'s
  actual payload turns out to carry an explicit ranged/melee flag, prefer
  reading that directly over the distance heuristic and note the
  simplification in your PR.
