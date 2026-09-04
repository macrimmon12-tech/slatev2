# Component 15 — Integration & Verification

**Wave:** 3 (sequential, final — run only after every other component doc's
PR has merged to `main`).
**Depends on:** everything (`00` through `14`).

## 1. Scope & Boundaries

This is not a new feature. It is the gate that catches whatever
independent, parallel Wave 1/2 sessions couldn't see about each other —
by construction, none of them read each other's code, only the shared
contracts, so this is where contract drift gets caught before it ships.

It exists because of v1's single biggest recurring failure mode (spec §11
lesson 1): systems fully built and unit-tested in isolation, never
actually wired into the real pipeline (`place_floor_loot()` never called
from map generation; `vfx_play` emitted with nothing subscribed;
`EffectResolver` emitting `damage` while the renderer listened for
`damage_dealt`). Every component doc in this project already carries its
own Definition of Done and Test Plan aimed at not repeating that — this
component re-checks it at the whole-system level, because "each piece
individually satisfied its own DoD" does not guarantee "the pieces
actually fit."

In scope: cross-component audits, one real end-to-end playable vertical
slice, and a short, deliberately thin content pack sized only to exercise
every system at least once (not a content-volume task — spec §12
explicitly defers real content volume).

Out of scope: fixing a component's internals beyond what's needed to make
the wiring work. If a real design gap surfaces (not a wiring gap), stop
and escalate per `ORCHESTRATION.md` §5 rather than freelancing a fix that
wasn't reviewed as part of that component's own spec.

## 2. Cross-Component Audits (do these first, before the playtest)

### 2.1 Event table audit
For every event in `CONTRACTS.md` §3.2:
- [ ] At least one real `emit()` call exists in the merged codebase.
- [ ] At least one real `subscribe()` call exists in the merged codebase
      (exception: events that are genuinely terminal by design — e.g.
      `game_complete` may have no subscriber yet if there's no credits
      screen; list any such deliberate exception explicitly here rather
      than silently ignoring a gap).
- [ ] The payload shape actually emitted matches the payload shape actually
      consumed (field names, not just event name — this is exactly the
      `damage` vs `damage_dealt` class of bug, generalized: two components
      can agree on an event name and still disagree on its payload).

Produce a short table in this doc's PR (event → emitter file:line →
consumer file:line → verdict) rather than just checking a box — this is
the artifact that proves the audit happened, not just that it was run.

### 2.2 Component registry audit
- [ ] Every `@component`-marked dataclass across `engine/core` and
      `engine/systems` is present in `_COMPONENT_REGISTRY`
      (00-foundation-core.md's lint test should already enforce this in
      CI — re-run it here and confirm it's actually wired into CI, not
      just present as a test file nobody runs).
- [ ] `InteractableComponent` (from `11-npc-dialog-shop-content.md`) is
      registered.

### 2.3 Data schema audit
- [ ] Every namespace in `CONTRACTS.md` §4 has at least one real fixture
      file under `data/` that round-trips through the registry.
- [ ] Every JSON schema defined across the component docs
      (spell, campaign, vault, controls, audio_config, ui_skin widget-tree,
      dialog-graph, shop, monster `ai.behavior_phases`) has a validator
      under `engine/core/schemas/` per `CONTRACTS.md` §9, and every fixture
      file validates against it.

### 2.4 Wall autotile contract audit (spec §11 lesson 3, specifically)
- [ ] `07-input-renderer-audio.md`'s 16 wall-variant slot contract
      (`wall_0`..`wall_15`) is the exact same slot naming used by
      `13-editor-core-authoring.md`'s Sprite Manager theme JSON schema.
      This is the single named example from v1 of a renderer capability
      shipping without its authoring surface — confirm it didn't happen
      again here, don't just trust each doc's own Definition of Done.

### 2.5 room_id contract audit (spec §11 lesson 4, specifically)
- [ ] Procgen floors (`06-worldgen-campaign.md`) populate `room_id` on
      every tile.
