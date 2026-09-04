# Component 10 — Lua Scripting Layer

**Wave:** 1 (parallel with everything else).
**Depends on:** Wave 0 (`00-foundation-core.md`) + `docs/CONTRACTS.md`. **Soft**
dependencies only on `01-stats-combat.md`, `03-spells-status.md`,
`04-inventory-items-loot.md`, `05-progression-vision.md`,
`06-worldgen-campaign.md`, `08-ui-runtime.md` — none of these block starting
or finishing this component. Per CONTRACTS.md §8, build and test the sandbox,
the full `engine.*` API surface, and the script-loading mechanism against
fixtures/stubs of those systems' documented call boundaries. When the real
systems merge, swap the stubs for real imports and re-run the integration
test in §8 — that swap is expected to require zero changes to the `engine.*`
Lua-facing signatures, only to the Python glue behind them.

## 1. Scope & Boundaries

You are building the entire bridge between Lua content and the Python
engine: the sandboxed `lupa` runtime, the complete `engine.*` API surface
every Lua script (base library, NPC/dialog/shop scripts from `11`, quest
editor output from `14`) is written against, the mechanism that discovers
and loads `scripts/*.lua`, and the schema for `data/scripts/snippets.json`
that `14`'s Visual Quest Editor palette is generated from.

You are a **host**, not a gameplay system. You own no gameplay rules — every
`engine.*` function is a thin, documented wrapper around another
component's public entry point (or the event bus). If you find yourself
implementing gameplay logic (e.g. deciding *how much* damage a spell does),
that belongs in `01`/`03`/`04`, not here; you only route the call.

In scope:
- `engine/lua/lua_host.py` — `LuaHost`: sandbox construction, the merged
  `engine` Lua table, script directory loading, error containment,
  floor/campaign context tracking.
- `engine/lua/api/` — one Python module per `engine.*` API group (§2.2).
- The `scripts/` directory loading mechanism (discovery, sandboxing,
  self-registration convention, error containment per script).
- `data/scripts/snippets.json`'s **schema** (§5.1) plus one or two example
  entries — not a full snippet library. `14` is the schema's real consumer;
  it must be nailed down here since `14` cannot start until it can rely on
  this shape.
- The `-- @node ... -- @endnode` structured-comment marker convention
  embedded in snippet `lua_template` strings (§5.1) — this is the mechanism
  `14`'s round-trip parser keys off of, and since it lives inside
  `snippets.json` entries, it is this component's decision to make, not
  `14`'s.
- Two minimal example scripts under `scripts/examples/` proving the
  mechanism works end-to-end (subscribe → call an `engine.*` function →
  observable effect). Real content (`npc_interaction.lua`, `dialog_walker.lua`,
  `shop.lua`) is `11`'s job; further snippet-driven scripts are `14`'s
  generated output. You prove the pipe works; you do not fill it.

Out of scope (leave as a documented boundary):
- Any actual gameplay logic behind the wrappers (combat math, AI decisions,
  inventory rules, world generation) — those are `01`–`06`.
- The base script *library* content (`npc_interaction.lua` etc.) — `11`.
- The Visual Quest/Dialog Editor GUI — `14`.
- `ui_skin.json`'s widget-tree schema itself — `08` owns that format; you
  only pass it through unmodified from Lua to `08`'s `UIRuntime`.

**On CONTRACTS.md rule 4 ("systems never call each other directly"):** that
rule governs Python system-to-system calls. A Lua script calling
`engine.deal_damage(...)`, which internally calls `01`'s
`EffectResolver.apply_effect_list`, is **not** a system-to-system call — it
is content calling through a defined, documented engine boundary, exactly
the same shape as a spell JSON's effect list being resolved by `01`, or an
item's `stat_modifiers` being read by `04`. State this explicitly in your
code comments at each wrapper call site so a future reviewer doesn't
mistake the sanctioned exception for a rule violation.

## 2. Provides

### 2.1 Sandboxing (`LuaHost`)

