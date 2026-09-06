# SLATE v2 — Shared Contracts

**Read this before writing any code.** Every component spec in `docs/components/`
references this document instead of repeating it. If your component doc and
this doc ever disagree, this doc wins — flag the conflict in your PR
description rather than silently picking one.

This is the thing that lets ~13 components get built by independent,
non-communicating Claude Code sessions and still fit together: nobody needs
to read anybody else's code, only this contract.

---

## 1. Repository Layout

```
slatev2/
  engine/
    core/            # ecs.py, events.py, registry.py, spatial_hash.py, save.py, formula.py
    systems/         # one module per gameplay system (stats.py, effects.py, combat.py,
                      #   ai.py, spells.py, status.py, inventory.py, affixes.py, sets.py,
                      #   loot.py, progression.py, vision.py, vaults.py, campaign.py,
                      #   worldgen.py)
    ui/              # ui_runtime.py, anim_system.py
    render/          # renderer.py, autotile.py
    audio/           # audio_system.py
    input/           # input_handler.py
    lua/             # lua_host.py, api/ (one module per engine.* API group)
    modding/         # archive.py, load_order.py
    main.py          # process entry point; wires systems, runs the game loop
  data/
    entities/        # monsters; entities/npcs/ is the `npcs` namespace, excluded from `entities`
    items/           # weapons/armor/etc.; items/legendary/ and items/sets/ are separate namespaces
    spells/
    effects/
    affixes/
    campaigns/
    maps/            # maps/vaults/ is the separate `vaults` namespace, excluded from `maps`
    config/          # controls.json, ui_skin.json, ui_config.json, audio_config.json, difficulty.json, ...
    dialogs/
    shops/
    scripts/
      snippets.json  # node palette source for the Visual Quest Editor
  scripts/           # base Lua library — npc_interaction.lua, dialog_walker.lua, shop.lua, etc.
  editor/
    editor.py
    project.py
    modes/           # map_editor.py, sprite_editor.py, data_editor.py, skin_editor.py,
                      #   sprite_manager.py, audio_tab.py, quest_editor.py, dialog_editor.py
  saves/
  mods/
    load_order.txt
  tests/
    unit/
    integration/     # full-pipeline / wire-up tests — see §7 and component doc §Test Plan
  docs/
    ORCHESTRATION.md
    CONTRACTS.md
    components/
  CLAUDE.md
  pyproject.toml
```

A component's spec names the exact files/modules it owns. **Never edit a file
another component's spec names as owned by it.** If you need something from
another module that doesn't exist yet, depend on the interface documented
here, not on the file — see §8 (stubbing).

---

## 2. Core Architectural Rules (non-negotiable, apply everywhere)

1. **Entities are bare integer IDs.** Components are `@dataclass`, data only,
   plus `to_dict()`/`from_dict()`. No methods beyond serialization. Systems
   read/write via `world.query(ComponentA, ComponentB, ...)`.
2. **New feature → new component.** Never modify an existing component's
   shape to bolt on unrelated behavior; add a new component instead.
3. **Every component class must be registered in `_COMPONENT_REGISTRY`**
   (`engine/core/save.py`). A component missing from that dict is silently
   dropped on load. This is lint-checked (see `tests/unit/test_component_registry.py`
   in the foundation component) — CI fails if a dataclass under `engine/core`
   or `engine/systems` decorated with the project's `@component` marker isn't
   in the registry. **Every component doc that introduces a new component
   dataclass must add it to the registry in the same PR.**
4. **Systems never call each other directly.** All cross-system
   communication is through the event bus (§3) or by reading/writing shared
   components via `world.query`. If you find yourself importing another
   system's module to call a method on it, stop — either it should be an
   event, or the logic belongs in `EffectResolver`.
5. **Distance is Chebyshev everywhere**, no exceptions: `max(abs(dx),
   abs(dy))`. Vision, noise, AoE, pathfinding heuristic, spatial queries, all
   of it. There is exactly one distance function
   (`engine/core/spatial_hash.py:chebyshev_distance`) and every system
   imports it rather than inlining the formula.
