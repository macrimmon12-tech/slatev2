# Component 14 — Visual Quest & Dialog Editor

**Wave:** 2.
**Depends on:** `10-lua-scripting-layer.md` (the `engine.*` API surface and
the `data/scripts/snippets.json` schema + `-- @node`/`-- @endnode` marker
convention it defines) **and** `13-editor-core-authoring.md` (the editor
shell / mode-registration mechanism) — **both must be MERGED before this
starts.** Genuine code-level dependency: this component generates real Lua
against `10`'s real API names and registers itself into `13`'s real mode
system, so building against stubs would mean redoing the integration once
both land anyway. Also references `11-npc-dialog-shop-content.md`'s
dialog-graph JSON schema for the Dialog Editor (§1.2) — read that schema,
do not redefine it here.

## 1. Scope & Boundaries

Spec §9 calls this "the most structurally important editor decision" in
the whole project, and singles out one property as the entire reason it
worked: **the GUI generates Lua, and the generated `.lua` file is the one
and only source of truth** — there is no separate "quest data"
representation living alongside it that could drift out of sync.

This component is two editor modes of very different difficulty, and the
doc is explicit about which is which so effort isn't misallocated:

### 1.1 Visual Quest Editor (`editor/modes/quest_editor.py`) — the hard one

- A node-graph canvas where a designer drags snippets from a palette
  (grouped by the fixed category vocabulary `10` defines:
  `trigger, condition, action, reward, narrative, ui, world`, sourced live
  from `data/scripts/snippets.json`) and wires them together.
- **Generating** a quest from the node graph must produce **the identical
  file** a scripter could write by hand for the same logic — same
  `engine.*` calls, same structure, no generator-only boilerplate a human
  author wouldn't also write.
- A **"View/Edit Script"** escape hatch opens the generated `.lua` file
  directly in a text editor pane for hand editing.
- **On save**, the GUI **re-parses** the (possibly hand-edited) Lua file:
  every block it recognizes as a snippet-shaped node (via the `-- @node`/
  `-- @endnode` markers, §2) re-renders as a normal node on the canvas;
  everything else — anything the parser doesn't recognize, including a
  hand-written snippet that isn't wrapped in markers at all, or a marker
  whose `type` doesn't resolve to a known snippet — is preserved verbatim
  as an opaque grey **"Custom Block"** node that survives every future
  round-trip untouched. Never silently dropped. Never silently mangled.
- **This round-trip — generate → hand-edit → re-parse → re-render,
  losslessly, for both recognized and unrecognized code — is THE hard
  requirement of this component and its Definition of Done's centerpiece
  test (§7).** This is what let designers build most of a quest visually
  and a scripter finish the rest without either side clobbering the
  other's work. State this explicitly as the design goal; it is also a
  pattern worth generalizing to any *future* "generate code from a GUI"
  feature (a documentation/precedent point only — not a build requirement
  of this component).

### 1.2 Dialog Editor (`editor/modes/dialog_editor.py`) — the simple one

A **deliberately thinner** case of the same idea: no code generation, no
round-trip fidelity risk, because there is no generation step at all. It
is a pure GUI layer directly over `11`'s existing dialog-graph JSON format
(`data/dialogs/*.json`, schema in `11-npc-dialog-shop-content.md` §5.1) —
the visual editor and the hand-editable JSON are **literally the same
file**, two views on one piece of data, not a generate/parse relationship.
Nodes = dialog nodes (`nodes[<id>]`), edges = choices/branches
(`choices[].goto`). Loading the editor reads the JSON; saving writes it
back, structurally, with no intermediate representation to keep in sync.
**Do not redefine the dialog schema here** — read `11`'s doc for it and
build directly against that shape.

Out of scope:
- `10`'s `engine.*` API and `snippets.json` schema — you consume both,
  own neither.
- `11`'s dialog/shop JSON schemas and Lua content — you consume the dialog
  schema (read-only reference), own neither it nor `dialog_walker.lua`.