```python
# engine/lua/lua_host.py

class LuaHost:
    def __init__(self, world: World, event_bus: EventBus, registry: DataRegistry): ...

    def boot(self) -> None:
        """Creates exactly one lupa.LuaRuntime for the process lifetime
        (constructed once, like a gameplay system — not per script, not
        per floor). Sandboxing happens here, BEFORE the `engine` table is
        assigned, in this exact order:
          1. Create lua_runtime = lupa.LuaRuntime(unpack_returned_tuples=True).
          2. Null the dangerous surface on lua_runtime.globals():
             io, os, package, require, load, loadfile, dofile, debug,
             rawget, rawset  -> set each to None/nil.
          3. Verify (assert, not just hope) that math, string, table,
             ipairs, pairs, type, tostring, tonumber, pcall, error, assert,
             select, unpack are still present and callable.
          4. Build the merged `engine` table (§2.2) and assign it as the
             ONLY new global. Nothing else is added to the global namespace.
          5. Call load_scripts_dir() (§2.3).
        Steps 2-3 happen for every LuaRuntime this process ever creates —
        there is exactly one sandboxing code path, not one per call site.
        """

    def set_context(self, floor_entity_hint: int | None, player_entity_id: int) -> None:
        """Rebinds which entities back the floor-scoped and campaign-scoped
        state APIs (§2.2's persistent-state group). Called by LuaHost ITSELF
        — not exposed to Lua — as an internal handler subscribed to
        `floor_changed`. See §2.4 for exactly what it does."""

    def clear_floor_subscriptions(self) -> None:
        """Unsubscribes every event-bus token registered via engine.subscribe
        with tag_floor_scoped=True. Called automatically by LuaHost's own
        `floor_changed` handler, in the same handler as set_context, BEFORE
        rebinding context (so scripts don't receive stale-floor events
        during the rebind). Also exposed as engine.clear_floor_subscriptions()
        so a script can opt to clear early (e.g. a scripted cutscene ending
        before the actual floor transition event fires)."""

    def run_protected(self, lua_callable, *args) -> Any | None:
        """THE single call path used everywhere a Lua callable is invoked
        from Python (event dispatch, script load, panel callbacks). Wraps
        the call in try/except catching lupa.LuaError and any Python
        exception raised by an engine.* wrapper (e.g. the set_stat
        blocklist rejection). On failure: log at ERROR with the Lua
        traceback if available, the script's source file if known, and the
        event/context that triggered the call; swallow the exception;
        return None. This is what makes 'a broken script never crashes the
        game, only itself' true — there is exactly one place this
        guarantee is implemented, not one per call site."""
```

**Nulled at init (before `engine` exists):** `io`, `os`, `package`,
`require`, `load`, `loadfile`, `dofile`, `debug`, `rawget`, `rawset`.

**Kept:** `math`, `string`, `table`, `ipairs`, `pairs`, `type`, `tostring`,
`tonumber`, `pcall`, `error`, `assert`, `select`, `unpack`.

This is security-critical, not a style choice: a script executing
`os.execute("rm -rf /")` or `io.open(...)` must be structurally impossible,
not merely discouraged. §7's Definition of Done requires a test that
asserts every nulled name is actually absent (`nil`) in the sandboxed
environment, not just "unused by convention," and a second test that a
script explicitly trying to reach one of them fails cleanly (caught error,
process alive, no filesystem/process side effect).

### 2.2 The `engine.*` API surface

All functions live in one flat Lua table `engine`, built by merging the
per-group Python dicts returned by each module under `engine/lua/api/`:

```python
# engine/lua/lua_host.py (excerpt)
def _build_engine_table(lua_runtime, ctx: "ApiContext") -> Any:
    merged: dict[str, Callable] = {}
    for module in (entity_api, lifecycle_api, combat_api, ai_api,
                   inventory_api, world_api, state_api, narrative_api,
                   panel_api, dialog_shop_api, event_api):
        merged.update(module.build(ctx))
    return lua_runtime.table_from(merged)
```

`ApiContext` is a small dataclass carrying `world`, `event_bus`, `registry`,
and the mutable current-floor/current-campaign entity ids `set_context`
rebinds (§2.4) — every API module closes over the same `ctx` instance so
rebinding it once updates every group consistently.

Grouped below by area, with the Python entry point(s) each wraps. Where the
callee lives in a component not yet merged, the wrapper's Python side is
written against the documented signature and stubbed per CONTRACTS.md §8
(logged once, no-op or a safe fixture default) until that component lands —
marked **(soft)** below.

#### Entity read/write — `engine/lua/api/entity.py`

```
engine.get_position(entity_id) -> {x, y} | nil
engine.set_position(entity_id, x, y)
engine.get_component(entity_id, component_name) -> table | nil   -- read-only copy
engine.has_component(entity_id, component_name) -> bool
engine.get_stat(entity_id, stat_name) -> number
engine.set_stat(entity_id, stat_name, value)
```

- `get_position`/`set_position` wrap `World.get_component`/`add_component`
  on `PositionComponent`, AND on write, `SpatialHash.move` (foundation,
  hard dependency — `SpatialHash` is authoritative for position per
  CONTRACTS.md §2.5; `set_position` must never update `PositionComponent`
  without also calling `SpatialHash.move`, exactly like every Python
  system must).
- `get_component` returns a plain Lua table copy (via `dataclasses.asdict`)
  of any registered component — read-only by convention (mutating the
  returned table has no effect back on the entity; there is deliberately
  no generic `set_component`, since arbitrary component mutation from
  content would bypass every system's own invariants).
- `get_stat` **(soft, 01)** wraps `01`'s `StatsSystem.get_stat(entity_id,
  stat_name, world)` exactly.
- `set_stat` **(soft, 01)** — see the guardrail below. This is the
  security/correctness-critical wrapper of this whole component, not just
  a convenience function.

**`set_stat` enforcement (binding, testable):** `set_stat` is for
non-derived flags and counters only — quest counters, faction reputation,
"times talked to," any script-authored bookkeeping number that nothing
else in the engine ever composes via `StatsComponent.modifiers`. It must
**never** be usable for HP, MP, damage, or any other combat-derived stat —
those always route through `engine.deal_damage`/`engine.apply_effect` so
they hit the same resolution pipeline (`01`'s `EffectResolver`) as
everything else. v1 documented this convention but never enforced it in
code, and it got violated. v2 enforces it at the API boundary:

```python
# engine/lua/api/entity.py
COMBAT_DERIVED_STAT_NAMES = frozenset({
    "hp", "max_hp", "mp", "max_mp",
    "damage_min", "damage_max", "damage",
    "armor", "strength", "dexterity", "intelligence", "constitution",
    "vision_range", "evasion", "accuracy", "crit_chance", "crit_multiplier",
})

