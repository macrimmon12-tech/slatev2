# Component 11 — NPC / Dialog / Shop Content

**Wave:** 2.
**Depends on:** `10-lua-scripting-layer.md` (the `engine.*` API surface,
panel control, and floor/campaign-scoped persistent state) **and**
`08-ui-runtime.md` (`UIRuntime`'s panel mechanism) — **both must be MERGED
before this starts.** This is a genuine code-level dependency, not just a
contract dependency: this component's Lua scripts call real `engine.*`
functions and expect real panels to actually render, so building it against
stubs would mean re-testing everything anyway once `10`/`08` land. Also has
a **soft** dependency on `06-worldgen-campaign.md`'s `VaultInjector` for NPC
placement (build and test against a fixture vault + manual entity placement
in the meantime, per CONTRACTS.md §8).

## 1. Scope & Boundaries

This is spec §5's flagship pattern: **Python knows nothing.** By the end of
v1, Python had zero system classes for NPCs, dialog, or shops — no
`NPCSystem`, `DialogSystem`, `ShopSystem` — only one generic component and
two event-emission hooks. This component's job is to build exactly that
shape in v2, faithfully, and to write the Lua that makes it a real,
playable feature.

**Python's entire involvement, and nothing more:**
1. One component, `InteractableComponent(data: dict)` — an opaque blob
   Python never reads the contents of.
2. Two engine hooks (bump-into-entity during movement, and an explicit
   interact keypress) that emit `entity_interacted` without knowing what an
   NPC, dialog, or shop *is*.
3. Populating the three registry namespaces `npcs`, `dialogs`, `shops`
   (already defined in CONTRACTS.md §4 — you don't create these namespaces,
   you're the first component to actually put content in them).

**Everything downstream is Lua:** `npc_interaction.lua` (dispatch),
`dialog_walker.lua` (graph walking), `shop.lua` (buy/sell/stock/gold).

In scope:
- `InteractableComponent` dataclass + its registration in
  `_COMPONENT_REGISTRY` (coordinate via CONTRACTS.md §5 — shared, additive
  file).
- The two `entity_interacted`-emitting hooks — see §1.1 on where these live
  and the small addition this component's PR may need to make outside its
  own primary ownership.
- `scripts/npc_interaction.lua`, `scripts/dialog_walker.lua`,
  `scripts/shop.lua` — real, playable Lua content, not stubs.
- The dialog-graph JSON schema and the shop JSON schema (§5) — nailed down
  here since `14`'s Dialog Editor is a thin GUI layer directly over the
  dialog schema and depends on it being stable.
- A couple of real, testable content examples: one shopkeeper NPC with a
  simple branching dialog tree and a small shop inventory.
- Stating, explicitly, the generalization rule spec §5 draws from this
  pattern (§6).

Out of scope:
- The `engine.*` API itself — `10` owns that; you only call it.
- `UIRuntime`'s panel rendering — `08` owns that; you only call
  `engine.create_panel`/`open_dialog_panel`/`open_shop_panel` (from `10`)
  with data.
- `VaultInjector`'s placement mechanics — `06` owns that; you only supply
  NPC vault-marker content it can place (soft dependency, §3).
- Any *other* kind of interactable (levers, readable books, crafting
  stations) — explicitly not this component's job to build; see §6.

### 1.1 The `entity_interacted` hooks

Per CONTRACTS.md §3.2, `entity_interacted` is already listed as emitted by
"input/movement (bump or interact key)" with payload `{actor_id,
target_id}`, consumed by "Lua (`npc_interaction.lua`)." That row exists in
the shared contract already — but the actual Python code that emits it
(the movement collision check that detects "player bumped into an entity
carrying `InteractableComponent`," and the input handler's dedicated
interact-key binding) lives in `07-input-renderer-audio.md`'s ownership
(`engine/input/`, movement handling), not in this component's file
ownership.

**If, by the time this component starts, `07`'s merged code does not yet
emit `entity_interacted` in exactly this shape** (checking for
`InteractableComponent` presence on the bump/interact target and emitting
`{actor_id, target_id}`), **this component's PR is the one that adds that
emission**, since this is the component that actually needs it and no
other component has a reason to add it first. This is a small, clearly
flagged addition to a file outside this component's primary ownership —
call it out explicitly in the PR description (which lines, which file,
why) rather than silently touching `07`'s files. Do not add anything else
to `07`'s files beyond this one emission point.

## 2. Provides

### 2.1 `InteractableComponent`

```python
# engine/systems/interaction.py — a small module housing only the
# component + the two hook hand-offs, not a "system" in the gameplay
# sense (no update loop, no subscriptions of its own beyond what §1.1
# describes) — deliberately thin per spec §5's "Python knows nothing."

@component
@dataclass
class InteractableComponent:
    data: dict[str, Any]
    # Opaque to Python. Lua (npc_interaction.lua) is the only reader.
    # Conventional (but Python-invisible) shape used by this component's
    # own Lua content — see §5.3 — but Python never validates or branches
    # on this dict's contents. A FUTURE interactable kind (§6) can put
    # whatever shape it wants in here; Python's job stops at "this entity
    # has interactable data, hand it to Lua."
```

Registered in `_COMPONENT_REGISTRY` (add to `00-foundation-core.md`'s file,
alphabetically, per CONTRACTS.md §5) — this is a **required addition**,
flagged explicitly since it's the one Python-visible artifact this whole
component produces.

### 2.2 `npc_interaction.lua` — dispatch

Subscribes to `entity_interacted` at load time (self-registering, per
`10`'s loading convention). On receipt:

1. Reads `engine.get_component(payload.target_id, "InteractableComponent")`.
   If absent, no-op (bumped/interacted-with entity isn't interactable —
   normal case for e.g. a monster).
2. Branches on `data.kind` (a convention internal to this Lua file and the
   content JSON it reads — never inspected by Python):
   - `"npc"` → looks up `engine.get_component(target_id, "InteractableComponent").data.dialog_id"`,
     falls back to `data.npc_id`'s `dialogs` registry entry if present, and
     calls into `dialog_walker.lua`'s public entry point (a plain Lua
     function the two scripts share via a small shared table — see §2.5)
     to open the conversation.
   - `"shop"` → same shape, dispatching to `shop.lua`'s entry point with
     `data.shop_id`.
   - anything else / missing `kind` → logs via `engine.log_message` at
     `debug` category and no-ops (absence = zero cost: an interactable
     with data npc_interaction.lua doesn't recognize is not an error).

### 2.3 `dialog_walker.lua` — graph walking

Owns the dialog **state machine**: current node, choice resolution,
branching conditions, curse-reveal-in-dialog (spec §5's identify-adjacent
behavior — a dialog node's condition can check
`engine.get_component(actor_id, "InventoryComponent")`-derived identify
state, exposed by `04`'s soft-dependency call boundary the same way `10`
documents it, or more simply by checking a floor/campaign state flag
`10`'s API already provides — this component's own script decides which,
and either is acceptable since both route through documented `engine.*`
calls).

Public shape (called by `npc_interaction.lua`):
```lua
DialogWalker.open(actor_id, target_id, dialog_id)
```

Behavior:
- Loads the dialog graph via `engine.get_component` is NOT how JSON content
  is read — dialog graphs are **registry content**, not entity data. This
  script reads them via a small registry-read wrapper `10` is expected to
  expose generically for any namespace (`engine.get_registry_entry("dialogs",
  dialog_id)` — if `10`'s merged API doesn't have this exact name, use
  whatever generic registry-read primitive it does expose; flag the
  mismatch in this component's PR rather than reinventing a second
  registry-access path).
- Tracks the current node id in **floor-scoped persistent state**
  (`engine.set_floor_state("dialog_" .. target_id .. "_node", node_id)`) —
  per-NPC, per-floor, so a half-finished conversation with a specific NPC
  survives a save/reload with zero Python involvement (§4's DoD test).
- Emits `dialog_opened` `{entity_id, dialog_id, node_id}` on open,
  `dialog_choice_selected` `{entity_id, dialog_id, node_id}` per choice, and
  `dialog_closed` `{entity_id, dialog_id, node_id}` on exit — all three
  already in CONTRACTS.md's event table (attributed to Lua/`dialog_walker.lua`).
- Calls `engine.open_dialog_panel(data)` (from `10`) with the current
  node's text + choice list as `data`, and `engine.update_panel`/
  `engine.destroy_panel` as the conversation advances/ends — using `08`'s
  widget-tree format exclusively through `10`'s wrapper, never a bespoke
  rendering path.
- Branching conditions (§5.1's `choices[].condition`) are evaluated by
  calling `engine.get_stat`/`engine.get_floor_state`/`engine.get_campaign_state`
  as needed — the condition grammar is a small Lua expression evaluated
  with those three read paths available as locals (see §5.1), not a new
  formula language; this keeps dialog authoring inside the same "read via
  engine.*" discipline as everything else in this component.

### 2.4 `shop.lua` — buy/sell/stock/gold

Public shape (called by `npc_interaction.lua`):
```lua
Shop.open(actor_id, target_id, shop_id)
```

Behavior:
- Loads the shop definition via the same generic registry-read primitive
  as `dialog_walker.lua` (namespace `shops`).
- Tracks **current stock** and **shop gold pool** in floor-scoped state
  (`engine.set_floor_state("shop_" .. shop_id .. "_stock", stock_table)`,
  `..."_gold"`) — a shop's stock and gold persist per floor instance of
  that shop, same mechanism as dialog progress, same zero-Python-code
  guarantee.
- Subscribes (at `Shop.open` time, or once at load and gated internally —
  either is acceptable, document your choice) to `buy_item`/`sell_item`
  emitted by the shop panel's button callbacks (via `08`'s panel event
  wiring — a button's `data` payload round-trips back through
  `engine.subscribe` the same way any other UI-driven event does per spec
  §6). On `buy_item`: checks price (formula-driven, §5.2) against the
  actor's gold (`engine.has_item(actor_id, "gold", price)`), on success
  calls `engine.remove_item(actor_id, "gold", price)` +
  `engine.grant_item(actor_id, item_id, 1)` + adds `price` to the shop's
  gold pool, decrements stock, and emits `trade_completed`
  `{entity_id, shop_id, item, price}`. On `sell_item`: mirror image,
  gold flows from the shop's pool to the actor (capped by the shop's
  current gold — a shop that's out of gold can't buy from the player,
  a deliberate, documented content rule, not a bug).
- Restock: on shop open, if a `restock_interval_floors` (or similar,
  see §5.2) condition is met since the last restock (tracked in the same
  floor-scoped state), regenerates stock from the shop definition's
  `stock_table` and emits `shop_refreshed` `{entity_id, shop_id}`.
- Emits `shop_opened` `{entity_id, shop_id, item, price}` on open (`item`/
  `price` absent/null for the open event itself, present for
  buy/sell/trade events — matches CONTRACTS.md's shared payload row for
  this event group).

### 2.5 Shared dispatch table between the three scripts

Since scripts are separate files loaded independently (per `10`'s loading
mechanism, each executes its own top-level code once), `npc_interaction.lua`
needs a way to call into `dialog_walker.lua`/`shop.lua`'s "open" functions
without a Lua `require` (nulled by the sandbox — see `10`§2.1). Convention:
each script that wants to expose a callable entry point to *other scripts*
(not to Python) registers it on a single shared global table the sandbox
permits scripts to create themselves, e.g. `SLATE = SLATE or {}` at the top
of each file, then `SLATE.DialogWalker = { open = function(...) ... end }`.
This is plain Lua (no engine API needed) and works fine under the sandbox
since `SLATE` is just a global table scripts agree by convention to share —
document this convention here since it's this component's own scripts that
first need cross-script calls; `10`'s example scripts don't need it and
don't establish the pattern.

## 3. Consumes

- `10-lua-scripting-layer.md`: the full `engine.*` surface — specifically
  `engine.get_component`, `engine.subscribe`/`emit`, `engine.get_floor_state`/
  `set_floor_state`, `engine.grant_item`/`remove_item`/`has_item`,
  `engine.open_dialog_panel`/`open_shop_panel`/`create_panel`/`update_panel`/
  `destroy_panel`, `engine.log_message`. Hard dependency — must be merged.
- `08-ui-runtime.md`: `UIRuntime`'s panel stack, specifically that a
  panel's `Button` widget can round-trip a click into an emitted event
  (`buy_item`/`sell_item`/`dialog_choice_selected`) that Lua can subscribe
  to — hard dependency, must be merged.
- `entity_interacted` — from `07-input-renderer-audio.md`'s
  movement/input handling, per CONTRACTS.md §3.2's existing row. Soft in
  the sense that if it's not wired yet at the exact shape needed, this
  component's own PR adds it (§1.1) rather than blocking.
- `06-worldgen-campaign.md`'s `VaultInjector` — soft dependency for placing
  NPC entities via vault markers. Until merged, place the example NPC
  content in your own test fixture data/maps and via direct
  `world.create_entity` calls in tests rather than blocking on real vault
  placement.
- `04-inventory-items-loot.md`'s `InventoryComponent`/item grant-remove
  entry points — indirectly, via `10`'s `engine.grant_item`/`remove_item`/
  `has_item` wrappers. You never import `04` directly.

## 4. File/Directory Ownership

You own, exclusively:
- `scripts/npc_interaction.lua`, `scripts/dialog_walker.lua`,
  `scripts/shop.lua`.
- `engine/systems/interaction.py` (the `InteractableComponent` dataclass
  +, only if needed per §1.1, the small addition to `07`'s input/movement
  module — flagged separately in the PR, not silently folded in).
- `data/entities/npcs/*.json` — at least one real NPC (§5.3).
- `data/dialogs/*.json` — at least one real dialog graph (§5.1).
- `data/shops/*.json` — at least one real shop (§5.2).
- `engine/core/schemas/dialog.schema.json`,
  `engine/core/schemas/shop.schema.json` (per CONTRACTS.md §9).
- `tests/unit/test_interaction_component.py`.
- `tests/integration/test_npc_dialog_shop_pipeline.py`.

You add entries to (shared, additive-only):
- `engine/core/save.py:_COMPONENT_REGISTRY` — add `InteractableComponent`,
  alphabetically.
- `data/config/ui_skin.json` — only if `08` hasn't already authored generic
  `"dialog"`/`"shop"` screen entries by the time this starts (per `10`'s
  §2.2 note that `10`'s `open_dialog_panel`/`open_shop_panel` expect these
  to exist); add minimal ones if missing, flagged in your PR as a
  cross-file addition to `08`'s config file.
- `docs/CONTRACTS.md` §3.2 — none expected; every event this component
  emits (`entity_interacted` consumption, `dialog_opened`,
  `dialog_choice_selected`, `dialog_closed`, `shop_opened`, `buy_item`,
  `sell_item`, `trade_completed`, `shop_refreshed`) is already in the
  table. Confirm, don't add, unless you find a genuine gap.

## 5. Data Schemas

### 5.1 Dialog graph JSON (`data/dialogs/*.json`)

```json
{
  "id": "shopkeeper_mira_dialog",
  "start_node": "greet",
  "nodes": {
    "greet": {
      "text": "Welcome, traveler. Looking to trade, or just passing through?",
      "choices": [
        {"text": "Show me your wares.", "action": "open_shop", "shop_id": "mira_general_store"},
        {"text": "Tell me about this place.", "goto": "lore_1"},
        {"text": "Just passing through.", "goto": "end"}
      ]
    },
    "lore_1": {
      "text": "This outpost has stood since the old kingdom fell. Most who settle here are running from something.",
      "choices": [
        {"text": "Running from what?", "goto": "lore_2", "condition": "not campaign.mira_lore_told"},
        {"text": "Interesting. Anyway...", "goto": "greet"}
      ]
    },
    "lore_2": {
      "text": "That's a story for another time, perhaps once you've earned my trust.",
      "on_enter": [{"campaign_state": "mira_lore_told", "value": true}],
      "choices": [
        {"text": "Fair enough.", "goto": "greet"}
      ]
    },
    "end": {
      "text": "Safe travels.",
      "choices": []
    }
  }
}
```

**Field vocabulary (binding for `14`'s Dialog Editor):**

| Field | Meaning |
|---|---|
| `id` | registry id, matches filename by convention |
| `start_node` | node id `dialog_walker.lua` opens on first visit (no persisted floor-state node yet) |
| `nodes` | map of node id → node object |
| `nodes[].text` | narrative text shown in the dialog panel |
| `nodes[].on_enter` | optional list of side-effect directives run when this node is reached — currently supports `{"campaign_state": key, "value": v}` and `{"floor_state": key, "value": v}` (written via `engine.set_campaign_state`/`set_floor_state`); extend additively, don't overload `choices` for this |
| `nodes[].choices` | ordered list; empty list = terminal node (conversation ends when reached, or a `[Leave]` choice is implicit — `dialog_walker.lua`'s call, document which in code) |
| `choices[].text` | button label |
| `choices[].goto` | next node id (mutually exclusive with `action`) |
| `choices[].action` | one of a small fixed vocabulary: `"open_shop"` (needs `shop_id`), `"close"` (ends the conversation) — extend additively if a real content need surfaces, don't invent per-dialog custom actions in the schema itself (a genuinely custom action belongs in a `script_ref` field pointing at a small Lua hook, not a new JSON action keyword per use case) |
| `choices[].condition` | optional string, a small boolean expression over `campaign.<key>`/`floor.<key>` (backed by `engine.get_campaign_state`/`get_floor_state`) and `stat.<name>` (backed by `engine.get_stat`) — a choice with a false condition is hidden from the panel, not shown-but-disabled |

**Curse-reveal-in-dialog (spec §5):** modeled as an ordinary `condition`
checking whatever curse/identify flag `04`'s InventoryComponent exposes
(via `engine.get_component`/a floor-state mirror the shop or dialog script
sets when an item is inspected) — no special-case field needed in the
schema; it's just content using the existing condition grammar. Document
the exact flag name/shape your example content uses in the fixture file's
own comments (JSON has none — use the dialog's `id` naming or a sibling
`README` note in `data/dialogs/` if a convention needs explaining).

### 5.2 Shop JSON (`data/shops/*.json`)

```json
{
  "id": "mira_general_store",
  "starting_gold": 150,
  "restock_interval_floors": 3,
  "price_formula": {
    "buy": "base_price * 1.15",
    "sell": "base_price * 0.5"
  },
  "stock_table": [
    {"item_id": "healing_potion", "weight": 10, "restock_quantity": [3, 6]},
    {"item_id": "long_sword", "weight": 3, "restock_quantity": [1, 1]},
    {"item_id": "torch", "weight": 8, "restock_quantity": [4, 8]}
  ]
}
```

**Field vocabulary (binding for `14`):**

| Field | Meaning |
|---|---|
| `id` | registry id |
| `starting_gold` | shop's gold pool on first visit (floor-state seeded from this the first time `Shop.open` runs for this shop instance) |
| `restock_interval_floors` | how many *player floor-transitions since last restock of this shop instance* before `shop.lua` regenerates stock on next open — a plain counter compared against a floor-state timestamp/counter, not wall-clock time |
| `price_formula.buy` / `.sell` | formula strings evaluated via the same `eval_formula`-style convention as everywhere else in the project — `base_price` comes from the item definition (an assumed field `04` exposes; flag in your PR if `04`'s item schema doesn't already have one, since a shop needs *some* base price source and this doc doesn't own item JSON) |
| `stock_table` | weighted entries, same shape family as `01`'s/`04`'s `loot_table` convention (`item_id`, `weight`, plus `restock_quantity` as a `[min, max]` range rolled on each restock) — reusing the established weighted-table pattern rather than inventing a new one |

### 5.3 Example NPC (`data/entities/npcs/mira.json`)

```json
{
  "id": "mira",
  "display_name": "Mira",
  "sprite_ref": "npc_mira.png",
  "interactable": {
    "kind": "npc",
    "dialog_id": "shopkeeper_mira_dialog"
  }
}
```

Loaded into the `npcs` namespace (`data/entities/npcs/`, per
CONTRACTS.md §4). The `interactable` block is copied verbatim into this
NPC entity's `InteractableComponent.data` at spawn time (by whatever
spawns NPC entities — `06`'s `VaultInjector`, or this component's own test
harness while `06` isn't merged) — Python copies the dict; it never reads
inside it.

## 6. Generalizing the pattern (documentation/precedent, not a build item)

Spec §5 states this explicitly and this doc restates it as a binding
design rule for the whole project going forward, not just a historical
note: **any future "kind of interactable thing"** — a lever, a readable
book, a crafting station, a shrine — **should default to being a new vault
marker type interpreted by a new Lua script reading a new
`InteractableComponent.data.kind`, not a new Python component or system.**
The only Python-side change a new interactable kind should ever need is,
at most, a new vault marker character/tag `06`'s `VaultInjector` maps to an
`InteractableComponent` with a different `kind` value — never a new
component class, never a new event, never a new Python system. This
component does not build any of those future examples; it exists so that
when one is needed, its author has a working, faithful reference to copy
the shape from.

## 7. Lessons from v1 applied here

- **This was the cleanest system boundary v1 produced — don't regress it
  by adding "just one" Python-side special case.** It is tempting, e.g.,
  to give `InteractableComponent` a typed `kind: Literal["npc", "shop"]`
  field so Python can validate it — resist this. The whole value of the
  pattern is that Python's schema knowledge of `data`'s contents is
  exactly zero; a `kind` enum in Python is a foothold for the next
  "helpful" special case.
- **"Built but not wired" applies here too.** A dialog graph and a shop
  JSON file that exist but that no vault ever places an NPC to trigger are
  exactly the v1 loot-pipeline failure shape. §8's integration test must
  drive the *actual* `entity_interacted` path (a real bump or interact-key
  event), not call `DialogWalker.open` directly, to prove the hooks are
  really wired.
- **v1 shipped exactly one NPC/dialog/shop for its entire life** (spec
  §11.6) — content volume is explicitly out of scope for this rebuild
  (spec §12) too. This doc asks for "a couple of real, testable examples,"
  not a content library; don't over-invest here at the expense of the
  schema/mechanism work, which is the part `14` actually depends on.

## 8. Definition of Done

- [ ] `InteractableComponent` implemented, registered in
      `_COMPONENT_REGISTRY`, and a test proves Python code never branches
      on `.data`'s contents anywhere in this component's own source
      (e.g. a lint/grep test scanning `engine/systems/interaction.py` for
      any conditional keyed on `data["kind"]` or similar — there should be
      none; only Lua branches on `kind`).
- [ ] `entity_interacted` is confirmed emitted (by `07`'s existing code) or
      added by this component's PR (§1.1) with the documented payload
      shape, for both the bump-into-NPC and explicit-interact-key paths.
- [ ] `npc_interaction.lua` dispatches correctly to `dialog_walker.lua`/
      `shop.lua` based on `InteractableComponent.data.kind`, and no-ops
      cleanly for an interactable with an unrecognized `kind`.
- [ ] `dialog_walker.lua` walks a real multi-node graph (§5.1's example),
      resolves at least one conditional choice correctly (shown/hidden
      based on campaign state), and emits `dialog_opened`/
      `dialog_choice_selected`/`dialog_closed` with correct payloads.
- [ ] `shop.lua` completes a real buy and a real sell against the example
      shop (§5.2), correctly moves gold and items via `10`'s
      `engine.grant_item`/`remove_item`, decrements/restocks stock on the
      documented schedule, and emits `shop_opened`/`buy_item`/`sell_item`/
      `trade_completed`/`shop_refreshed` with correct payloads.
- [ ] **Zero-special-case persistence test**: save mid-dialog (a non-start
      node current) and with modified shop stock/gold, reload, and assert
      both survived — with a code-review-level check (stated in the PR,
      not just a comment) that no Python code anywhere was written
      specifically to make NPC/dialog/shop state survive save/load; it
      works purely because `LuaFloorStateComponent` (from `10`) is a
      generic component like any other.
- [ ] Dialog and shop JSON schemas validated via `jsonschema` per
      CONTRACTS.md §9, with round-trip tests over the example fixture
      files under `data/dialogs/` and `data/shops/`.
- [ ] `pytest` green.

## 9. Test Plan

Unit tests:
- `InteractableComponent` serialization round-trip
  (`to_dict`/`from_dict`) with an arbitrary nested `data` dict, including
  one containing lists and booleans (proves the "opaque blob" really is
  opaque all the way through save/load, not just at the Python API
  surface).
- Dialog graph traversal logic (choice resolution, condition
  true/false branches, terminal node handling) — can be tested by driving
  `dialog_walker.lua`'s exposed `SLATE.DialogWalker` table directly through
  a lightweight Lua test harness (or through `10`'s `LuaHost` with a
  fixture `World`), whichever this component's test setup finds more
  ergonomic; document the choice.
- Shop price-formula evaluation (`buy`/`sell` formulas against a fixture
  item's `base_price`), stock weighted-roll determinism under a fixed RNG
  seed, restock-interval counting.

Integration test (`tests/integration/test_npc_dialog_shop_pipeline.py` —
required per CONTRACTS.md §7.3, drives the real path end-to-end):
- Build a real `World` + `EventBus` + `DataRegistry` (loaded from a
  fixture `data/` tree including `mira.json`, the example dialog, and the
  example shop) + a real, booted `10`'s `LuaHost` with `scripts/*.lua`
  loaded (including this component's three scripts) + `08`'s real
  `UIRuntime` (or its documented fixture substitute if not yet merged in
  this CI run, per CONTRACTS.md §8 — note which).
- Spawn a player entity and an NPC entity (`mira.json`'s data, with
  `InteractableComponent` populated) at adjacent tiles.
- Drive the actual player movement path that causes a bump (or emit the
  real interact-key event through `07`'s handler, not
  `event_bus.emit("entity_interacted", ...)` directly — the point is to
  prove the hook, not bypass it) and assert: a dialog panel was created
  (via `08`'s panel stack, inspected through its own test-facing state,
  not mocked), the correct start node's text is present in the panel's
  bound data, and choosing the "Show me your wares" choice transitions
  into a shop panel with `mira_general_store`'s stock visible.
- Complete one buy transaction through the shop panel's button-click →
  `buy_item` event path (simulated at the event-bus level is acceptable
  here, since driving real pygame input is `07`'s concern, not this
  component's — document this boundary in the test) and assert the
  player's inventory gained the item, gold decreased correctly, and the
  shop's floor-state gold pool increased.
- Save (`serialize_floor_snapshot`), tear down the `World`, rebuild, and
  `restore_floor_snapshot`; assert the shop's post-purchase stock/gold and
  any advanced dialog node state are both exactly as they were before the
  save — the zero-special-case persistence proof required by §8.