- `13`'s editor shell, project model, mode-switching mechanism, and the
  other editor modes (map/sprite/data/skin editors) — you register into
  `13`'s mechanism, you don't build or modify it.
- A shop-authoring GUI — spec doesn't call for a "Shop Editor" mode
  distinct from `13`'s generic Data Editor's schema-aware forms; shop JSON
  is simple enough for that generic path and is not this component's job.

## 2. The Lua-generation template/pattern-recognition mechanism (concrete)

This is the real design decision this doc has to make, and it is made
already, jointly with `10`, so it is stated here **concretely**, not left
vague: round-trip fidelity is meaningless without an exact mechanism.

### 2.1 The marker format (defined in `10-lua-scripting-layer.md` §5.1,
restated here as this component's parsing target)

Every node the Visual Quest Editor generates is wrapped in a structured
comment block:

```lua
-- @node id="n3" type="action:action_grant_item" x=420 y=180
-- @params item_id="healing_potion" quantity=2
engine.grant_item(actor_id, "healing_potion", 2)
-- @endnode
```

- `id` — unique within the file, assigned by the editor when a node is
  created, stable across saves (never reassigned to a different node on
  re-render, so edges/wires drawn to it don't jump).
- `type` — `"<category>:<snippet_id>"`, an exact key into
  `data/scripts/snippets.json`'s `snippets[]` list.
- `x`, `y` — canvas position, bare numbers, so layout round-trips as well
  as logic.
- `@params` line — a **literal, structured record** of the parameter
  values used the last time this node was generated or edited via the
  GUI's inspector panel. This is the load-bearing design choice that makes
  parsing tractable: the parser **never** tries to reverse-engineer
  parameter values out of arbitrary Lua syntax in the body; it reads them
  off this one line, full stop.
- The body between the marker lines is whatever `snippets.json`'s
  `lua_template` produced for those parameter values (per `10`'s §5.1
  format-string mechanism).

### 2.2 Parsing algorithm (`editor/modes/quest_editor.py`'s Lua→node pass)

A **line-based scan**, not a Lua AST parse (deliberately — a real Lua
parser would need to fully understand arbitrary hand-edited code just to
find block boundaries, which is both harder and unnecessary since the
markers already delimit blocks unambiguously):

1. Scan the file line by line, tracking "outside a block" vs. "inside a
   block" state.
2. A line matching `^-- @node id="(.+)" type="(.+)" x=(-?\d+) y=(-?\d+)$`
   opens a block: record `id`, `type`, `x`, `y`, start line number.
3. If the very next line matches `^-- @params (.*)$`, parse it as a
   sequence of `key=value` tokens (quoted strings, bare numbers, bare
   `true`/`false`) into a params dict. Absent `@params` line = empty
   params dict (valid for a parameterless snippet).
4. Everything until the next line matching exactly `-- @endnode` is the
   block's **raw body text**, captured verbatim, byte-for-byte.
5. On `-- @endnode`: close the block. Look up `type` in the currently
   loaded `snippets.json`. **Two outcomes:**
   - **Recognized** (`type` resolves to a known snippet, AND
     re-instantiating that snippet's `lua_template` with the parsed
     `@params` values produces a body that — after whitespace
     normalization — matches the captured raw body): render a normal,
     editable node: category, label, and inspector fields all sourced
     from `snippets.json`'s definition, pre-filled with the parsed
     `@params` values, positioned at `(x, y)`.
   - **Unrecognized** (`type` doesn't resolve, OR the snippet no longer
     exists in the currently loaded `snippets.json` (deleted/renamed since
     generation), OR the body was hand-edited such that it no longer
     matches what the template would regenerate for those exact params):
     render an opaque grey **Custom Block** node at `(x, y)` whose payload
     is the **entire original block, markers included, byte-for-byte**.
     A Custom Block is inert to the editor's own logic (no inspector
     fields, no re-templating) but fully wired into the graph the same as
     any other node for connection/ordering purposes if the surrounding
     script structure implies adjacency (see §2.4).