def set_stat(entity_id: int, stat_name: str, value: float) -> None:
    if stat_name.lower() in COMBAT_DERIVED_STAT_NAMES:
        raise LuaApiError(
            f"set_stat rejected for '{stat_name}': combat-derived stats "
            "must go through engine.deal_damage or engine.apply_effect."
        )
    stats = world.get_component(entity_id, StatsComponent)
    if stats is None:
        raise LuaApiError(f"entity {entity_id} has no StatsComponent")
    stats.base[stat_name] = value
```

`COMBAT_DERIVED_STAT_NAMES` is a **shared, additive-only** list — `01` and
`05` are expected to extend it (via a PR touching this file) if they add
new combat/attribute stat names not already listed here, the same way
`_COMPONENT_REGISTRY` and the CONTRACTS.md event table are shared/additive.
Note it in your PR the same way.

**Deliberate, narrow exception to CONTRACTS.md rule 9** ("base stat fields
are never mutated directly"): `set_stat`'s direct write to
`StatsComponent.base[stat_name]` is exactly this — a direct mutation. It is
safe specifically *because* the blocklist guarantees it only ever touches
names nothing else in the engine ever attaches a modifier to; there is no
composability being broken in practice, only in the letter of the rule.
Flag this explicitly for `01`'s and CONTRACTS.md reviewers rather than
letting it look accidental — see §9 for the fallback if `01` objects to
sharing `StatsComponent.base` for this.

#### Lifecycle — `engine/lua/api/lifecycle.py`

```
engine.spawn(entity_def_id, x, y) -> entity_id
engine.destroy(entity_id)
```

Wraps `World.create_entity` + populating components from
`registry.get("entities", entity_def_id)` (mirrors however worldgen/loot
spawn monsters — **(soft, 06)**, documented spawn shape to match once `06`
merges) and emits `entity_spawned` `{entity_id, kind, position}` per
CONTRACTS.md §3.2. `destroy` wraps `World.destroy_entity` +
`SpatialHash.remove`.

#### Combat / effects — `engine/lua/api/combat.py`

```
engine.deal_damage(source_id, target_id, amount, damage_type)
engine.apply_effect(effect_or_effect_list, source_id, target_id)
```

Both **(soft, 01)**. `deal_damage` builds
`{"type": "damage", "amount": amount, "damage_type": damage_type}` and
calls `01`'s `effects.apply_effect_list([...], source_id, target_id, world,
event_bus)`. `amount` may be a plain Lua number or a formula string
(`"2d6 + (INT * 0.3)"`) — both pass straight through to `01`'s formula
evaluation unchanged, no special-casing here. `apply_effect` accepts either
a single effect table or an array of effect tables (normalized to a list
Python-side) and calls the same `apply_effect_list` entry point — this is
**the only** path for HP/MP/damage from Lua; there is no other way to hurt
or heal an entity from a script, by design (see `set_stat` above).

#### AI control — `engine/lua/api/ai.py`

```
engine.override_ai_behavior(entity_id, behavior, params)
engine.get_ai_behavior(entity_id) -> {behavior, state, params}
engine.set_ai_enabled(entity_id, enabled)
```

**(soft, 02 — 02-ai-system.md is not yet written as of this doc; the exact
Python entry point names below are this component's documented assumption,
to be verified once 02 merges, same treatment 01 gives its own call into
02):** expected shape `ai_system.override_behavior(entity_id, behavior:
str, params: dict, world) -> None`, `ai_system.get_ai_state(entity_id,
world) -> dict`, `ai_system.set_enabled(entity_id, enabled: bool, world) ->
None`. Used for scripted set-pieces (e.g. a quest script wants an NPC to
stop wandering and face the player during a cutscene). Until `02` merges,
these are documented no-ops per CONTRACTS.md §8, logged once.

#### Inventory / rewards — `engine/lua/api/inventory.py`

```
engine.grant_item(entity_id, item_id, quantity) -> item_instance_id
engine.remove_item(entity_id, item_id_or_instance, quantity) -> bool
engine.has_item(entity_id, item_id, quantity) -> bool
engine.grant_xp(entity_id, amount)
```

**(soft, 04 for the first three, 05 for `grant_xp`)**. Expected shapes:
`inventory.grant_item(entity_id, item_id, quantity, world, event_bus) ->
str`, `inventory.remove_item(entity_id, item_id_or_instance, quantity,
world, event_bus) -> bool`, `inventory.has_item(entity_id, item_id,
quantity, world) -> bool` — all documented `04` call boundaries, verify
exact names once `04` merges. `grant_xp` wraps `05`'s ProgressionSystem
expected entry point `progression.grant_xp(entity_id, amount, world,
event_bus) -> None` — this is the mechanism quest rewards use (`11` and
`14`-generated scripts), distinct from the automatic on-kill XP award
(`death` event → `05`'s own subscription), since a quest handing out XP
isn't a kill. Gold is modeled as an ordinary item (`grant_item(entity_id,
"gold", amount)`), not a stat — keeps it inside the one inventory pipeline
rather than inventing a second currency mechanism.

#### World read/write — `engine/lua/api/world.py`

```
engine.get_tile(x, y) -> {terrain, passable, room_id, sprite_ref, ...} | nil
engine.query_entities_in_radius(x, y, radius) -> {entity_id, ...}
engine.spawn_vfx(vfx_id, x, y, data)
engine.play_sound(sound_id, x, y)
```

`query_entities_in_radius` wraps `00`'s `SpatialHash.query_radius` directly
— hard dependency, always available. `get_tile` **(soft, 06)** wraps the
documented tilemap accessor `worldgen.get_tile(x, y, world) -> dict | None`
(exact name TBD once `06` merges — no-op returning `nil` until then).
`spawn_vfx`/`play_sound` need no Python system at all — they just
`event_bus.emit("vfx_play", {...})` / `event_bus.emit("play_sound", {...})`
per the existing CONTRACTS.md §3.2 rows; no new events, no soft dependency.

#### Floor- and campaign-scoped persistent state — `engine/lua/api/state.py`

```
engine.get_floor_state(key, default) -> value
engine.set_floor_state(key, value)
engine.get_campaign_state(key, default) -> value
engine.set_campaign_state(key, value)
```

This is the mechanism that lets `11`'s dialog node / shop stock / gold
persist per floor or per campaign with **zero special-case Python
handling**. See §2.4 for exactly how it hooks into the save system — this
is a load-bearing design decision for `11`, read it carefully.

#### Narrative logging — `engine/lua/api/narrative.py`

```
engine.log_message(text, category)
```

Thin wrapper: `event_bus.emit("message", {"text": text, "category":
category})` — the same `message` event any Python system uses per
CONTRACTS.md §3.2. No second logging path.

#### Panel control — `engine/lua/api/panel.py`

```
engine.create_panel(panel_id, tree, data)
engine.update_panel(panel_id, data)
engine.destroy_panel(panel_id)
```

`tree` is **the exact same widget-tree JSON format** `08-ui-runtime.md`
defines for `ui_skin.json` and for Python's own `create_panel` calls — spec
§6 is explicit that there is no second, lesser UI format for scripted
content, and this doc does not redefine that schema; it only passes it
through.

Implementation **(soft, 08)**, chosen to stay event-driven rather than add
a new direct Lua→08 call boundary:
- `create_panel(panel_id, tree, data)` emits the existing CONTRACTS.md
  `show_panel` event with payload `{panel_id, tree, data}`. `08`'s
  `UIRuntime` is expected to accept an optional `tree` field on
  `show_panel`'s payload: present → build/replace the panel from this tree;
  absent → look the panel up by id in `ui_skin.json` (the existing
  Python-owned-screen path).
- `update_panel(panel_id, data)` emits `show_panel` with `{panel_id, tree:
  None, data}` — `tree: None` signals "keep the existing tree for this
  panel, just rebind its data" (a partial update, not a rebuild).
- `destroy_panel(panel_id)` emits a **new** event, `panel_closed`,
  `{panel_id}`. This event does not yet exist in CONTRACTS.md §3.2 — **flag
  it for addition in your PR** per CONTRACTS.md's own rule ("if you're
  adding an event not in the table, add it in your PR"); this doc cannot
  edit CONTRACTS.md itself but documents the exact addition needed:
  `panel_closed | Lua (engine.destroy_panel) | panel_id | UIRuntime`.

#### Dialog/shop screen openers — `engine/lua/api/dialog_shop.py`

```
engine.open_dialog_panel(data)
engine.open_shop_panel(data)
```

Thin convenience wrappers only — **all real dialog/shop logic lives in
`11`'s Lua scripts**, not here. Each looks up the pre-authored "dialog" or
"shop" screen entry from `registry.get("configs", "ui_skin")["screens"]`
(owned by `08`) and calls `create_panel("dialog", <that tree>, data)` /
`create_panel("shop", <that tree>, data)` — so `dialog_walker.lua` and
`shop.lua` never need to hand-build a widget tree from scratch, only supply
the data to bind into it. If `ui_skin.json` has no `"dialog"`/`"shop"`
screen entry yet (soft dependency on `08`'s content), log once and no-op
per "absence = zero cost" — `11`'s own PR is expected to add those screen
entries to `ui_skin.json` if `08` hasn't authored generic ones, since `11`
is the first real consumer.

#### Event subscription — `engine/lua/api/event.py`

```
engine.subscribe(event_type, lua_callback, opts) -> token
engine.unsubscribe(token)
engine.emit(event_type, payload)
engine.clear_floor_subscriptions()
```

- `subscribe` wraps `EventBus.subscribe` exactly — Lua scripts subscribe to
  **the exact same event bus** Python systems do, no second-class event
  source. `opts` is a Lua table; `opts.tag_floor_scoped = true` records the
  returned token in a per-floor set that `clear_floor_subscriptions`
  drains. Every call from Lua is wrapped so the actual handler registered
  with `EventBus` is `lambda payload: lua_host.run_protected(lua_callback,
  payload)` — this is what guarantees a throwing Lua handler can't take
  down event dispatch for every other subscriber.
- `emit` wraps `EventBus.emit` exactly — same bus, same ordering, same
  rules as CONTRACTS.md §3. Lua content can emit **any** event, including
  ones from `11`'s vocabulary (`dialog_opened`, `shop_opened`, etc.) or a
  brand-new one a quest script needs (subject to the same "add it to
  CONTRACTS.md in your PR" rule as Python code).
- `clear_floor_subscriptions` is normally called automatically by
  `LuaHost`'s own `floor_changed` handler (§2.1), but is also exposed to
  Lua directly for a script that wants to clear early.

### 2.3 Script directory loading mechanism

```python
def load_scripts_dir(self, scripts_dir: Path = Path("scripts")) -> dict[str, bool]:
    """Discovers scripts_dir/*.lua (and scripts_dir/examples/*.lua),
    sorted alphabetically for determinism, and executes each file's
    top-level Lua code exactly once, in the already-sandboxed runtime with
    `engine` already assigned. Returns {filename: loaded_ok}.

    Loading convention: a script REGISTERS ITSELF at load time by calling
    engine.subscribe(...) at its top level — mirroring how Python gameplay
    systems are 'constructed once, subscribing in __init__' (spec §3).
    There is no separate 'system class' per script and no per-script
    update() hook; everything happens through event subscriptions the
    script sets up when loaded.

    A script that fails to parse or throws during its top-level execution
    is caught by run_protected, logged once, and skipped — every OTHER
    script still loads. A missing scripts/ directory, or an empty one, is
    not an error (absence = zero cost) — the engine boots with no Lua
    content loaded at all, same as an empty data/ tree.
    """