- [ ] Handcrafted maps authored via the Map Editor
      (`13-editor-core-authoring.md`) either also populate `room_id`, or
      the editor visibly warns ("this map has no room_id data — noise
      attenuation will use radius only") per that doc's own DoD — confirm
      the warning actually fires against a real handcrafted map fixture
      with no room authoring done, not just that the warning code exists.

### 2.6 Lua boundary audit
- [ ] `set_stat` (from `10-lua-scripting-layer.md`) actually rejects
      combat-derived stat names at the API boundary — write a Lua fixture
      script that tries `engine.set_stat(id, "hp", 999)` and confirm it's
      rejected, not silently allowed.
- [ ] The sandbox actually removes `io`/`os`/`require`/`load`/etc. — run a
      Lua fixture script that attempts each nulled global and confirm every
      one fails cleanly.
- [ ] `npc_interaction.lua`/`dialog_walker.lua`/`shop.lua`
      (`11-npc-dialog-shop-content.md`) use `engine.create_panel` with
      widget-tree JSON in the exact same shape `08-ui-runtime.md` and
      `13-editor-core-authoring.md`'s Skin Editor use — no drift into a
      second, Lua-only panel format.

## 3. The Vertical Slice

A single, small, fully playable path exercising every system at least
once in the real game loop (not a unit test harness). Build exactly this,
no more, no less — resist the temptation to use this as a content-volume
task; that's explicitly deferred per spec §12.

**Minimum slice content** (author via the real editor from
`13-editor-core-authoring.md`, not by hand-writing JSON, so the editor
itself gets exercised as part of this gate):

- One small campaign, 2 floors. Floor 1 procgen, floor 2 handcrafted
  (to exercise both `room_id` paths from §2.5).
- One guaranteed vault on floor 1 (exercises `VaultInjector`'s guaranteed
  path — spec §11 flags this as never having been exercised at all in v1:
  "no vault with a guaranteed spawn ever authored to exercise
  VaultInjector's guaranteed path." Fix that gap here, explicitly.)
- 3–4 monsters covering all 4 AI behaviors (chaser, ambusher, patroller,
  coward) and at least one with `behavior_phases` (a mini-boss).
- 2–3 spells covering at least 2 different targeting modes, and at least
  one monster that casts one via the AI's SpellSystem path.
- A handful of items: one equippable weapon with `stat_modifiers`, one
  cursed item, one legendary (exercises `spawned_uniques`), one full set
  (2+ pieces, exercises `SetTrackerSystem`'s threshold bonus).
- A monster with a populated `loot_table`, plus ambient floor loot placed
  via `place_floor_loot()` — confirm items are physically present on the
  tile map after generation, per `04-inventory-items-loot.md`'s own DoD,
  re-verified here in the real game, not the test suite.
- One NPC with a short dialog tree (including one branch and one
  curse-reveal moment) and one shop with a small stock list.
- At least one ranged weapon + ammo flow, played start to finish — spec
  §11 explicitly flags this as never tested end-to-end in v1
  ("no ranged-ammo flow tested end-to-end").
- One trap-detect-radius-bearing item/status equipped near a `trap_revealed`
  hook point — since traps are explicitly deferred (spec §12), this is
  just confirming the stat and inert hook from `05-progression-vision.md`
  don't crash anything, not that trap gameplay works.

**Play it to completion**, either by a scripted input-replay test or by a
human playtest pass, covering:
- [ ] Move, fight, die to a monster, respawn/game-over flow works.
- [ ] Kill a monster → XP → level up → stat allocation → 3-option spell
      choice, all UI panels appear and are dismissable.
- [ ] Cast a spell in each of at least 2 targeting modes; provoke at least
      one fizzle/backfire.
- [ ] Pick up, equip, unequip, identify (`identify_type` and
      `identify_instance` both), and drop items; confirm cursed-item
      auto-identify-and-lock works.
- [ ] Talk to the NPC, walk the full dialog tree including the branch,
      trigger the curse reveal.
- [ ] Buy and sell at the shop; confirm gold and stock update and persist.
- [ ] Fire a ranged weapon until ammo depletes; confirm the
      `ranged_attack_blocked` path fires correctly at zero ammo.
- [ ] Take the stairs to floor 2 (handcrafted); confirm floor 1 state
      snapshots correctly and floor 2 loads.
- [ ] Save mid-dialog or mid-shop-interaction, reload, confirm Lua-managed
      state (dialog node position, shop stock/gold) survived with zero
      Python-side special-casing, per `11`'s own DoD test repeated here in
      the full game context.
- [ ] Complete the campaign; confirm `campaign_complete`/`game_complete`
      fire and something (even just a log line / simple screen) reacts.
- [ ] Every wall on both floors renders using an appropriate one of the 16
      autotile variants, not a single flat "wall" look throughout.

## 4. Pack/Mod Round-Trip (exercises 12 end-to-end, for real)

- [ ] Take the vertical-slice project, Pack it via the editor into
      `.pak`/`.pkd` under `dist/`.
- [ ] Point a clean run of the game at the packed archive only (no loose
      files) and confirm it plays identically to the loose-file version.
- [ ] Add one loose-file override on top of the packed archive (e.g.
      rebalance one monster's HP) and confirm the loose file wins per
      load-order rules, without touching the archive.
- [ ] Add a second mod archive that adds one new item, confirm it's
      additive and doesn't collide with the base archive.

## 5. Definition of Done

- [ ] §2's five audits complete, each with its evidence table/notes
      recorded in this doc's own PR description, not just checkboxes.
- [ ] §3's vertical slice authored via the real editor, and every checklist
      item played through and passing (scripted replay or human pass —
      record which).
- [ ] §4's pack/mod round-trip passing.
- [ ] Full `pytest` (unit + integration, all components) green on `main`
      at the exact commit this verification pass ran against.
- [ ] Any gap found gets one of two outcomes, recorded explicitly: (a) a
      small, obviously-in-scope wiring fix pushed directly as part of this
      component's PR (e.g. a missing `subscribe()` call, a schema field
      rename to match), or (b) an issue filed against the owning
      component's doc with a clear repro, escalated per
      `ORCHESTRATION.md` §5 rather than fixed ad hoc here. Do not silently
      patch around a real design gap.
- [ ] `docs/SLATE_REDESIGN_SPEC.md` §12's explicit deferrals (Windows
      packaging, full room/corridor authoring for handcrafted maps, trap
      component/trigger system, content volume) remain untouched by this
      pass — confirm nothing crept into scope.

## 6. Test Plan

- All audits in §2 are themselves the "test plan" for the wiring-level
  concerns — each produces either an automated check (preferred, add it to
  `tests/integration/test_contract_audit.py` so it's not a one-time manual
  pass) or a documented manual verification with evidence.
- §3's vertical slice should be captured as
  `tests/integration/test_vertical_slice.py` — a scripted input-replay
  test if the input system supports headless replay
  (`07-input-renderer-audio.md` should confirm this is possible; if not,
  note that as a gap in that component and do the slice as a recorded
  human playtest instead, documented step-by-step in this PR).
- §4's pack/mod round-trip becomes
  `tests/integration/test_mod_pack_roundtrip.py`.

## 7. Open Questions & Defaults

- **Scripted replay vs. human playtest for §3:** default to scripted
  input-replay if `07`'s InputHandler exposes a way to feed synthetic
  input events (recommended default: yes, build it there specifically so
  this component doesn't need a human in the loop for every future
  regression run of the vertical slice). If that capability wasn't built,
  do one human pass, write it up in detail, and file a small follow-up
  against `07` to add replay support rather than accepting "manual only"
  as permanent.