6. Any Lua text **outside** any `@node`/`@endnode` pair (a stray
   hand-written line, a comment, a `require`-style header a scripter
   added — even though `require` itself is sandboxed at runtime by `10`,
   nothing stops a human from typing it in the editor's text view) is
   preserved as its own Custom Block anchored at the position it occupied
   in the file (top-of-file preamble and end-of-file trailer are captured
   as implicit "before all nodes" / "after all nodes" Custom Blocks so
   nothing outside a marker is ever silently dropped either).

**Why "match the regenerated body," not just "the type resolves," decides
Recognized vs. Custom Block:** a node whose `type` is valid but whose body
a human has since hand-tweaked in a way the template can't reproduce (e.g.
they added a second line of logic inside the block) must **not** silently
snap back to the template's version and lose that edit, and must not be
shown as an innocuously-editable node whose inspector fields, if touched,
would blow away the human's addition without warning. Demoting it to a
Custom Block the moment its body diverges from what its own declared
`type`+`params` would regenerate is the conservative, lossless choice —
better a node the designer has to notice is now "just a Custom Block" than
one that silently discards a hand-edit the next time the inspector panel
is touched.

### 2.3 Generation algorithm (node graph → `.lua` file)

The inverse of §2.2, and simpler: walk the node list in the graph's stored
order (the order nodes were added / last serialized — **not** re-sorted by
canvas position or graph topology, so an unrelated pure-layout drag
doesn't produce a large, noisy diff), and for each node:
- **Normal node**: look up its `type` in `snippets.json`, instantiate
  `lua_template` with `{id, x, y, **params}`, emit the resulting block
  verbatim (this is exactly the same instantiation `10`'s example snippets
  document — there is exactly one template-instantiation code path,
  shared conceptually between "generate new" and "the string re-parsing
  compares against" in §2.2 step 5).
- **Custom Block**: emit its stored raw text verbatim, unchanged,
  regardless of where it sits in the node order.

Concatenate with a single blank line between blocks. This is the entire
generator — there is no additional "linking" step that rewrites node
bodies to reference each other structurally; a quest's actual control flow
(trigger → condition → action) is expressed the same way a hand-written
script expresses it: normal Lua code inside each block (e.g. an `action`
node's body calling `engine.*` inside a `trigger` node's callback, or
scripts sharing state via `10`'s floor/campaign state API) — the node
graph's "wires" in the UI are a **visual aid showing data/control
relationships the generated code already expresses in Lua**, not a
separate structural format the generator has to keep in sync. This is the
detail that makes "the generated file is the ONE AND ONLY source of
truth" actually true rather than aspirational: there is nothing else to
keep in sync.

### 2.4 What a "wire" on the canvas actually represents

Since generation (§2.3) doesn't need graph edges to produce correct Lua
(each node's body is already complete, self-contained Lua per §2.3), edges
are a **presentation-layer overlay**, inferred and re-inferred on every
load rather than stored as separate ground truth that could drift:
- A `trigger` node's body is scanned (still via the same line-based
  approach, not a full parser) for calls to other known `engine.*`
  functions that match `action`/`reward`/`narrative`/`ui`/`world`
  snippets' signatures appearing textually inside it — a heuristic edge
  from trigger → action for display purposes only.
- If this heuristic can't confidently infer an edge (common for a Custom
  Block, or a node whose body doesn't call another snippet's exact
  signature), the node is simply shown unconnected on the canvas — this
  is a display degradation, never a data-loss risk, since §2.3 never
  needs edges to regenerate correct output. Document this plainly in the
  UI (an unconnected node is not an error state).

## 3. Registration into `13`'s mode system

```python
# editor/modes/quest_editor.py, editor/modes/dialog_editor.py

class QuestEditorMode:
    """Registers as a tab per 13-editor-core-authoring.md's mode interface.
    ASSUMPTION (verify against 13's actual doc once merged — flagged
    explicitly since this doc may be written before/concurrently with 13):
    a mode is a class implementing handle_event(event), update(dt),
    draw(surface), and is registered via editor.register_mode(name: str,
    mode_instance, hotkey: str | None) at editor startup, switching
    happens via callbacks per spec §9 ('modes never know each other's
    internals'). If 13's real interface differs in method names or
    registration call shape, adapt this component's two mode classes to
    match — the internal quest/dialog editing logic (§2, §4) is unaffected
    either way, only the thin adapter shell changes."""

class DialogEditorMode:
    """Same registration shape as QuestEditorMode. See §4 for its (much
    smaller) internal logic."""
```

Both modes get a tab + hotkey per `13`'s existing four-mode pattern
(spec §9: "tab bar + F1–F4... plus two tabs added after the fact" — this
component IS one of those "added after the fact" tabs, F5/F6 or whatever
`13`'s registration mechanism assigns next).

**Context links** (spec §9's cross-mode ergonomics investment, explicitly
called out as worth budgeting for, not a stretch goal): placing an NPC
with no dialog JSON assigned in the Map Editor (`13`'s scope) should be
able to jump into this component's Dialog Editor pre-filled to create one
— implement the receiving half of that link here (an entry point this
mode exposes, e.g. `DialogEditorMode.open_new(dialog_id_hint: str)`) even
though the *initiating* click lives in `13`'s Map Editor; document the
exact call shape here so `13`'s side can call into it once both exist.

## 4. Dialog Editor internals (the simple case, in full)

- Loads `data/dialogs/<id>.json` (per `11`'s schema) directly — a node per
  `nodes[<id>]` entry, positioned by a simple auto-layout on first load
  (a `_editor_layout` side-table keyed by node id, persisted... **but not
  inside the dialog JSON itself**, since `11`'s schema doesn't reserve a
  layout field and this component must not silently add undocumented
  fields to a file `11` owns the schema of. Store per-file layout hints in
  a sibling file, `data/dialogs/<id>.layout.json` (this component's own,
  owned file, additive-only, never read by `11`'s Lua or by the game
  runtime — purely an editor convenience). Losing this file is harmless
  (re-auto-layout on next open); losing the dialog JSON itself is not this
  component's risk to manage.
- An edge per `choices[].goto`. A `choices[].action` entry (e.g.
  `"open_shop"`) renders as a distinct terminal marker on that edge rather
  than a `goto` arrow, since it doesn't point at another node in this
  file.
- Editing a node's text, adding/removing/reordering choices, and setting a
  `condition` string are all direct edits to the in-memory JSON structure,
  written back verbatim on save — there is no parse/regenerate step
  because there was never a generation step; this is the entire reason
  §1.2 calls this mode "the simple case."
- `condition` strings are edited as plain text fields in this component
  (not parsed/validated beyond the grammar `11`'s doc documents) — full
  semantic validation of a condition expression is out of scope here;
  malformed conditions are `11`'s Lua-side concern at runtime (a condition
  that fails to evaluate defaults to `false`/hidden-choice per that
  runtime's own error handling, not something this editor blocks saving
  over).

## 5. Data Schemas

This component **introduces no new content schema** for the game runtime —
it reads/writes `10`'s `snippets.json` (read-only) and `.lua` files it
generates using `10`'s marker format (§2.1, not redefined here), and
reads/writes `11`'s dialog JSON verbatim (§4). The one new file this
component does own the shape of is its own editor-only sidecar:

### 5.1 `data/dialogs/<id>.layout.json` (editor-only, owned by this component)

```json
{
  "schema_version": 1,
  "node_positions": {
    "greet": {"x": 80, "y": 120},
    "lore_1": {"x": 340, "y": 120},
    "lore_2": {"x": 340, "y": 280},
    "end": {"x": 600, "y": 120}
  }
}
```

Not loaded by the Data Registry (it's not gameplay content — it lives
alongside `data/dialogs/` but the `dialogs` namespace's loader, owned by
`00`, keys only on files matching the dialog schema; a `.layout.json`
sibling is simply never referenced by `registry.get("dialogs", ...)`
consumers — confirm this doesn't collide with `00`'s directory-scan
convention when integrating; if it does, move layout sidecars to a
parallel `editor/state/dialog_layouts/` tree instead, a purely local
adjustment).

## 6. Lessons from v1 applied here

- **The whole point of this component, restated as a warning:** the
  moment the visual editor's internal graph representation becomes
  authoritative over the generated `.lua` file (e.g. by storing wiring as
  separate metadata the generator depends on to produce correct output),
  the "one and only source of truth" property is gone and the two can
  drift — exactly the failure mode a naive "GUI generates code" design
  falls into. §2.3/§2.4 are written the way they are specifically to keep
  every wire inferred, never load-bearing.
- **Silently dropping or mangling unrecognized content is the single worst
  failure this component could ship**, since it directly destroys a
  scripter's hand-written work with no warning — treat any code path that
  *could* discard bytes from a loaded `.lua` file without accounting for
  them in a Custom Block as a correctness bug, not a polish item.
- **Editor ergonomics were underweighted relative to engine work across
  the whole v1 project** (spec §11.2) — budget the "View/Edit Script"
  escape hatch, the context links (§3), and basic node-graph usability
  (drag, connect, delete, undo — even a minimal undo stack) as part of
  this component's own scope from the start, not a follow-up pass.

## 7. Definition of Done

- [ ] `data/scripts/snippets.json` (from `10`) drives the palette:
      adding a new snippet entry (in a test fixture) makes a new,
      correctly-categorized palette entry appear with no code change to
      this component.
- [ ] Dragging a snippet onto the canvas, filling its inspector params,
      and generating produces a `.lua` file byte-identical (modulo
      whitespace normalization) to what a human typing the same
      `lua_template` instantiation by hand would produce.
- [ ] **The round-trip test (the centerpiece requirement):**
      1. Generate a quest visually from at least 3 different snippet
         categories (e.g. one `trigger`, one `condition`, one `action`).
      2. Save. Confirm all 3 nodes render correctly as nodes on reload
         (not demoted to Custom Blocks) — proves recognition works for
         freshly-generated, untouched content.
      3. Manually edit the saved `.lua` file outside the editor: inject a
         new, hand-written Lua snippet the parser has no
         `snippets.json` entry for (no markers at all, or markers with a
         made-up `type`).
      4. Reload in the GUI. Assert: the hand-written snippet appears as a
         Custom Block containing its exact original text; **all 3
         originally-generated nodes still render as normal, editable
         nodes** (not also demoted to Custom Blocks by the presence of
         nearby unrecognized text).
      5. Save again without touching anything. Assert the file is
         byte-identical to the file from step 3 (proves a no-op
         load→save round-trip is truly lossless, Custom Block included).
      6. Edit one of the 3 generated nodes' params via the inspector,
         save, reload: assert that node's body updated to match the new
         params, the Custom Block is still untouched, and the other 2
         generated nodes are unaffected.
- [ ] A node whose body has been hand-edited to diverge from what its
      declared `type`+`@params` would regenerate is demoted to a Custom
      Block on next load (§2.2's "match the regenerated body" rule),
      tested explicitly — not just the "totally unrecognized" case.
- [ ] Dialog Editor round-trips `11`'s dialog JSON with zero data loss for
      every field in `11`'s schema (§4's "direct edit, no intermediate
      representation" claim verified by a test loading a dialog fixture,
      making one edit, saving, and asserting only the touched field
      changed, byte-diffed against the original for everything else it
      shouldn't have touched — including key ordering if `11`'s schema
      cares about it).
- [ ] Both modes register into `13`'s mode system per its actual
      documented interface once `13` is merged (or per this doc's §3
      assumption if built before/concurrently, with the adapter shell
      updated to match on integration).
- [ ] `pytest` green.

## 8. Test Plan

Unit tests:
- Marker parsing (§2.2): well-formed node block → correct
  `{id, type, x, y, params, body}`; malformed/partial marker lines
  (missing `@endnode`, non-numeric `x`/`y`) degrade to "everything from
  here to EOF is one Custom Block" rather than raising — an editor must
  never crash on a hand-mangled file, it must fail toward preserving
  bytes.
- `@params` line tokenizing: quoted strings with embedded spaces, numbers,
  booleans, an empty params line for a parameterless snippet.
- Template instantiation determinism: instantiating the same snippet with
  the same params twice produces byte-identical output (required for
  §2.2's "does the body match what regeneration would produce" check to
  be meaningful at all).
- Generation ordering: node graph order is preserved in output regardless
  of canvas (x, y) values (proves layout-only edits don't reorder or
  otherwise perturb unrelated blocks in the file).
- Dialog Editor: JSON load → in-memory edit → JSON save preserves every
  untouched field and (if `11`'s schema is order-sensitive) key order.

Integration test (`tests/integration/test_quest_editor_roundtrip.py` —
required per CONTRACTS.md §7.3, exercising the real generate/parse code
path end-to-end, not unit-testing the parser against a hand-built string
fixture in isolation):
- Drive the actual `QuestEditorMode` object (constructed for real, not
  mocked) through its real "add node from palette" and "set param" and
  "generate/save" entry points, sourcing the palette from a real, loaded
  `data/scripts/snippets.json` fixture (via `10`'s real schema/loader if
  merged, or the documented fixture shape from `10`'s doc otherwise) to
  build a 3-node quest as in §7's round-trip test.
- Read the generated file back off disk (a real file write/read, not an
  in-memory string handoff) — this is the "actual entry point... not a
  mock of it" requirement: the test proves the file that would actually
  sit in a project directory round-trips, not just an in-memory
  representation of one.
- Perform the manual hand-edit step (§7 step 3) as a real file-append/edit
  operation on disk between "save" and "reload."
- Reload through `QuestEditorMode`'s real load path and assert the node
  graph's in-memory state matches §7's expected outcome (3 real nodes + 1
  Custom Block, correct content each).
- A second integration case for the Dialog Editor: load `11`'s real
  example fixture dialog (`shopkeeper_mira_dialog`, from
  `11-npc-dialog-shop-content.md`'s own test fixtures, or an equivalent
  fixture under this component's `tests/fixtures/` if `11` isn't merged
  yet — soft dependency per CONTRACTS.md §8), make one edit (add a
  choice), save through the real `DialogEditorMode` save path, and assert
  the resulting JSON file is still valid against `11`'s own dialog schema
  validator (cross-component schema compatibility, not just "this
  component's own opinion of what it wrote is fine").

## 9. Open Questions & Defaults

- **Full Lua parsing vs. line-based marker scanning** (§2.2): default is
  line-based scanning keyed entirely on the `-- @node`/`-- @endnode`/
  `-- @params` marker lines, explicitly **not** a real Lua tokenizer/AST
  parse of block bodies. This is sufficient because bodies are only ever
  compared for byte/whitespace-normalized equality against a freshly
  templated version (§2.2 step 5), never semantically interpreted by the
  editor. If a future need arises to *understand* a Custom Block's Lua
  well enough to offer partial editing support for it, that's a
  substantial new capability, not a small extension of this mechanism —
  treat it as a new open question for whoever picks it up, not something
  to improvise here.
- **Edge inference heuristic (§2.4) false negatives:** accepted as a
  cosmetic-only limitation (an unconnected node on the canvas that a human
  would recognize as related) since it never affects generated output
  correctness (§2.3 doesn't consume edges at all). Do not invest in a
  more sophisticated static-analysis pass here without a concrete,
  demonstrated usability complaint — this was explicitly not the hard
  part spec §9 flagged.
- **`13`'s exact mode-registration interface** is unknown at the time this
  doc is written if `13` hasn't merged yet. §3's assumption is the
  documented default; the adapter shell (not the quest/dialog logic) is
  the only part expected to need adjustment once `13`'s real doc/code is
  available — note any deviation in your PR.
