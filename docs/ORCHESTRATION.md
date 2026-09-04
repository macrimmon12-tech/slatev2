# SLATE v2 — Build Orchestration

This is the master doc. Read this to decide **what to run, in what order,
and how to tell when a wave is done.** Each individual component's own doc
(`docs/components/NN-name.md`) is self-contained enough for a fresh Claude
Code session to build in isolation — this doc is about scheduling those
sessions, not about how any one of them works internally.

Source design doc: the original spec this whole build derives from is
preserved at `docs/SLATE_REDESIGN_SPEC.md`. `docs/CONTRACTS.md` is the
binding interface reference every component doc and every session must
treat as ground truth. Read both before reading this if you haven't.

---

## 1. Why this is parallelizable at all

v1's own hardest-won lesson was **"everything is events"** — systems never
call each other directly. That discipline is what makes this reorganizable
into independent workstreams: a component's real dependency is almost never
"another component's code," it's "the event/schema contract that code will
satisfy." `docs/CONTRACTS.md` fixes that contract up front, so most
components can be built simultaneously against the contract, by sessions
that never see each other's work in progress, and still integrate cleanly.

The exceptions are called out explicitly in the wave graph below (§3) —
places where a component genuinely needs another component's *code*, not
just its contract (e.g., the Lua API surface needs real systems to call
into for its own tests to mean anything).

---

## 2. Session mechanics

Run each component as its own Claude Code session on its own branch:

```
git checkout -b component/NN-short-name origin/main
```

(or a git worktree per component if running sessions concurrently on one
machine — recommended so two sessions never fight over the working tree:
`git worktree add ../slatev2-NN component/NN-short-name`).

Each session's opening prompt should be, essentially:

> Read `docs/CONTRACTS.md` and `docs/components/NN-short-name.md` in full.
> Implement everything in that component doc's scope, on this branch.
> Follow the Definition of Done and Test Plan sections exactly. Do not
> touch files outside the layout that doc and CONTRACTS.md §1 assign to
> you. When done, run the full test suite (`pytest`), fix anything you
> broke outside your own new tests, commit, push, and open a PR against
> `main` summarizing what's implemented, what's stubbed per CONTRACTS.md
> §8, and which CONTRACTS.md §7 wiring points are satisfied vs. deferred.

A session is unattended-safe because its own doc contains everything it
needs to self-check (Definition of Done, Test Plan) without asking you
anything — if it hits a real ambiguity CONTRACTS.md doesn't resolve, its
doc's "Open Questions" section says so explicitly and it should make the
documented default choice and note the deviation in its PR rather than
stall.

### 2.1 Merge discipline

- PRs merge to `main` only when: `pytest` is green, the component's own
  Definition of Done checklist is fully checked in the PR body, and (per
  CONTRACTS.md §7) every event it emits/consumes is accounted for.
- `engine/core/save.py`'s `_COMPONENT_REGISTRY` and
  `docs/CONTRACTS.md` §3.2's event table are the two files most likely to
  get concurrent edits across components. Rebase-and-append, don't
  resolve by dropping the other side's entries.
- Within a wave, merge order between components doesn't matter — they're
  independent by construction. Merge whichever PR is green first.

---

## 3. Build Waves

**Wave 0 — Foundation (sequential, blocking).** One session. Nothing else
starts until this merges, because every other component imports it.

| # | Component doc | Depends on |
|---|---|---|
| 0 | `00-foundation-core.md` | — |

**Wave 1 — Core systems and presentation (parallel, up to 12 sessions at
once).** Each depends only on Wave 0 + `CONTRACTS.md`.