6. **Movement is 8-way, diagonal and cardinal cost the same.** Passability is
   destination-tile-only — no corner-cutting check. Do not "fix" this.
7. **Absence = zero cost.** A missing sprite, missing sound, missing Lua
   script reference, or sleeping monster must never raise, block, or cost
   meaningful CPU. Falls back silently (sprite → colored rect, sound → no-op,
   script ref → no-op), logs once, moves on. Any component whose failure
   mode is "throws if content is missing" fails review.
8. **Data over code.** Numeric/string tunables belong in JSON, not Python
   constants. Formula fields (`"3d6"`, `"0.4 - (INT * 0.03)"`) are evaluated
   through `engine/core/formula.py` (wraps `simpleeval`), never hand-parsed
   per system.
9. **Base stat fields are never mutated directly.** All temporary/permanent
   modification goes through `StatsComponent.modifiers` (see
   `01-stats-combat.md`). If your system wants to change a derived stat,
   it adds a modifier; it does not write `stats.strength += x`.
10. **The Data Registry is the only disk I/O path for content**, loaded once
    at startup, zero disk I/O during gameplay. Nothing outside
    `engine/core/registry.py` and `engine/modding/` reads JSON off disk
    directly.

---

## 3. Event Bus Contract

`engine/core/events.py` (owned by the foundation component — see
`00-foundation-core.md`). API:

```python
subscribe(event_type: str, handler: Callable[[dict | None], None]) -> int  # token
emit(event_type: str, payload: dict | None = None) -> None
unsubscribe(token: int) -> None
```

- Synchronous, in-process pub/sub. Handlers run in subscription order.
- A snapshot of the handler list is taken **before** dispatch begins, so a
  handler that subscribes a new handler mid-dispatch does not have that new
  handler receive the event currently being dispatched.
- Payloads are `dict` or `None` by convention — never a bespoke object.

### 3.1 Naming convention (binding for every *new* event)

`noun_verbedpast` — e.g. `item_pickup` → prefer `item_picked_up` going
forward for anything new, `floor_changed`, `status_applied`. Events that
represent an async "waiting on UI" state are the one sanctioned exception
and keep the `_pending` suffix (`level_up_pending`, `spell_choice_pending`).
Do not invent a third pattern. If you're adding an event not in the table
below, name it to match and add it to the table in your PR.

### 3.2 Canonical event table

This is the full event vocabulary, carried forward from the v1 index with
one deliberate fix applied: v1 had both `damage` and `damage_dealt` emitted
for the same thing, and the renderer listened to the wrong one. **v2 has
exactly one: `damage_dealt`.** `EffectResolver` emits it; nothing emits
`damage`. Any component doc below referencing `damage_dealt` means this
event.

