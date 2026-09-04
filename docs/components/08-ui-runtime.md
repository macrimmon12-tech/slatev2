# Component 08 — UI Runtime

**Wave:** 1 (parallel).
**Depends on:** Wave 0 (`00-foundation-core.md`) only. Build against
`CONTRACTS.md` event shapes directly — do not import other Wave 1
components' code.

## 1. Scope & Boundaries

You build the **mechanism**, not the screens. `UIRuntime` is a data-driven
runtime that parses a widget-tree JSON format and renders/updates the live
panel stack every frame. The exact same tree format must work identically
whether it comes from `data/config/ui_skin.json`, a Python `create_panel()`
call, or Lua's `engine.create_panel()` call (from `10-lua-scripting-layer.md`)
— there is no second, lesser format for scripted content. This is the
single most important invariant in this doc: if you catch yourself building
a Python-only shortcut that Lua's `create_panel` couldn't also express,
stop — it isn't a valid design.

Only two screens are **yours to build concretely**: `main_menu` and
`save_select`, because they must run before Lua initializes. Every other
screen (inventory, spellbook, level-up, spell choice, dialog, shop,
journal, minimap) is created *at runtime* by a Lua script reacting to an
event — you provide the `create_panel`/`update_panel`/`destroy_panel`
runtime and the `show_panel` event contract; you do not author those
screens' JSON trees (that's `10-lua-scripting-layer.md`'s base scripts and
`11-npc-dialog-shop-content.md`'s content-specific ones).

The targeting cursor overlay is a **third, distinct thing**: not a panel,
a special engine-owned rendering mode, triggered by `spell_cast_initiated`
and torn down by `target_confirmed`/`cancel_targeting`. Build it explicitly
here.

In scope:
- `UIRuntime` (`engine/ui/ui_runtime.py`): widget-tree parser, panel stack,
  per-frame render/update, `create_panel`/`update_panel`/`destroy_panel`
  API surface (this is what `10-lua-scripting-layer.md`'s `engine.*` API
  wraps for Lua — document the exact Python signatures Lua will bind to).
- `data/config/ui_skin.json`: only the two Python-owned screen definitions
  (`main_menu`, `save_select`). Do not add other screens' trees here even
  as examples beyond one optional demonstrative stub (see §2.3).
- The widget-tree JSON schema itself (§5) — document it in full; this is
  the contract `10-lua-scripting-layer.md` and `13-editor-core-authoring.md`
  (Skin Editor) both build against.
- Targeting cursor overlay: engine-owned rendering mode, own implementation
  location under `engine/ui/` (co-located with `ui_runtime.py` since it
  shares panel-stack z-ordering concerns, though it is not itself a panel —
  document your exact file choice in your PR).

Out of scope:
- Actual tile-viewport/HUD-frame rendering — that's `07-input-renderer-audio.md`.
  You draw *into* the HUD rect the renderer hands you; you don't own the
  window or the tile grid.
- Any Lua-authored screen's actual JSON tree content (inventory.lua's panel
  definition, spellbook.lua's, dialog/shop panels) — those are
  `10-lua-scripting-layer.md` base scripts and `11-npc-dialog-shop-content.md`
  content. You build at most one demonstrative example for your own tests
  if needed to prove the runtime works; otherwise stub with a fixture.
- Visual effects (particle-like transients) — `09-animation-vfx.md`.
- Raw key→action translation — `07-input-renderer-audio.md` (you consume
  the action-level events it emits; you don't read pygame key events
  yourself, except within your two owned screens' own button/list
  navigation, which listens for the `ui`-context actions `07` documents).

## 2. Provides

### 2.1 Widget-tree runtime

```python
class UIRuntime:
    def __init__(self, event_bus, registry, screen_rect_provider): ...
    def create_panel(self, panel_id: str, tree: dict, data: dict | None = None) -> None: ...
    def update_panel(self, panel_id: str, data: dict) -> None: ...
    def destroy_panel(self, panel_id: str) -> None: ...
    def draw(self, surface) -> None: ...          # called once per frame by main loop
    def handle_ui_input(self, action: str) -> bool: ...  # returns True if consumed
```