| # | Component doc | Depends on |
|---|---|---|
| 1 | `01-stats-combat.md` | Wave 0 |
| 2 | `02-ai-system.md` | Wave 0 |
| 3 | `03-spells-status.md` | Wave 0 |
| 4 | `04-inventory-items-loot.md` | Wave 0 |
| 5 | `05-progression-vision.md` | Wave 0 |
| 6 | `06-worldgen-campaign.md` | Wave 0 |
| 7 | `07-input-renderer-audio.md` | Wave 0 |
| 8 | `08-ui-runtime.md` | Wave 0 |
| 9 | `09-animation-vfx.md` | Wave 0 (contract with 01/03 events, no code dep) |
| 10 | `10-lua-scripting-layer.md` | Wave 0 (full API hardening benefits from 01/04 merged, but sandbox + surface can be built and tested against stubs per CONTRACTS.md §8) |
| 12 | `12-modding-archive-system.md` | Wave 0 |
| 13 | `13-editor-core-authoring.md` | Wave 0 (reads CONTRACTS.md §4 schemas directly, not other components' code) |

**Wave 2 — Genuine code-level dependents (parallel, up to 2 sessions).**
These need another component's *implementation*, not just its contract.

| # | Component doc | Depends on |
|---|---|---|
| 11 | `11-npc-dialog-shop-content.md` | 10 (Lua host + engine.* API) and 08 (UI runtime, for the Lua-authored panels) merged |
| 14 | `14-editor-visual-quest-dialog.md` | 10 (engine.* API + `data/scripts/snippets.json` shape) and 13 (editor shell/mode-switching) merged |

**Wave 3 — Integration gate (sequential, one session, run last).**

| # | Component doc | Depends on |
|---|---|---|
| 15 | `15-integration-verification.md` | Everything above merged |

### 3.1 Practical scheduling

- Kick off Wave 0 alone. Confirm it's merged and `pytest` is green on
  `main` before starting anything else.
- Kick off all of Wave 1 at once (11 sessions/branches/worktrees). They
  will merge at different times; that's fine, they don't block each other.
- As soon as **10 and 08** are both merged, kick off 11. As soon as **10
  and 13** are both merged, kick off 14. Everything else in Wave 1 has no
  bearing on Wave 2's start time.
- Kick off 15 only once every other component doc's PR has merged.

---

## 4. What "mostly unattended" means here

You don't need to referee each session's design choices — the component
docs are written to make the default choice explicit wherever v1 already
answered the question, and to say so in an "Open Questions" section
wherever it's a genuine judgment call, with a recommended default. A
session should never block on you unless it hits something neither its own
doc nor CONTRACTS.md resolves.

What you *do* need to do, between waves:

1. **Gate Wave 0 → Wave 1 yourself.** Read the Wave 0 PR, confirm
   `pytest` is green and the Definition of Done checklist is checked, merge
   it, then kick off Wave 1.
2. **Spot-check Wave 1 PRs against CONTRACTS.md §7** as they land — a
   session grading its own homework will occasionally mark something "done"
   that only has a unit test, not a real wiring test. This is exactly the
   v1 failure mode (§8.5's "built but not wired") and the reason §7 exists
   as a hard gate — don't wave a PR through on green tests alone without
   at least skimming that its integration test actually exercises the real
   pipeline (e.g., for loot: does the test call floor generation and
   inspect the tile map, or does it just call `LootSystem.roll()` directly?).
3. **Run Wave 3 yourself last**, and treat its findings as blocking —
   it exists specifically to catch anything Wave 1/2's parallelism let
   slip past each other (an event named slightly differently by two
   components, a schema field two docs each assumed they owned, etc.).

## 5. Escalation path

If a session's PR notes a genuine open question that its own doc's "Open
Questions" section didn't already resolve for it (rare — most were decided
in the docs precisely to avoid this), don't have it guess further: either
answer it directly and have the session note the decision in its PR, or
add the decision to `docs/CONTRACTS.md` if it's the kind of thing future
components will also need to know.

## 6. Definition of "built"

The whole effort is done when:

- Every component doc's Definition of Done is checked and merged.
- `15-integration-verification.md`'s full checklist and vertical-slice
  playtest pass.
- `pytest` (unit + integration) is green on `main`.
- The gaps explicitly deferred in `docs/SLATE_REDESIGN_SPEC.md` §12
  (Windows packaging, full room/corridor authoring for handcrafted maps,
  trap component/trigger system, content volume) remain deferred — they
  were carried forward on purpose, not dropped by omission. Don't have a
  session "helpfully" pick one up outside its own doc's scope.