| Event | Emitted by | Payload (key fields) | Consumed by (typical) |
|---|---|---|---|
| `entity_spawned` | worldgen / campaign / loot | `entity_id, kind, position` | AI, renderer |
| `entity_moved` | movement/input handling | `entity_id, from, to` | AI (path invalidation), vision, animation |
| `entity_died` | combat | `entity_id, killer_id` | AI, quest/Lua listeners |
| `entity_interacted` | input/movement (bump or interact key) | `actor_id, target_id` | Lua (`npc_interaction.lua`) |
| `player_moved` | input handling | `from, to` | vision, AI wake checks |
| `player_died` | combat | `entity_id` | game-over flow (entity stays intact) |
| `check_wake` | movement/noise | `entity_id, source_pos, radius` | AI |
| `noise_emitted` | movement, combat, spells | `position, radius, source_id` | AI |
| `resolve_hit` | combat (internal step, may be event or direct call — see `01-stats-combat.md`) | `attacker_id, defender_id` | combat pipeline |
| `damage_dealt` | EffectResolver | `target_id, amount, damage_type, source_id, position` | renderer/anim, combat, quest hooks |
| `miss` | combat | `attacker_id, defender_id` | renderer/anim, message log |
| `death` | combat (pre-`entity_died`, XP-relevant) | `entity_id, killer_id, xp_value` | ProgressionSystem |
| `loot_drop` | combat/loot | `position, entries, is_boss` | LootSystem placement, message log |
| `item_pickup` | inventory | `entity_id, item_instance_id` | UI, journal |
| `item_dropped` | inventory | `entity_id, item_instance_id, position` | worldgen/loot |
| `item_used` | inventory | `entity_id, item_instance_id` | EffectResolver |
| `equip_changed` | inventory | `entity_id, slot, item_instance_id \| None` | StatsSystem, SetTrackerSystem |
| `set_bonus_changed` | SetTrackerSystem | `entity_id, set_id, active_tier` | StatsSystem |
| `identify_type_request` | inventory/UI | `item_base_id` | inventory (identify_type) |
| `stat_modifier_applied` | StatsSystem | `entity_id, tag, stat, op, value` | UI, anim |
| `stat_allocated` | progression/UI | `entity_id, stat, amount` | StatsSystem |
| `level_up_pending` | ProgressionSystem | `entity_id` | UI (Lua panel) |
| `spell_choice_pending` | ProgressionSystem | `entity_id, options` | UI (Lua panel) |
| `spell_chosen` | UI → spell system | `entity_id, spell_id` | ProgressionSystem |
| `spell_learned` | ProgressionSystem/effects | `entity_id, spell_id` | UI, journal |
| `bonus_points_remaining` | ProgressionSystem | `entity_id, remaining` | UI |
| `spell_cast_initiated` | SpellSystem | `entity_id, spell_id, targeting_mode` | input (targeting cursor), UI |
| `spell_target_chosen` | input/targeting | `entity_id, spell_id, target` | SpellSystem |
| `target_confirmed` | input/targeting | `target` | SpellSystem |
| `cancel_targeting` | input | `entity_id` | SpellSystem |
| `spell_cast_cancelled` | SpellSystem | `entity_id, spell_id` | UI |
| `status_applied` | StatusEffectsSystem/EffectResolver | `entity_id, effect_id, instance_id, duration` | UI, anim |
| `status_expired` | StatusEffectsSystem | `entity_id, effect_id, instance_id` | UI, anim |
| `trap_revealed` | VisionSystem (see `05-progression-vision.md` for scope) | `position, trap_id` | renderer, message log |
| `trigger_level_complete` | EffectResolver (effect type) or campaign trigger | `entity_id` | CampaignSystem |
| `floor_changed` | FloorManager | `from_depth, to_depth` | AI (reset), vision, save |
| `stair_use` | input/movement | `entity_id, direction` | FloorManager |
| `campaign_selected` | UI | `campaign_id` | CampaignSystem |
| `campaign_complete` | CampaignSystem | `campaign_id` | UI |
| `game_complete` | CampaignSystem | — | UI |
| `new_game_selected` / `load_game_selected` / `save_slot_selected` / `quit_selected` | main menu UI | varies | main.py |
| `show_panel` | any system needing UI | `panel_id, data, tree` (tree is optional — added by 10's `engine.create_panel`/`update_panel`, see 10-lua-scripting-layer.md §2.2) | UIRuntime / Lua |
| `panel_closed` | Lua (`engine.destroy_panel`, 10-lua-scripting-layer.md §2.2) | `panel_id` | UIRuntime |
| `message` | any system | `text, category` | UI message log |
| `play_sound` | any system (or `"sound"` override key on any payload) | `sound_id, position \| None` | audio system |
| `vfx_play` | combat/effects | `vfx_id, position, data` | anim system |
| `ranged_attack_attempt` | combat | `attacker_id, target` | combat (ammo check) |
| `ranged_attack_blocked` | combat | `attacker_id, reason` | UI/message |
| `dialog_opened` / `dialog_choice_selected` / `dialog_closed` | Lua (`dialog_walker.lua`) | `entity_id, dialog_id, node_id` | UI (Lua panel) |
| `dialog_choice_clicked` | UI (dialog panel's choice Button, `on_click`) | `entity_id, target_id, dialog_id, node_id, choice_index` | Lua (`dialog_walker.lua`) |
| `shop_opened` / `buy_item` / `sell_item` / `trade_completed` / `shop_refreshed` | Lua (`shop.lua`) | `entity_id, shop_id, item, price` | UI (Lua panel) |
| `shop_leave_clicked` | UI (shop panel's "Leave" Button, `on_click`) | `entity_id, shop_id` | Lua (`shop.lua`) |
| `portal_target_changed` | Lua | `entity_id, target_floor` | CampaignSystem |

If a component doc lists an event not in this table, treat that as a spec
bug — add it here in your PR rather than emitting an undocumented event.

---

## 4. Data Registry Contract

Owned by the foundation component (`engine/core/registry.py`). Singleton,
loaded once at startup, no disk I/O afterward.

**Namespaces → source directory:**

| Namespace | Source directory |
|---|---|
| `entities` | `data/entities/` (excludes `entities/npcs/`) |
| `items` | `data/items/` (excludes `items/legendary/`, `items/sets/`) |
| `spells` | `data/spells/` |
| `effects` | `data/effects/` |
| `affixes` | `data/affixes/` |
| `campaigns` | `data/campaigns/` |
| `vaults` | `data/maps/vaults/` |
| `legendaries` | `data/items/legendary/` |
| `sets` | `data/items/sets/` |
| `maps` | `data/maps/` (excludes `maps/vaults/`) |
| `configs` | `data/config/` (keyed by filename, not `id`) |
| `npcs` | `data/entities/npcs/` |
| `dialogs` | `data/dialogs/` |
| `shops` | `data/shops/` |

Every namespace except `configs` is keyed by the JSON `"id"` field.

**Load priority (lowest → highest):** base archives → mod archives (order
from `mods/load_order.txt`, top = lowest priority) → loose files. Loose
files always win at any priority. Same `id` at the **same** priority is a
load-time error. Same `id` at **different** priority is a silent override —
no warning, by design. New IDs are always additive regardless of source.

Public API (exact signature is the foundation component's call, but every
consumer should code against this shape):

```python
registry.get(namespace: str, id: str) -> dict | None
registry.all(namespace: str) -> dict[str, dict]
registry.reload() -> None   # dev/editor use only, never called mid-gameplay
```

---

## 5. Component Registry (save system)

Every `@dataclass` component that gameplay ever attaches to an entity must
be registered by name in `engine/core/save.py:_COMPONENT_REGISTRY` so
save/load round-trips it. This file is **shared, additive-only, edited by
every component that introduces a new component type** — expect merge
churn here; keep entries alphabetical by component name to minimize
conflicts, and never remove another component's entry.

---

## 6. Formula Evaluation

`engine/core/formula.py` wraps `simpleeval`. Two entry points every system
must use instead of hand-rolling arithmetic or dice parsing:

```python
roll_dice(expr: str, rng: random.Random | None = None) -> int   # "3d6", "1d8+2"
eval_formula(expr: str, context: dict) -> float                  # "0.4 - (INT * 0.03)"
```

`context` keys are conventionally the entity's derived stat names in
UPPERCASE (`INT`, `STR`, `DEX`, ...). Any system introducing a new formula
field in its JSON schema must document the context keys it supplies in its
own component doc.

**`StatsComponent.base` key convention (resolved by
`15-integration-verification.md`, §2.1 audit):** this doc originally left
open whether the ability-score keys backing `INT`/`STR`/`DEX`/... are
themselves abbreviated (`"dex"`) or spelled out (`"dexterity"`). By the
time 15 ran, `03-spells-status.md`'s `SpellSystem` and
`05-progression-vision.md`'s `ProgressionSystem` had both already
independently settled on the spelled-out form, matching the canonical
monster/item content schema's own illustrative examples (spec §4/§5) —
only `01-stats-combat.md`'s `CombatSystem.resolve_hit` and
`EffectResolver`'s generic formula-context builder had defaulted to a bare
lowercase instead. **Spelled out wins**: `INT` → `"intelligence"`, `STR` →
`"strength"`, `DEX` → `"dexterity"`, `CON` → `"constitution"`, `WIS` →
`"wisdom"`, `CHA` → `"charisma"`, `LUK` → `"luck"`; any other identifier
falls back to a plain lowercase (`MAX_HP` → `"max_hp"`). `01`'s two
holdouts were fixed to match as part of 15's integration pass
(`engine/systems/combat.py`'s `resolve_hit`, `engine/systems/effects.py`'s
`_build_formula_context`) — content and future components should author
`StatsComponent.base`/monster `"stats"` blocks with the spelled-out names.

---

## 7. "Wired, Not Just Built" — Definition of Done Floor

This is the single biggest failure mode from v1 (full loot pipeline built,
tested in isolation, never called from map generation; `vfx_play` emitted
with nothing subscribed; `damage`/`damage_dealt` mismatch silently killing
hit VFX). **It is now a hard gate, not a lesson:**

A component is not done, and its PR should not merge, until:

1. Every event it emits has at least one real subscriber somewhere in the
   merged codebase (a subscriber added by a *different* component counts —
   coordinate via the event table in §3, not by guessing).
2. Every event it subscribes to is actually emitted somewhere in the merged
   codebase, or the component doc explicitly says "not yet wired, tracked
   in `15-integration-verification.md`."
3. At least one integration test in `tests/integration/` drives the
   component's real entry point (not a mock of it) end-to-end — e.g. loot:
   a test that runs floor generation and asserts items exist on the tile
   map afterward, not just a test that `LootSystem.roll()` returns a table.
4. Its Definition of Done checklist (in its own doc) is fully checked.

`15-integration-verification.md` re-verifies this across the whole system
at the end and is the final merge gate for the "done" milestone — but each
component is responsible for its own slice of this before that point, not
for deferring it there.

---

## 8. Working Against Unmerged Dependencies (stubbing)

Most components only depend on **this document**, not on another
component's code — that's the point of defining events and schemas up
front. When your spec says you consume an event or read a namespace that
another component's code will populate later:

- Write against the documented payload/schema shape here.
- If you need a concrete instance to test against (e.g. a monster JSON to
  test AI wake logic), author a minimal fixture under
  `tests/fixtures/` — don't wait for the real content to land.
- Do not import another component's not-yet-merged module. If your tests
  need e.g. `EffectResolver` and it isn't merged yet, mock the function
  boundary (`engine.core.events.emit`/`subscribe`) instead of importing the
  real implementation.
- When the real dependency does merge, delete your fixture/mock only if the
  integration test (§7.3) still passes against the real thing — don't
  delete it speculatively.

---

## 9. Coding & Testing Conventions

- Python 3.11+, type hints on all public functions, `dataclasses` for
  components/DTOs.
- `pytest` for all tests. Unit tests live next to the system in
  `tests/unit/test_<module>.py`. Cross-system tests live in
  `tests/integration/`.
- No global mutable state outside the singletons explicitly named here
  (`DataRegistry`, the event bus, `SpatialHash`). If you think you need a
  new one, that's a design question for `docs/ORCHESTRATION.md`'s
  escalation path, not a local decision.
- Every new JSON schema introduced by a component must ship a `jsonschema`
  (or equivalent lightweight validator) under `engine/core/schemas/` and a
  round-trip test loading every fixture file under its `data/` directory.
- Logging: use the standard `logging` module, module-level logger. "Log
  once" fallbacks (missing sprite, missing sound) must track what they've
  already logged (a module-level `set()`) so they don't spam.

---

## 10. Canonical Reference Values

- **Damage types (8):** `physical, fire, cold, lightning, poison, holy,
  arcane, necrotic`.
- **Rarity tiers:** `common, uncommon, rare, epic, legendary` (budget
  `common=0` through `legendary=14` in the affix system — see
  `04-inventory-items-loot.md`). Rarity is never stored on an item
  definition; it's rolled at spawn time.
- **AI behaviors (4):** `chaser, ambusher, patroller, coward`. Do not add a
  fifth without a strong, documented reason — see `02-ai-system.md`.
- **Identification tiers (2):** `identify_type` (base-item-wide),
  `identify_instance` (per-item curse reveal).