```

**Design rule for every future base script (documented here, binding for
`11` and for anything `14` generates):** the base script library served
three roles simultaneously in v1 — a playable feature, a canonical living
reference for the `engine.*` API, and the snippet source `14`'s editor
generates against — and that was not an accident worth losing. Any script
added to `scripts/` should be written clearly enough to double as API
documentation (readable, commented, using descriptive local variable
names) and, where it demonstrates a reusable pattern, should have a
matching entry added to `data/scripts/snippets.json` (§5.1) rather than
being bolted on as a one-off. This doc only proves the loading mechanism
with two minimal examples; `11` and `14` are expected to keep growing the
library under this same rule.

### 2.4 `set_context` and persistent state — the mechanism `11` depends on

`LuaHost` subscribes to `floor_changed` itself, once, at `boot()` time (an
internal subscription, not exposed as a script-facing hook). On every
`floor_changed`:

1. `clear_floor_subscriptions()` runs first, so no floor-scoped handler
   from the departing floor can fire again.
2. `set_context` finds-or-creates the singleton entity carrying
   `LuaFloorStateComponent` in the **current, already-restored** live
   `World` (by the time any `floor_changed` subscriber runs, `06`'s
   `FloorManager` has already called `restore_floor_snapshot` for the
   destination floor — emit-after-mutate is the existing convention every
   event in CONTRACTS.md §3.2 follows). If this is the floor's first visit
   ever, no such entity exists yet: create one (`world.create_entity()` +
   `world.add_component(id, LuaFloorStateComponent(data={}))`) — from this
   point on it is an ordinary entity in this floor's entity set, so the
   **next** `serialize_floor_snapshot` call captures it automatically,
   with no code written specifically for floor-scoped Lua state.
3. Campaign-scoped state uses the same pattern but attached to the player
   entity (per `00`'s foundation player-id convention — the exact accessor
   is `00`'s to name; treat this as a soft dependency the same way `01`
   treats "current floor depth," defaulting to a documented placeholder
   until confirmed), since the player entity persists across floors
   **outside** any snapshot per CONTRACTS.md §2.6/`00`'s save system — so
   `LuaCampaignStateComponent` round-trips through the top-level save
   exactly the same way, again with zero NPC/dialog/shop-specific code.

```python
@component
@dataclass
class LuaFloorStateComponent:
    data: dict[str, Any]   # arbitrary JSON-safe key/value store, this floor only

