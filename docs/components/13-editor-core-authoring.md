# Component 13 — Editor Core & Authoring

**Wave:** 1 (parallel).
**Depends on:** Wave 0 (`00-foundation-core.md`) only, for reading/writing
the same `data/` JSON schemas via CONTRACTS.md — you do not import
`engine/` gameplay systems and you do not import `engine/main.py`'s game
loop. Soft dependency (stub-and-extend) on `12-modding-archive-system.md`
for archive unpack/pack — see §2.1.

## 1. Scope & Boundaries

You build a **large, standalone pygame-ce application**, `editor/editor.py`,
entirely independent of the game runtime. It never imports the game loop;
it shares only the JSON data schemas defined in `CONTRACTS.md` and produced
by the Wave 1 component docs. Think of it as a second, separate pygame
application that happens to read and write the same `data/` directory the
game engine reads at runtime.

In scope:
- Project Manager (entry point): New Project, Open Project, Pack Project.
- Four core modes with a shared, callback-based mode-switching shell: Map
  Editor, Sprite Editor, Data Editor, Skin Editor.
- Two added-later tabs on the same shell: Sprite Manager, Audio tab.
- The mode-registration interface itself — documented explicitly (§2.5) so
  `14-editor-visual-quest-dialog.md` (Wave 2, depends on this component's
  shell) can plug in a fifth/sixth mode (`quest_editor.py`,
  `dialog_editor.py`) without editing any file this component owns.
- A budgeted, non-deferred usability pass (§7) as part of this component's
  own Definition of Done, not a follow-up phase — this is a direct,
  named lesson from v1's `docs/editor-bugs-todos.md` retrofit pain (spec
  §11 lesson 2).
- Context-links across modes (§2.6) as a planned, budgeted feature, not a
  stretch goal (spec §9).

Out of scope (explicitly, do not touch):
- `editor/modes/quest_editor.py`, `editor/modes/dialog_editor.py` —
  `14-editor-visual-quest-dialog.md` owns these; you provide the shell they
  register into, nothing more.
- Any `engine/` runtime code, any game-loop wiring, any event-bus
  interaction with a running game — this editor never runs alongside a
  live game session.
- Archive *writing* internals beyond what's needed for Pack Project (you
  may call into `12-modding-archive-system.md`'s writer if one exists, or
  stub your own minimal zip-write for Pack Project if `12` hasn't merged
  yet — see §2.1).
- Deciding gameplay balance/content — you're an authoring tool; the actual
  monster stats, spell formulas, etc. a designer types in are their call,
  not yours to validate beyond structural schema correctness.

### 1.1 Project Manager

A **project** is a working folder the editor owns: loose files, fully
git-trackable, **never an archive** — archives are a pure export artifact,
never something the editor edits in place.

- **New Project**: creates a fresh project folder mirroring the
  `data/`/`assets/` layout from CONTRACTS.md §1, seeded with a blank map
  and a default tileset, and the designer lands in **Map Editor within
  seconds** — no intermediate setup wizard. Concretely: create the minimal
  directory skeleton, write one blank `data/maps/untitled.json` (a small
  fixed-size map, e.g. 40×25, all floor tiles, no entities), and switch
  the shell straight to Map Editor with that map loaded.
- **Open Project**: opens an existing project folder directly (loose
  files, no unpacking needed), **or** unpacks an existing `.pak`/`.pkd`
  into a fresh project folder first via `12-modding-archive-system.md`'s
  archive reader, then opens the resulting loose folder. See §2.1 for the
  soft-dependency handling if `12` isn't merged yet.
- **Pack Project**: validates the project's data (structural schema checks
  per §2.4's Data Editor validators, plus Map Editor's own validation
  warnings) and writes `.pak`/`.pkd` build artifacts into `dist/` **without
  touching the loose source** — packing is a read-only operation over the
  project folder.

### 2.1 Soft dependency on `12-modding-archive-system.md`

Archive unpack (Open Project) and archive write (Pack Project) both want
`12`'s `ArchiveSource`/writer. Since Wave 1 components build in parallel
and may merge in any order:

- If `12-modding-archive-system.md` is already merged when you build this:
  import and use its real `engine/modding/archive.py` directly for both
  unpack and pack.
- If it is **not yet merged**: per CONTRACTS.md §8, do not import its
  not-yet-existing module. Stub Open Project to **loose-folder-only**
  project opening (skip the `.pak`/`.pkd` unpack path entirely, document
  the stub plainly in the UI — e.g. a disabled "Open Archive..." menu item
  with a tooltip noting it's pending), and stub Pack Project with a minimal
  local `zipfile`-based writer (a `.pak`/`.pkd` is just a renamed zip per
  CONTRACTS.md §1/`12`'s own doc — this is simple enough to stub without
  waiting) that you replace with `12`'s real writer once it merges, per
  CONTRACTS.md §8's "delete your stub only if the integration test still
  passes against the real thing" rule.
- Either way, do not block your own Definition of Done on `12` merging
  first — Wave 1 components are independent by construction (ORCHESTRATION.md
  §1). Note the actual state you built against in your PR.

## 2. Provides

### 2.2 Application shell

```python
# editor/editor.py
class Editor:
    def __init__(self, project: "Project | None" = None): ...
    def run(self) -> None: ...  # main loop: handle_event/update/draw across active mode
    def switch_mode(self, mode_id: str) -> None: ...
    def register_mode(self, mode_id: str, mode_instance: "EditorMode", tab_label: str, hotkey: str | None = None) -> None: ...

# editor/project.py
class Project:
    def __init__(self, root: Path): ...
    @classmethod
    def new(cls, root: Path) -> "Project": ...
    @classmethod
    def open(cls, root_or_archive: Path) -> "Project": ...
    def pack(self, dist_dir: Path) -> tuple[Path, Path]: ...  # (.pak path, .pkd path)
    def data_path(self, *parts: str) -> Path: ...
    def assets_path(self, *parts: str) -> Path: ...
```

### 2.3 Mode interface (the shared contract every mode implements)

```python
# editor/modes/__init__.py (or a base module you own)
class EditorMode(Protocol):
    def handle_event(self, event: "pygame.event.Event") -> None: ...
    def update(self, dt: float) -> None: ...
    def draw(self, surface: "pygame.Surface") -> None: ...
    def on_activate(self) -> None: ...   # called by switch_mode when this mode becomes active
    def on_deactivate(self) -> None: ...
```

Modes switch **via callbacks passed at registration**, never by importing
each other — "modes never know each other's internals" is the binding rule
here, directly mirroring CONTRACTS.md §2.4's "systems never call each other
directly" for the game engine. A mode that wants to jump to another mode
(context links, §2.6) calls a `navigate_to(mode_id, context)` callback
handed to it at construction, never `other_mode_instance.some_method()`
directly.

### 2.4 The four core modes

**Map Editor** (`editor/modes/map_editor.py`):
- Paint tiles (against the active project's tileset/theme — same
  `wall_variants` 16-slot autotile contract as `07-input-renderer-audio.md`,
  see §2.6 below), place entities/NPCs/items/transitions by picking from
  the live `entities`/`npcs`/`items` registry namespaces.
- Author vaults on the identical toolset at a vault-sized canvas — vaults
  are just small maps (`vaults` namespace, `data/maps/vaults/` per
  CONTRACTS.md §4), so reuse the same paint/place code path, not a parallel
  vault-specific editor.
- Author campaigns: drag-reorder level list, hub flags (`is_hub: true` per
  spec §3's `CampaignSystem`/`FloorManager` description) — a campaign is an
  ordered JSON level list, so this is a list-editing UI over
  `data/campaigns/*.json`, not a new data model.
- Validation warnings (non-blocking, shown in a persistent warnings panel,
  not a modal that stops work): no stairs on a map, no completion trigger,
  duplicate IDs across placed entities.
- **Required, exact-text warning** (spec §11 lesson 4): a handcrafted map
  with no `room_id` data authored must show the warning **"this map has no
  room_id data — noise attenuation will use radius only"** — verbatim, so
  it's greppable/testable and matches what `06-worldgen-campaign.md`'s
  noise-attenuation code actually falls back to. This is not optional
  polish; v1's exact failure was that handcrafted maps silently lost
  room-based noise attenuation with no signal to the designer that anything
  was different from procgen output. Cross-reference: `room_id` population
  is `06-worldgen-campaign.md`'s BSP procgen concern; this component's job
  is only to detect its *absence* on a handcrafted/saved map and surface
  the warning, not to compute or backfill room IDs itself (full
  room/corridor authoring tools are explicitly out of scope for the whole
  v1 rebuild per spec §12 — this warning is the deliberate "degrade
  visibly" response the spec calls for instead, not a lesser version of
  full authoring tools).

**Sprite Editor** (`editor/modes/sprite_editor.py`):
- Pixel-level painting against a registry-aware palette (the same palette
  concept `07-input-renderer-audio.md`'s fallback-color system references —
  coordinate the palette JSON's shape informally via CONTRACTS.md's
  `configs` namespace; don't invent a second incompatible palette format).
- Sheet Mode with animation preview (onion-skin or frame-scrub across a
  sprite sheet's frames).
- Multi-size round/square brushes, mirror tool (horizontal/vertical pixel
  mirroring while painting).

**Data Editor** (`editor/modes/data_editor.py`):
- Schema-aware forms **per content type** — explicitly **not** a raw JSON
  editor. One form layout per namespace/content-type (monster, item, spell,
  effect, affix, npc, dialog, shop, etc.), each driven by a schema
  description, not hand-written per-type Python form code where avoidable
  — **this mode's form definitions should themselves be data-driven** (a
  small per-content-type form-schema file, analogous in spirit to the
  widget-tree format `08-ui-runtime.md` defines, though this is a distinct,
  simpler format scoped to form-field layout, not full widget rendering) so
  adding a new content type's form later is a data addition, not a new
  Python module. Document your chosen form-schema shape explicitly in this
  doc once decided (see §5.2) so a later content-type addition (e.g. a new
  Wave 1/2 component introducing a new namespace) has something concrete to
  extend.
- A sub-editor for effect arrays (since almost every content type —
  spells, items, traps-that-don't-exist-yet, etc. — embeds a list of
  `EffectResolver`-shaped effect dicts per `01-stats-combat.md`'s
  `EffectResolver` schema) — one reusable effect-array widget used
  everywhere an `effects: [...]` field appears in a schema, not
  reimplemented per content type.
- Live JSON preview pane alongside the form (so a designer always sees the
  literal file they're about to save — this is also your escape hatch for
  power users, matching the spirit of the Lua visual-quest-editor's
  "View/Edit Script" pattern from the original spec §9, though scoped to
  read-only preview here unless you choose to make it editable — your
  call, document it).
- Saves directly into the project's data layout (loose files under the
  project's `data/`), never into an archive.
- This is the mode most directly dependent on every other component's JSON
  schemas — build its form-schema registry to be **extensible without a
  code change per addition** wherever realistically possible; where a
  content type's schema has a field type your generic form widgets don't
  yet support, add the generic widget type rather than a one-off
  special-cased field just for that content type.

**Skin Editor** (`editor/modes/skin_editor.py`):
- Visual authoring for `ui_skin.json`'s widget-tree format, defined in full
  by `08-ui-runtime.md` — build against that doc's schema (§5.1 of `08`)
  exactly; do not invent a parallel widget-tree shape. A tree edited here
  and one built by Python's `create_panel` or Lua's `engine.create_panel`
  must be interchangeable, byte-for-byte-compatible JSON.
- Live preview pane: renders the tree being edited using (a vendored or
  directly imported, read-only usage of) the same rendering logic
  `08-ui-runtime.md`'s `UIRuntime` uses, so what you see in the Skin Editor
  is what actually renders in-game — if importing `engine/ui/ui_runtime.py`
  directly is awkward given "editor never imports engine's game loop," it's
  fine to import just that one rendering module (not the loop, not the
  event bus wiring) purely for preview purposes; document this one
  deliberate exception in your PR since it's the one place this editor
  reaches into `engine/`.
- Palette color-picker for `ui_config.json` (a separate config file from
  `ui_skin.json`, per CONTRACTS.md §1's file listing — colors/theme
  constants referenced by style names in the widget tree, e.g. `"style":
  "title"` or `"color": "hp_red"` resolve through this file).

### 2.5 Mode-registration interface (for Wave 2's `14-editor-visual-quest-dialog.md`)

This is the explicit seam `14` plugs into once it starts (Wave 2, after
this component merges). Document and build it as a genuine registry, not a
hardcoded tab bar:

```python
# Conceptually:
editor.register_mode(
    mode_id="quest_editor",
    mode_instance=SomeModeImplementingEditorMode(...),
    tab_label="Quest",
    hotkey="F5",
)
```

- The tab bar and F1–F4(+) keybinds are **rendered from the registered-modes
  list**, not hand-enumerated in `editor.py`. Your own four core modes plus
  Sprite Manager and Audio tab register through this exact same call at
  startup — there is no privileged "built-in" registration path different
  from what `14` will use. This is the concrete proof the seam works: if
  your own six tabs go through `register_mode`, `14`'s two more tabs are
  guaranteed to slot in without editing your files.
- `switch_mode`/tab-click/hotkey-press all resolve through the same
  registry lookup by `mode_id`.
- Document the exact registration call and any ordering/grouping
  convention (e.g. do quest/dialog tabs get appended after Audio, or does
  registration order strictly follow call order?) explicitly in this file
  once implemented, since `14`'s session reads this doc, not your source,
  before it starts.

### 2.6 Added-later tabs

**Sprite Manager** (`editor/modes/sprite_manager.py`):
- Needed-sprite/has-sprite catalogue across all entity types: scans every
  loaded content JSON for `sprite_ref` fields, cross-references against
  what actually exists under the project's `assets/` tree, and lists gaps.
- Import-and-link-back: importing a new sprite file from this catalogue
  view writes the resulting `sprite_ref` onto the source JSON file
  automatically (no manual "go edit the JSON to point at the new file"
  step).
- Folds in the tile/autotile-variant view: **this MUST expose all 16 named
  wall variant slots** (`wall_0` through `wall_15`), individually
  assignable, per `07-input-renderer-audio.md`'s explicit
  `wall_variants` lookup contract (that doc's §2.3/§5.1) — **exactly**
  those 16 slot names, keyed the same way that doc's theme JSON does. This
  is the direct fix for spec §11 lesson 3 (v1 built the 16-variant renderer
  capability while the authoring surface exposed one generic `"wall"`
  slot for a long stretch): do not build a single generic wall-sprite
  picker here under any circumstance. Cross-reference `07`'s doc's exact
  `wall_variants` JSON shape (§5.1 of that doc) when building this view —
  if that doc's actual merged key names differ from this doc's draft,
  `07`'s doc wins (per CONTRACTS.md's normal "the more specific/owning doc
  wins" convention) and you conform to it.

**Audio tab** (`editor/modes/audio_tab.py`):
- Plain file-manager import/organize for `sfx/` and `music/` under the
  project's `assets/`, mirroring the Sprite Manager's import pattern
  (import → file lands in the right project subfolder → optionally
  link-back to `data/config/audio_config.json`'s `event_sounds` mapping if
  the designer chooses to bind it from here, though building the full
  audio_config editing UI is optional polish beyond plain file-manager
  import/organize — the minimum bar is import/organize, link-back is a
  nice-to-have you may include if time allows within this component's
  scope).

### 2.7 Context links across modes

A deliberate, budgeted ergonomics investment (spec §9), not a stretch
goal — plan it into this component's own work:
- Right-click a palette tile in Map Editor → jump to Sprite Editor with
  that PNG already loaded (via the `navigate_to` callback from §2.3,
  carrying a context payload naming the target asset).
- Place an entity with no JSON definition yet (e.g. a placeholder/new
  entity type) → jump to Data Editor with a new-record form pre-filled
  with whatever the Map Editor already knows (id guess, position context
  irrelevant, content-type preselected).

## 3. Consumes

Nothing from the game's event bus — the editor never runs alongside a live
game session and has no event-bus wiring to the runtime. It reads/writes
the same `data/` JSON files the runtime's `DataRegistry` reads, but through
its own direct file I/O (the editor is explicitly a second, independent
disk-I/O path — CONTRACTS.md §2.10's "Data Registry is the only disk I/O
path for content" governs the **runtime**, not this authoring tool, which
by nature must read/write JSON directly since that's its whole purpose;
state this distinction plainly in your PR since it could otherwise look
like a CONTRACTS.md §2.10 violation on a superficial read).

## 4. Emits

Nothing onto the game's event bus (none exists in this process). Internally,
modes communicate via the `navigate_to`/registration callback mechanism in
§2.3/§2.5, not events — that's a deliberate, simpler choice for a
single-process authoring tool with no save/load-across-time state machine
to decouple, unlike the live game.

## 5. Data Schemas

### 5.1 Project folder layout

Identical to `CONTRACTS.md` §1's `data/`/`assets/` layout, rooted at the
project folder instead of the repo root:

```
<project_root>/
  data/            # mirrors CONTRACTS.md §1 exactly
  assets/          # sprites/, sfx/, music/
  project.json     # { "schema_version": 1, "name": "...", "created": "..." }
```

### 5.2 Data Editor form-schema format (owned/introduced by this component)

A small, data-driven description of a content type's form, separate from
and simpler than `08-ui-runtime.md`'s widget-tree format (that format is
for live in-game panels; this one is purely for the editor's own
form-generation and is never consumed by the runtime or by Lua):

```json
{
  "content_type": "monster",
  "namespace": "entities",
  "fields": [
    {"key": "id", "label": "ID", "widget": "text", "required": true},
    {"key": "display_name", "label": "Display Name", "widget": "text"},
    {"key": "sprite_ref", "label": "Sprite", "widget": "sprite_picker"},
    {"key": "stats.hp", "label": "HP", "widget": "int", "min": 1},
    {"key": "stats.vision_range", "label": "Vision Range", "widget": "int", "min": 0},
    {"key": "ai.behavior", "label": "AI Behavior", "widget": "dropdown",
     "options": ["chaser", "ambusher", "patroller", "coward"]},
    {"key": "loot_table.entries", "label": "Loot Table", "widget": "effect_array_like_list"}
  ]
}
```

- `key` supports dotted paths into the target JSON's nested structure.
- `widget` names a generic, reusable form-widget type (`text`, `int`,
  `float`, `bool`, `dropdown`, `sprite_picker`, `id_picker_or_typed` — see
  §7's usability requirement that ID fields accept typed input, not only
  picker selection — and the dedicated `effect_array` widget for any
  `effects: [...]`-shaped field).
- `options` for `dropdown` may be a literal list (as above) or a string
  naming a registry namespace to populate from (e.g.
  `"options_from_namespace": "spells"` for a spell-choice dropdown) — this
  is what makes "new content type's form needs no new Python code" hold in
  the common case: adding a new namespace elsewhere doesn't require touching
  this mode's code, only adding a new form-schema JSON file.
- These form-schema files live under a location this component owns (e.g.
  `editor/form_schemas/*.json`, not under the shared `data/config/` tree
  since they're editor-internal, not runtime config) — pick a location and
  state it in your PR.

## 6. File/Directory Ownership

You own, exclusively:
- `editor/editor.py`
- `editor/project.py`
- `editor/modes/map_editor.py`
- `editor/modes/sprite_editor.py`
- `editor/modes/data_editor.py`
- `editor/modes/skin_editor.py`
- `editor/modes/sprite_manager.py`
- `editor/modes/audio_tab.py`
- `editor/modes/__init__.py` (or wherever the shared `EditorMode`
  protocol/base and the mode registry live)
- `editor/form_schemas/` (or your chosen location per §5.2 — state it)
- `tests/unit/test_editor_project.py`, `test_editor_modes_registry.py`,
  `test_map_editor.py`, `test_sprite_editor.py`, `test_data_editor.py`,
  `test_skin_editor.py`, `test_sprite_manager.py`, `test_audio_tab.py`
- `tests/fixtures/editor_projects/` (fixture project folders for tests)

Explicitly **not** owned here (leave untouched, even as stubs):
- `editor/modes/quest_editor.py`, `editor/modes/dialog_editor.py` —
  `14-editor-visual-quest-dialog.md`.

## 7. Lessons from v1 applied here

This section is not optional flavor text — the following are **required
Definition of Done items** (§8), budgeted into this component's own scope,
because v1's `docs/editor-bugs-todos.md` retrofit pass (spec §11 lesson 2)
existed *solely* to fix these after the fact, months after the tools were
first built and dogfooded only by their own author:

- **Tooltips** on non-obvious controls across all four core modes plus the
  two added tabs — not exhaustive coverage of every pixel, but every
  control whose purpose isn't self-evident from its label alone.
- **A visible Save affordance** beyond Ctrl+S — v1's Save worked but had no
  discoverable UI element; this component ships an actual visible Save
  button/menu item in every mode that can save, plus a visible
  unsaved-changes indicator.
- **Sprite-cache invalidation after cross-editor saves** — e.g. saving a
  sprite in Sprite Editor must invalidate any cached preview of that sprite
  currently shown in Map Editor or Sprite Manager without requiring an
  app restart.
- **Dropdown popups that don't clip off-screen** — position-aware popup
  placement (flip above/left when there's insufficient room below/right).
- **Visible column headers on form tables** — any list/table widget in
  Data Editor (e.g. the effect-array sub-editor, loot table entries) shows
  labeled columns, not bare unlabeled rows.
- **ID fields that accept typed input, not only picker selection** — every
  `id_picker_or_typed`-style widget (§5.2) must let a designer type an ID
  directly (e.g. for an ID that doesn't exist yet, to be created later) in
  addition to picking from an existing list.

Additionally:
- **The autotile 16-slot gap (spec §11 lesson 3)** is addressed structurally
  by §2.6's Sprite Manager requirement — do not let this regress to a
  single generic wall slot under time pressure; it is explicitly called out
  here a second time because it's the single most concrete, named lesson
  this whole component doc exists partly to prevent from recurring.
- **The room_id authoring gap (spec §11 lesson 4)** is addressed by the
  exact-text warning in §2.4's Map Editor section — implement the literal
  string, not a paraphrase, so it's testable and matches designer
  expectations set by this doc and `06-worldgen-campaign.md`.
- **Context links (spec §9)** are budgeted directly into this component's
  own scope per §2.7 — not deferred, not a stretch goal.

## 8. Definition of Done

- [ ] Project Manager: New Project lands the user in Map Editor with a
      blank map + default tileset within the same test-observable frame
      sequence (no intermediate blocking dialog required to reach it).
- [ ] Open Project opens a loose-folder project directly; archive unpack
      either uses `12-modding-archive-system.md`'s real reader (if merged)
      or a documented loose-folder-only stub (if not), per §2.1.
- [ ] Pack Project validates and writes `.pak`/`.pkd` into `dist/` without
      modifying the loose project source (test asserts source file mtimes/
      contents unchanged after a pack).
- [ ] All four core modes implement `handle_event`/`update`/`draw`/
      `on_activate`/`on_deactivate`, switch via the shared registry (not
      direct references to each other), and are covered by a mode-specific
      unit test for their primary authoring action (paint a tile, paint a
      pixel, edit-and-save a form, edit-and-preview a widget tree).
- [ ] Mode registration (§2.5) is a real registry your own six tabs go
      through — test asserts registering a brand-new dummy mode via
      `register_mode` makes it switchable via `switch_mode` and appears in
      the tab list, with zero changes to `editor.py` beyond the
      registration call itself (proving the seam `14` will use actually
      works generically).
- [ ] Map Editor: vaults authored on the same toolset save into
      `data/maps/vaults/`; campaigns support drag-reorder + hub flags;
      validation warnings fire for no-stairs, no-completion-trigger,
      duplicate-IDs, and the exact-text `room_id` warning from §2.4/§7.
- [ ] Data Editor: at least three distinct content-type forms driven purely
      from form-schema JSON (§5.2) with no per-type Python branching beyond
      generic widget rendering; the effect-array sub-editor is reused
      identically across at least two of them; live JSON preview reflects
      form edits in real time.
- [ ] Skin Editor: edits a widget tree matching `08-ui-runtime.md`'s exact
      schema, live preview renders it via that component's actual render
      path (not a reimplementation), palette color-picker edits
      `ui_config.json`.
- [ ] Sprite Manager exposes all 16 named `wall_0`..`wall_15` slots
      individually, matching `07-input-renderer-audio.md`'s
      `wall_variants` contract exactly (test asserts all 16 keys are
      independently assignable/read from a fixture theme file, and that
      assigning one does not affect the others).
- [ ] Audio tab imports/organizes files into `sfx/`/`music/` correctly.
- [ ] Every §7 usability item (tooltips, visible Save affordance,
      cache invalidation, non-clipping dropdowns, table column headers,
      typed ID input) is implemented and has at least one test or a
      documented manual-verification note where automated testing isn't
      practical (e.g. tooltip *presence* is testable; tooltip visual
      styling is not — test presence, note styling as manually verified).
- [ ] Context links (§2.7) implemented for both named cases (palette→Sprite
      Editor, unplaced-entity→Data Editor pre-fill).
- [ ] `pytest` green, including the headless smoke-test integration test
      in §9.

## 9. Test Plan

Unit tests per §8, per mode. Integration test (CONTRACTS.md §7.3 — for this
component, a scripted headless pygame smoke test per the task brief):

- `tests/integration/test_editor_smoke.py`: set `SDL_VIDEODRIVER=dummy`
  (headless pygame), boot `Editor` fresh, drive **New Project** to create a
  real temp-dir project, assert it lands in Map Editor with a loaded blank
  map; programmatically switch through every registered mode (Map Editor →
  Sprite Editor → Data Editor → Skin Editor → Sprite Manager → Audio tab)
  via `switch_mode`, calling `update`/`draw` once per mode against a dummy
  surface, asserting no exception anywhere in the sequence; perform one
  real save in Data Editor (create a minimal monster record via a fixture
  form-schema, save it) and assert the resulting JSON file exists on disk
  under the project's `data/entities/` with the expected `id`; finally call
  **Pack Project** and assert a `.pak` and `.pkd` land under `dist/` in the
  temp project and are each openable as valid zip files containing the
  saved monster record. This single test exercises Project Manager, the
  mode-switching shell, at least one mode's real save path, and Pack
  Project end-to-end — the "built but not wired" gate from CONTRACTS.md §7
  applied to the editor's own internal pipeline, not just the game engine's.

## 10. Open Questions & Defaults

- **Form-schema file location** (`editor/form_schemas/` vs. elsewhere):
  default as stated in §5.2/§6; if you find a stronger convention
  mid-implementation, document the change in your PR.
- **Live JSON preview editability** (read-only vs. a full escape-hatch
  editable text view that re-parses on save, mirroring the Lua visual
  quest editor's "View/Edit Script" pattern): default to read-only for
  this component's initial scope — an editable JSON escape hatch is a
  reasonable future enhancement but not required for Definition of Done;
  note whichever you build.
- **Audio tab's audio_config.json link-back**: optional per §2.6; default
  to plain import/organize only if time-constrained, and state in your PR
  whether link-back editing was included.
- **Skin Editor's reach into `engine/ui/ui_runtime.py` for live preview**:
  the one deliberate exception to "editor never imports engine's game
  loop" per §2.4 — import only the rendering/parsing logic, never
  `engine/main.py` or anything event-bus-wired to a live game session.