- `create_panel` accepts a widget-tree dict (§5 schema) plus an optional
  initial `data` binding dict used to fill `{{template}}` fields (Bar
  values, Label text, List/Grid item sources). Panels stack in creation
  order; the topmost panel receives `ui`-context input first
  (`handle_ui_input` returns `True` to signal "consumed, stop propagating"
  — same convention Lua's event handling should mirror).
- `update_panel(panel_id, data)` re-binds data into an already-created
  panel's live tree without rebuilding widget objects from scratch —
  this is what a Lua script calls every time it wants to refresh, e.g., an
  HP bar's value.
- `destroy_panel` removes a panel from the stack immediately; safe to call
  on a panel_id that doesn't exist (no-op, per "absence = zero cost").
- This exact triple (`create_panel`/`update_panel`/`destroy_panel`) is what
  `10-lua-scripting-layer.md`'s `engine.create_panel`/`engine.update_panel`/
  `engine.destroy_panel` API group binds to 1:1 — keep the Python signature
  stable and document any deviation loudly since Lua's API surface is a
  hard dependency on these exact names/args.

### 2.2 The `show_panel` contract

`show_panel` (payload `panel_id, data`) is the generic "something wants a
panel shown" event per CONTRACTS.md §3.2. `UIRuntime` subscribes to it and:
1. Looks up a pre-registered tree for `panel_id` (registered via
   `create_panel` ahead of time by whoever owns that screen — Python for
   `main_menu`/`save_select`, a Lua base script for everything else at
   floor/game load time), or
2. If no tree is registered for that `panel_id` yet, no-ops silently
   (absence = zero cost — a `show_panel` emitted before its owning Lua
   script has registered the tree is not an error, just a missed frame;
   document this clearly since it's a likely source of "built but not
   wired" confusion per CONTRACTS.md §7 if a Wave 2 component's Lua script
   forgets to register before emitting).

`level_up_pending` and `spell_choice_pending` are **not** routed
automatically into `show_panel` by this component — they are raw
domain events. The **default and recommended pattern** (per
`10-lua-scripting-layer.md`'s base script library) is: a Lua base script
(`level_up.lua`, `spell_choice.lua`) subscribes to
`level_up_pending`/`spell_choice_pending` directly, builds/updates its own
widget tree via `engine.create_panel`, and manages its own lifecycle. You
provide the mechanism (`create_panel`/subscribe path) that makes this
possible; you do not build `level_up.lua`/`spell_choice.lua` yourselves.
Build **one demonstrative example only if needed for your own test
coverage** (e.g. a minimal fixture tree exercising a Bar + Button bound to
fake level-up data) — otherwise stub with a `tests/fixtures/` JSON file and
note in your PR that the real Lua panel is Wave 1's `10-lua-scripting-layer.md`
component's responsibility.

### 2.3 Python-owned screens: `main_menu`, `save_select`

Build these two concretely, registered directly in `ui_skin.json` and
loaded/created at boot before any Lua host exists.

- `main_menu`: buttons for New Game, Load Game, Quit. Emits
  `new_game_selected`, `load_game_selected`, `quit_selected` on activation
  (payloads per CONTRACTS.md §3.2 — `new_game_selected`/`load_game_selected`
  payload shape is "varies," so document exactly what you emit: e.g.
  `new_game_selected` → `{campaign_id: None}` if campaign choice happens on
  a later screen, or the chosen campaign id if main_menu itself lists them;
  pick one and state it plainly here).
- `save_select`: a List widget populated from actual files in `saves/`
  (read via a documented save-listing helper — do not read `saves/`
  directly bypassing any convention `00-foundation-core.md`'s save system
  established; if it doesn't expose a "list save files" helper, add a thin
  one here and note it in your PR since it's a natural extension of
  `engine/core/save.py`, or keep the directory listing local to this
  component's own code if you'd rather not touch foundation's files).
  Emits `save_slot_selected` with the chosen slot identifier.
- Both screens are pure widget-tree JSON, driven through the exact same
  `create_panel` path as everything else — no special-cased Python
  rendering logic for them. That's the proof the runtime is real: the two
  "must be Python" screens are still expressed as data.

### 2.4 Targeting cursor overlay

```python
class TargetingOverlay:
    def __init__(self, event_bus, screen_rect_provider): ...
    def is_active(self) -> bool: ...
    def draw(self, surface) -> None: ...
```

- Subscribes to `spell_cast_initiated` (payload `entity_id, spell_id,
  targeting_mode`): activates the overlay, switches `07-input-renderer-audio.md`'s
  `InputHandler` context to `targeting` (via a documented callback/method
  call — coordinate the exact hook, e.g. `input_handler.set_context`, in
  your PR against `07`'s doc since both components touch this transition).
  For `targeting_mode` values that don't need a tile cursor (`self`,
  `aoe_self`) the overlay may render a lighter self-highlight or nothing,
  per `03-spells-status.md`'s five targeting modes — document which modes
  get a visible cursor vs. an instant-resolve pass-through.
- Torn down on **either** `target_confirmed` or `cancel_targeting` —
  restores the previous input context (`game`).
- This is explicitly **not** a panel: it never goes through
  `create_panel`/the panel stack, has its own draw call invoked directly by
  the render loop (document the exact call order relative to
  `UIRuntime.draw()` and `07`'s tile-viewport draw — recommended: tile
  viewport → targeting overlay (world-space cursor) → HUD panels
  (screen-space), so the overlay draws over the map but under any modal
  panel).

## 3. Consumes

| Event | Behavior |
|---|---|
| `show_panel` | route to a registered panel tree per §2.2 |
| `level_up_pending` | mechanism available (create_panel), default: Lua base script under `10-lua-scripting-layer.md` owns the actual panel; UIRuntime does not auto-render one |
| `spell_choice_pending` | same as above |
| `spell_cast_initiated` | activate targeting overlay |
| `target_confirmed` | tear down targeting overlay |
| `cancel_targeting` | tear down targeting overlay |

## 4. Emits

| Event | From |
|---|---|
| `new_game_selected` | main_menu screen |
| `load_game_selected` | main_menu screen |
| `save_slot_selected` | save_select screen |
| `quit_selected` | main_menu screen (and/or `ui`-context quit action relayed from `07-input-renderer-audio.md`) |

## 5. Data Schemas

### 5.1 Widget-tree JSON — the shared contract

Node types: `Panel`, `Label`, `Bar`, `Button`, `List`, `Grid`. Every node is
a dict with `type` plus type-specific fields, plus universal layout fields
(`rect` or `x/y/w/h`, `anchor`, optional `visible_if`). Children nest under
`children: [...]` for container types (`Panel`, `List`, `Grid`).

```json
{
  "type": "Panel",
  "id": "main_menu",
  "rect": {"x": "center", "y": "center", "w": 400, "h": 320},
  "background": "panel_bg",
  "children": [
    {"type": "Label", "text": "SLATE", "rect": {"x": "center", "y": 20, "w": 400, "h": 40}, "style": "title"},
    {
      "type": "Button", "id": "btn_new_game",
      "text": "New Game",
      "rect": {"x": "center", "y": 100, "w": 200, "h": 36},
      "on_click": {"event": "new_game_selected", "payload": {"campaign_id": null}}
    },
    {
      "type": "Button", "id": "btn_load_game",
      "text": "Load Game",
      "rect": {"x": "center", "y": 150, "w": 200, "h": 36},
      "on_click": {"event": "show_panel", "payload": {"panel_id": "save_select"}}
    },
    {
      "type": "Button", "id": "btn_quit",
      "text": "Quit",
      "rect": {"x": "center", "y": 200, "w": 200, "h": 36},
      "on_click": {"event": "quit_selected", "payload": {}}
    }
  ]
}
```

Field notes:
- `rect`: numeric pixel values, or the literal strings `"center"` for `x`/`y`
  (centers within parent), resolved at draw time against the current panel
  container's rect (top-level panels resolve against `screen_rect_provider`
  — for you, the HUD rect or full screen depending on `full_screen: true`
  on the root Panel node).
- `on_click`: `{event: str, payload: dict}` — clicking emits that event on
  the shared event bus with that payload, verbatim. This is how Button
  nodes talk to the rest of the engine without any Python/Lua-specific
  glue — **the exact same field works identically whether the tree was
  built by Python, by `ui_skin.json`, or by a Lua `engine.create_panel`
  call**, because it's just an event-bus emit.
- `Bar`: `{"type": "Bar", "value_key": "hp", "max_key": "max_hp", "rect": {...}, "color": "hp_red"}`
  — `value_key`/`max_key` are looked up against the panel's bound `data`
  dict (passed to `create_panel`/`update_panel`), read fresh every
  `update_panel` call, not baked in at creation.
- `List`/`Grid`: `{"type": "List", "items_key": "inventory_items", "item_template": {...}, "rect": {...}}`
  — `items_key` looks up a list in the bound data; `item_template` is a
  widget-tree fragment repeated per item, with `{{item.field}}`
  string-interpolation into any `text` field of the template.
- `visible_if`: optional `{"data_key": "...", "equals": ...}` — simple
  conditional visibility without requiring a full expression language;
  anything more complex belongs in the emitting system's logic (send a
  different `data` shape), not in the widget tree.

### 5.2 `data/config/ui_skin.json`

```json
{
  "schema_version": 1,
  "screens": {
    "main_menu": { "...": "the tree from §5.1" },
    "save_select": {
      "type": "Panel", "id": "save_select",
      "rect": {"x": "center", "y": "center", "w": 500, "h": 400},
      "children": [
        {"type": "Label", "text": "Load Game", "rect": {"x": "center", "y": 20, "w": 500, "h": 30}},
        {
          "type": "List", "id": "save_list", "items_key": "save_slots",
          "rect": {"x": 20, "y": 60, "w": 460, "h": 300},
          "item_template": {
            "type": "Button", "text": "{{item.name}}",
            "on_click": {"event": "save_slot_selected", "payload": {"slot": "{{item.slot_id}}"}}
          }
        }
      ]
    }
  }
}
```

`configs` namespace keys this by filename stem `ui_skin` per CONTRACTS.md
§4; the `screens` dict inside is this component's own sub-structure, only
`main_menu` and `save_select` populated by you. `13-editor-core-authoring.md`'s
Skin Editor is the intended authoring tool for any *other* screen trees
saved to this same file format elsewhere in `data/`, but you do not
populate more than your two screens here.

## 6. File/Directory Ownership

You own, exclusively:
- `engine/ui/ui_runtime.py`
- `engine/ui/targeting_overlay.py` (or co-located in `ui_runtime.py` —
  your call per §1, document the choice in your PR)
- `data/config/ui_skin.json` — but only the `main_menu`/`save_select`
  entries; if this file doesn't exist yet when you start, create it with
  exactly those two keys under `screens` and nothing else.
- `engine/core/schemas/ui_skin_schema.py` (widget-tree JSON validator)
- `tests/unit/test_ui_runtime.py`, `test_targeting_overlay.py`
- `tests/fixtures/ui_skin_minimal.json`, `tests/fixtures/widget_tree_samples/`

## 7. Lessons from v1 applied here

- **"Everything is events" applies to UI construction, not just gameplay.**
  v1's cleanest win (spec §5/§6) was that Lua could create/update/destroy
  panels through the identical API and JSON format Python used internally
  — no second-class scripted-UI path. This doc's Definition of Done
  requires a test that constructs a panel from a JSON fixture *without any
  Python-specific object*, proving the format alone is sufficient — that's
  the guarantee `10-lua-scripting-layer.md` is building against.
- **"Built but not wired" (spec §11.1)** applies directly to `show_panel`:
  v1 had cases of events emitted with nothing subscribed. This doc's
  Definition of Done requires proving `show_panel` → `create_panel`-backed
  render actually happens end-to-end for at least the two owned screens,
  not just that the parser can parse a tree in isolation.
- **Hotbar active-slot highlighting was a hardcoded placeholder in v1**
  (spec §11.6, `active = False`) — a HUD-content bug, not a runtime-mechanism
  bug, so it isn't this component's job to fix, but it's a reminder that a
  `Bar`/`Button` node's "active" visual state must be data-driven
  (`data`-bound, e.g. a `highlighted_key` field checked against bound data)
  rather than ever hardcoded in the node-drawing code path.

## 8. Definition of Done

- [ ] Widget-tree parser handles all six node types (`Panel`, `Label`,
      `Bar`, `Button`, `List`, `Grid`), nested children, `rect` resolution
      (including `"center"`), and `visible_if`, with unit tests per type.
- [ ] `create_panel`/`update_panel`/`destroy_panel` implemented with the
      exact documented signatures; `update_panel` proven to re-bind data
      into an existing tree without object churn (test asserts widget
      identity is stable across an update call).
- [ ] `main_menu` and `save_select` are built as pure `ui_skin.json` trees
      with zero special-cased Python rendering — a test constructs both
      purely via `create_panel(tree_from_json)` and asserts the resulting
      button click emits the documented event with the documented payload.
- [ ] `show_panel` correctly routes to a pre-registered tree and no-ops
      silently when no tree is registered for that `panel_id` (test for
      both cases).
- [ ] `on_click` emits the exact configured `{event, payload}` onto the
      real shared event bus (not a mock) when a Button is "clicked" via a
      simulated `ui`-context confirm action.
- [ ] Targeting overlay activates on `spell_cast_initiated` and tears down
      on both `target_confirmed` and `cancel_targeting`, restoring the
      prior input context, with tests for both teardown paths.
- [ ] A minimal fixture tree loaded from raw JSON (no Python dict literal
      in the test file, an actual `.json` fixture file) round-trips through
      `create_panel` → `draw()` without error — proves the "same format for
      Lua" guarantee isn't accidentally coupled to Python dict identity or
      object references.
- [ ] `pytest` green, including the integration test in §9.

## 9. Test Plan

Unit tests per §8. Integration test (CONTRACTS.md §7.3):

- `tests/integration/test_ui_runtime_pipeline.py`: build a real event bus +
  `UIRuntime`; load `ui_skin.json`'s real `main_menu` tree via
  `create_panel`; emit a synthetic `ui`-context confirm action targeting
  the "New Game" button (through `handle_ui_input`, not by calling the
  button's handler directly) and assert `new_game_selected` is actually
  observed on the real event bus by a test subscriber; separately, emit a
  real `spell_cast_initiated` event on the bus and assert the targeting
  overlay's `is_active()` flips true, then emit `target_confirmed` and
  assert it flips back false — exercising the full subscribe→state-change
  path, not calling overlay methods directly.

## 10. Open Questions & Defaults

- **`new_game_selected`/`load_game_selected` exact payload shape**:
  CONTRACTS.md marks these "varies." Default here: `new_game_selected` →
  `{"campaign_id": null}` (campaign choice deferred to a later Lua-driven
  screen or CampaignSystem default), `load_game_selected` → `{}` (routes to
  `save_select` via `show_panel` first, then the actual load is driven by
  `save_slot_selected`). If `06-worldgen-campaign.md` documents a different
  expectation (e.g. main_menu should list campaigns directly), prefer that
  and note the deviation in your PR.
- **Save-file listing helper**: default to a small local directory-scan
  helper inside this component (`glob saves/*.json`, read minimal metadata
  for display) rather than extending `engine/core/save.py`, to avoid
  touching foundation's owned files; revisit only if `00-foundation-core.md`
  already exposes one.
- **Targeting overlay file location**: default to
  `engine/ui/targeting_overlay.py` as its own module (cleaner ownership
  boundary for `13-editor-core-authoring.md`'s Skin Editor, which never
  needs to know about it) — co-locating in `ui_runtime.py` is acceptable if
  you find it meaningfully simpler; state your choice in the PR either way.