@component
@dataclass
class LuaCampaignStateComponent:
    data: dict[str, Any]   # arbitrary JSON-safe key/value store, whole campaign/run
```

`engine.get_floor_state`/`set_floor_state` read/write
`ctx.floor_state_entity`'s `LuaFloorStateComponent.data` dict directly;
`get_campaign_state`/`set_campaign_state` do the same against
`ctx.campaign_state_entity`'s `LuaCampaignStateComponent.data`. Both
components are registered in `_COMPONENT_REGISTRY` like any other — that
registration is the **entire** mechanism that makes save/load "just work"
for this data. There is no bespoke NPC/dialog/shop save code anywhere, in
this component or in `11`'s.

## 3. Consumes

Nothing gameplay-specific at the Python level — this is a host, not a
system. It wraps other components' public entry points as documented per
group in §2.2, all treated as **soft** dependencies per CONTRACTS.md §8:
build against the documented shape now, swap in the real import when each
merges, and re-run §8's integration test to confirm nothing broke. Hard
(Wave 0) dependencies: `World`, `EventBus`, `DataRegistry`, `SpatialHash`,
`_COMPONENT_REGISTRY` — all from `00-foundation-core.md`, already merged
before Wave 1 starts.

## 4. File/Directory Ownership

You own, exclusively:
- `engine/lua/` (all files): `lua_host.py`, `api/entity.py`,
  `api/lifecycle.py`, `api/combat.py`, `api/ai.py`, `api/inventory.py`,
  `api/world.py`, `api/state.py`, `api/narrative.py`, `api/panel.py`,
  `api/dialog_shop.py`, `api/event.py`.
- The `scripts/` directory's **loading mechanism** (not its content) —
  `scripts/examples/heal_shrine.lua`, `scripts/examples/quest_flag_demo.lua`
  (or similarly named minimal examples; exact filenames are your call, keep
  them under `scripts/examples/` so `11`'s real content at `scripts/*.lua`
  never collides on a filename).
- `data/scripts/snippets.json` **schema** + 1-2 example entries (§5.1).
- `tests/unit/test_lua_host.py`, `test_lua_sandbox.py`,
  `test_lua_api_entity.py` (and one test file per other API group),
  `test_snippets_schema.py`.
- `tests/fixtures/lua/` — malicious/broken script fixtures for sandbox and
  error-containment tests.
- `engine/core/schemas/snippets.schema.json` (per CONTRACTS.md §9's rule
  that every new JSON schema ships a validator).

You add entries to (shared, additive-only):
- `engine/core/save.py:_COMPONENT_REGISTRY` — add `LuaFloorStateComponent`,
  `LuaCampaignStateComponent`, alphabetically.
- `docs/CONTRACTS.md` §3.2 — flag `panel_closed` for addition (you cannot
  edit CONTRACTS.md directly per this doc-writing task's constraints, but
  the implementing PR should).

## 5. Data Schemas

### 5.1 `data/scripts/snippets.json`

```json
{
  "schema_version": 1,
  "categories": ["trigger", "condition", "action", "reward", "narrative", "ui", "world"],
  "snippets": [
    {
      "id": "trigger_on_enter_room",
      "category": "trigger",
      "label": "On Enter Room",
      "description": "Fires when the player enters a tagged room.",
      "params": [
        {"name": "room_tag", "type": "string", "default": ""}
      ],
      "lua_template": "-- @node id=\"{id}\" type=\"trigger:trigger_on_enter_room\" x={x} y={y}\n-- @params room_tag=\"{room_tag}\"\nengine.subscribe(\"player_moved\", function(payload)\n  local tile = engine.get_tile(payload.to.x, payload.to.y)\n  if tile and tile.room_tag == \"{room_tag}\" then\n    -- node body continues below\n  end\nend)\n-- @endnode"
    },
    {
      "id": "action_grant_item",
      "category": "action",
      "label": "Grant Item",
      "description": "Grants an item to the triggering entity.",
      "params": [
        {"name": "item_id", "type": "string", "default": ""},
        {"name": "quantity", "type": "number", "default": 1}
      ],
      "lua_template": "-- @node id=\"{id}\" type=\"action:action_grant_item\" x={x} y={y}\n-- @params item_id=\"{item_id}\" quantity={quantity}\nengine.grant_item(actor_id, \"{item_id}\", {quantity})\n-- @endnode"
    }
  ]
}
```

**Field vocabulary (binding for `14`):**

| Field | Meaning |
|---|---|
| `schema_version` | integer, bump on breaking schema changes |
| `categories` | the fixed node-category vocabulary `14`'s palette groups by: `trigger, condition, action, reward, narrative, ui, world` — do not add an eighth without updating this doc |
| `snippets[].id` | unique snippet id, referenced by generated nodes' `type` field as `"<category>:<id>"` |
| `snippets[].category` | one of `categories` |
| `snippets[].label` / `description` | palette display text |
| `snippets[].params` | ordered list of `{name, type, default}` — `type` is one of `string, number, boolean` — the editor's node inspector panel is built from this list |
| `snippets[].lua_template` | a Python-`.format()`-style template string (placeholders `{id}`, `{x}`, `{y}`, and one `{param_name}` per entry in `params`) that produces one complete `-- @node ... -- @endnode` block when instantiated |

**The `-- @node` / `-- @endnode` marker convention (binding for `14`,
decided here since it lives inside `lua_template` strings this component
owns):**

```lua
-- @node id="<node_id>" type="<category>:<snippet_id>" x=<canvas_x> y=<canvas_y>
-- @params <key>=<value> <key2>="<value2>" ...
<generated lua body>
-- @endnode
```

- `id` is a string unique within one generated script file (assigned by
  the editor, not by the snippet definition).
- `type` is `"<category>:<snippet_id>"`, letting a parser look the node
  back up in `snippets.json` without re-parsing the body.
- `x`/`y` are bare numbers — the node's canvas position, so visual layout
  round-trips too, not just logic.
- The `@params` line is a **structured, literal record** of the exact
  parameter values used to instantiate this node's template. This is the
  key mechanism that makes round-tripping tractable: `14`'s parser reads
  parameter values off this one comment line and never needs to reverse-
  engineer them out of arbitrary Lua syntax in the body.
- Any Lua code **outside** an `@node`/`@endnode` pair, or inside one whose
  `type` doesn't resolve to a known `snippets.json` entry, is not this
  component's concern to classify — that is `14`'s "Custom Block"
  fallback (see `14-editor-visual-quest-dialog.md`). This doc only
  guarantees the marker shape is unambiguous to scan for with a line-based
  parser (each marker is a single `--`-prefixed comment line; no marker
  ever spans multiple lines).

A `jsonschema` validator for this file lives at
`engine/core/schemas/snippets.schema.json` per CONTRACTS.md §9, with a
round-trip test loading the example file above.

### 5.2 `LuaFloorStateComponent` / `LuaCampaignStateComponent`

See §2.4 — in-memory/save-system schema only, no standalone JSON file
(the `data` dict's contents are entirely script-authored and opaque to
Python, same spirit as `11`'s `InteractableComponent`).

## 6. Lessons from v1 applied here

- **"Documented but violable" doesn't survive contact with content
  authors.** v1's `set_stat`/`apply_effect` convention was written down and
  broken anyway. v2 makes the dangerous half (`set_stat` on combat-derived
  names) structurally rejected at the API boundary — §2.2's blocklist and
  §7's test are the enforcement, not a comment in a script template.
- **Sandboxing is easy to get subtly wrong by ordering.** `lupa` exposes
  the full stdlib by default; nulling it *after* exposing `engine` (or
  worse, after a script has already run) is a real mistake shape, not a
  hypothetical one. §2.1's `boot()` sequence is written as an ordered list
  specifically so "null the stdlib, then and only then wire up `engine`,
  then and only then load scripts" is unambiguous.
- **The three-birds-one-stone base-script framing (§2.3) was a real,
  named win in v1** worth stating as an explicit rule here rather than
  letting it happen accidentally again, since `11` and `14` both build on
  top of what this component starts.
- **A broken script previously had no formal guarantee against crashing
  the host** — v1's spec calls this out as a design intent ("all Lua
  errors are caught and logged") but this doc makes it one function
  (`run_protected`) used at every single call site, so there is exactly
  one place to verify the guarantee rather than N call sites that could
  each individually forget to wrap a call.

## 7. Definition of Done

- [ ] `io`, `os`, `package`, `require`, `load`, `loadfile`, `dofile`,
      `debug`, `rawget`, `rawset` are `nil` in the sandboxed Lua
      environment — tested by asserting each name evaluates to `nil` from
      Lua, not merely "unused."
- [ ] `math`, `string`, `table`, `ipairs`, `pairs`, `type`, `tostring`,
      `tonumber`, `pcall`, `error`, `assert`, `select`, `unpack` are present
      and functional — tested with a script exercising each.
- [ ] A malicious script fixture attempting to touch any nulled global
      (e.g. `os.execute(...)`, `io.open(...)`, `require("socket")`) fails
      cleanly — caught, logged, the host process is alive afterward, no
      filesystem/process side effect occurred. One test per nulled global
      at minimum.
- [ ] A script fixture that throws mid-execution inside an event handler
      (`error("boom")` or a nil-index) is caught by `run_protected`, logged,
      does not crash the Python process, and does not prevent **other**
      subscribers (Lua or Python) from receiving the same or a subsequent
      event — tested by subscribing a second, working handler and asserting
      it still fires.
- [ ] `set_stat` rejects every name in `COMBAT_DERIVED_STAT_NAMES` with a
      clean, catchable error — one test per blocked name (or one
      parameterized test) — and succeeds for an arbitrary non-blocked
      counter name, readable back via `get_stat`.
- [ ] `deal_damage`/`apply_effect` route through `01`'s
      `EffectResolver.apply_effect_list` (real call if `01` is merged,
      fixture-substitutable function boundary per CONTRACTS.md §8
      otherwise), verified by a test asserting the resulting `damage_dealt`
      event fires with the right payload.
- [ ] `LuaFloorStateComponent`/`LuaCampaignStateComponent` round-trip
      through `00`'s `serialize_floor_snapshot`/`restore_floor_snapshot`
      and `serialize_world`/`deserialize_world` respectively, with a test
      that writes state via `set_floor_state`, saves, reloads, and asserts
      the value survives — with no NPC/dialog/shop-specific code involved
      (this proves the mechanism `11` will build on, generically).
- [ ] `engine.subscribe`'s `tag_floor_scoped=true` tokens are removed by
      `clear_floor_subscriptions`, and `LuaHost`'s own `floor_changed`
      handler calls it automatically — tested by subscribing floor-scoped
      and non-floor-scoped handlers, emitting `floor_changed`, and
      asserting only the floor-scoped one stopped firing.
- [ ] `set_context` rebinds floor/campaign state entities correctly across
      two different floors (writing under floor A, switching to floor B,
      writing a different value under the same key, switching back to A,
      and reading A's original value back).
- [ ] `load_scripts_dir` loads every well-formed `.lua` file under
      `scripts/` (alphabetical, deterministic order) and skips a
      deliberately broken fixture file without preventing the others from
      loading.
- [ ] `data/scripts/snippets.json`'s schema validates via the
      `engine/core/schemas/snippets.schema.json` validator, with a
      round-trip test over the example file in §5.1.
- [ ] Two example scripts under `scripts/examples/` load and, when their
      subscribed event fires, produce an observable, asserted effect
      (§8's integration test).
- [ ] Every event this component's wrappers emit
      (`entity_spawned`, `vfx_play`, `play_sound`, `message`, `show_panel`,
      the new `panel_closed`, plus whatever a test script emits via
      `engine.emit`) is accounted for per CONTRACTS.md §7 — `panel_closed`
      flagged for addition to CONTRACTS.md §3.2 in the implementing PR.
- [ ] `pytest` green.

## 8. Test Plan

Unit tests (`tests/unit/test_lua_host.py` + one per API group):
- Sandbox nulling and kept-globals presence (§7).
- Error containment for a broken script (§7).
- `set_stat` blocklist enforcement, all names + one accepted counter.
- Each `engine.*` function calls its documented Python entry point (mocked
  at the function boundary per CONTRACTS.md §8 for not-yet-merged
  components) with the right arguments and forwards the return value/
  raises correctly.
- `snippets.json` schema validation round-trip.

Integration test (`tests/integration/test_lua_pipeline.py` — required per
CONTRACTS.md §7.3, drives the real entry point end-to-end, not mocks of
it):
- Boot a real `LuaHost` against a real `World` + `EventBus` +
  `DataRegistry` loaded from a fixture `data/` tree that includes a
  fixture monster entity with a real `StatsComponent` (low HP) and `01`'s
  real `EffectResolver` (or, if `01` isn't merged in this test's own CI
  run, the documented fixture substitute per CONTRACTS.md §8 — note which
  in the test file).
- Call `load_scripts_dir("scripts/examples")`.
- Emit a fixture event one of the example scripts subscribes to (e.g.
  `entity_interacted` with a target matching the fixture monster).
- Assert the full chain fired for real: the Lua handler ran, called
  `engine.deal_damage`, `01`'s `EffectResolver` reduced the fixture
  monster's HP, and `damage_dealt` was emitted with the correct payload —
  this is the "wired, not just built" proof for this component, mirroring
  `01`'s own combat-pipeline integration test shape.
- A second case: floor-state persistence — set a floor-scoped value from a
  script's handler, trigger a save (`serialize_floor_snapshot`), tear down
  and rebuild the `World`, `restore_floor_snapshot`, and assert
  `engine.get_floor_state` (called from a second test script or directly
  through the API) returns the persisted value.

## 9. Open Questions & Defaults

- **`set_stat`'s storage location.** Default (documented in §2.2): direct
  write to `StatsComponent.base[stat_name]`, guarded by the blocklist, as
  a narrow documented exception to CONTRACTS.md rule 9. **Fallback if `01`
  objects to sharing `StatsComponent.base` for this:** introduce a new,
  `10`-owned `ScriptFlagsComponent(data: dict[str, Any])` instead, and
  route `set_stat`/`get_stat` for non-derived names through it while
  `get_stat` for real derived stats still goes through `01`'s
  `StatsSystem.get_stat`. Note whichever is chosen in your PR; either is
  compatible with everything `11` needs, since `11` only calls
  `set_stat`/`get_stat` by name and never inspects storage location.
- **Panel event shape.** Default: reuse `show_panel` for create/update
  (`tree: None` = update-in-place), add one new event `panel_closed` for
  destroy. If `08`'s actual `UIRuntime` implementation prefers three
  distinct events instead, that's a small, compatible change to this
  component's `panel.py` only — `11`'s and `14`'s Lua-facing
  `engine.create_panel`/`update_panel`/`destroy_panel` signatures do not
  change either way.
- **AI/inventory/progression/worldgen exact entry-point names** (§2.2's
  "soft" groups) are this component's best-documented guess at each
  sibling doc's eventual public signature. When `02`/`04`/`05`/`06`
  actually merge, update the corresponding `engine/lua/api/*.py` module's
  import and re-run §8's integration test — do not block on getting the
  guess exactly right up front, per CONTRACTS.md §8's whole point.
- **Player-entity accessor for campaign-scoped state.** Assumed to exist
  per `00`'s "foundation player-id convention" (also referenced by `01`).
  If `00` ships something other than a simple accessor/marker component,
  update §2.4's `set_context` implementation accordingly — the Lua-facing
  `engine.get_campaign_state`/`set_campaign_state` API is unaffected.
