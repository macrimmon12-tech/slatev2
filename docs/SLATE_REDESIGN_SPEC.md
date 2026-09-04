# SLATE — Redesign Design Document

**Status:** SLATE v1 is being terminated. This document captures the complete design
as it stood at end-of-life — architecture, systems, content pipeline, and the editor
suite — plus the decisions we reversed or corrected along the way, so the rebuild
starts from the end of the learning curve instead of the beginning of it.

This is not a phase plan. It's a snapshot of where the design actually landed,
written as the spec a fresh implementation should target.

---

## 1. Overview & Intent

SLATE is a classic tile-based roguelike dungeon crawler, in the lineage of Caves of
Qud, traditional SII-style roguelikes, and Castle of the Winds. Python + pygame-ce
for the runtime; Lua (via `lupa`) as a dormant scripting layer that activates only
when content asks it to; a fully data-driven content pipeline (JSON in, nothing
hardcoded); and — as the project matured — a full in-house content editor so
non-programmers could build campaigns, maps, items, and quests without touching
Python.

**The intent, stated plainly:** an engine that never needs a code change to add
game content. Every new monster, spell, item, quest, or NPC should be a JSON file
(or a Lua script for anything that needs branching logic), never a new Python class
or a modified one. The two design precepts that fell out of pursuing that goal
hardest, and that should be treated as non-negotiable from day one of the rebuild:

1. **Data over code.** If it can be a number or string in a JSON file, it is one.
   Formula strings (`"3d6"`, `"0.4 - (INT * 0.03)"`) evaluated via `simpleeval`
   rather than Python branches, so gameplay balance is a content edit, not a
   deploy.
2. **Absence = zero cost.** A sleeping monster costs nothing until woken. A missing
   Lua script reference is a silent no-op. A missing sprite falls back to a colored
   rectangle. A missing audio file is silently skipped forever after the first
   check. Nothing in the engine should ever require content to exist in order to
   run — content is additive, never load-bearing.

The system that proved this hardest, and validated it best, was Phase 8.5 (NPCs,
dialog, shops): by the end, Python had **zero** system classes for any of it —
no `NPCSystem`, `DialogSystem`, or `ShopSystem`. Every bit of interaction logic
lived in Lua, and Python's total involvement was one generic component
(`InteractableComponent`) and two lines checking for its presence. That's the
target shape for every future feature, not just the one that happened to land
there.

The other principle that paid for itself repeatedly: **everything is events.**
Systems never call each other directly. Every UI-facing state change is
event-queued (`level_up_pending`, `spell_choice_pending`, `bonus_points_remaining`)
so the engine never blocks on UI existing, and Lua scripts can subscribe to the
exact same events Python systems do — there is no second-class event source.

---

## 2. Core Architecture

### 2.1 Entity-Component-System

Entities are bare integer IDs. Components are plain `@dataclass` instances — data
only, no methods beyond `to_dict()`/`from_dict()` for serialization. Systems read
and write components via `world.query(ComponentA, ComponentB, ...)` and never touch
each other directly. Adding a feature means adding a new component; existing
components are never modified to bolt on new behavior.

Every component used anywhere in gameplay **must** be registered in
`_COMPONENT_REGISTRY` (save system) — a component missing from that dict is
silently dropped on load with no error. This bit the project more than once and
should be a lint-checked invariant in the rebuild, not a documentation note.

### 2.2 Event Bus

Synchronous pub/sub. `subscribe(event_type, handler) -> token`, `emit(event_type,
payload)`, `unsubscribe(token)`. Handlers run in subscription order; a snapshot of
the handler list is taken before dispatch so subscribing inside a handler doesn't
receive the event that triggered it. Payloads are conventionally `dict` or `None`.

By end-of-life the event vocabulary had grown to ~50 named events spanning combat,
AI, inventory, spells, status, progression, campaigns, floors, dialog, shops, and
UI. See Appendix A for the full index as it stood. **Lesson:** event names drifted
inconsistently in a couple of places (`damage` vs `damage_dealt` — see §8) —
the rebuild should fix a naming convention (`noun_verbedpast` — `item_pickup`,
`floor_changed`, `status_applied`) and enforce it, rather than letting each system
author pick independently.

### 2.3 Data Registry

Singleton, loaded once at startup, zero disk I/O during gameplay. Namespaces:
`entities`, `items`, `spells`, `effects`, `affixes`, `campaigns`, `vaults`,
`legendaries`, `sets`, `maps`, `configs`, `npcs`, `dialogs`, `shops` — each mapped
to a source directory under `data/`, keyed by JSON `id` field.

**Load priority (lowest → highest):** base archives → mod archives (in
`mods/load_order.txt` order) → loose files. Loose files always win, at any
priority — this is the Bethesda/MO2 model deliberately, chosen because it makes
mod development and live content iteration identical: edit a JSON, relaunch, done.
Same ID at the same priority is an error at load time; same ID at different
priority is a silent override, by design, with no warning — additive content
(new IDs) is always included regardless of source.

### 2.4 Spatial Hash

All position queries — vision, noise, AoE, wake triggers, trap detection — route
through `SpatialHash`, never through `world.query`. It is the **authoritative**
position store; `PositionComponent` must be kept in sync manually on every move.
Distance is **Chebyshev** (`max(abs(dx), abs(dy))`) everywhere in the codebase —
vision range, noise radius, AoE, pathfinding heuristic, spatial queries. This one
decision, made early and never revisited, is worth calling out explicitly: having
a single canonical distance function used by every system prevented an entire
category of bugs (diagonal-distance inconsistencies between systems) that a less
disciplined codebase accumulates. Keep it as a hard rule from line one of the
rebuild, not a convention to converge on.

### 2.5 Movement

8-way movement (cardinal + diagonal) for player and monsters alike. Diagonal and
cardinal steps have **equal cost** everywhere — movement, pathfinding, distance
checks — the roguelike convention, not a simulation. Passability check is
**destination-tile-only**; there is deliberately no corner-cutting check against
the cardinal neighbors. This was a considered simplicity choice (see §8) and
should stay a choice, not something the rebuild "improves" by accident.

### 2.6 Input, Renderer, Audio

- **Input:** all bindings in `data/config/controls.json`, loaded by `InputHandler`
  at startup, fully rebindable, never hardcoded. Contexts (`game`, `targeting`,
  `ui`) scope the same key to different actions. Multiple keys per action
  (arrows + WASD + numpad simultaneously bound) supported natively.
- **Renderer:** pygame-ce, resizable window (not fullscreen — a proper desktop
  app), 80×50 tile viewport letterboxed against a 240px HUD panel, tile size
  recalculated on every resize. Missing sprites fall back to a solid color from
  the palette, logged once. Wall tiles use a 4-bit cardinal autotile bitmask
  (16 variants, `wall_0.png`–`wall_15.png`) computed post-generation; the four
  diagonal-corridor inner-corner variants (3, 6, 9, 12) even have hardcoded
  right-triangle polygon fallbacks so diagonal corridors read correctly with zero
  sprite assets. Per-tile `sprite_ref` overrides always win over the autotile
  lookup.
- **Audio:** `pygame.mixer`, event → sound mapping entirely data-driven
  (`audio_config.json`), no hardcoded pairings. Per-entity/per-event sound
  overrides via a `"sound"` key on any event payload — zero config changes needed
  to give one goblin a different death sound than another.

### 2.7 Save System

Full world serialization on every floor transition, human-readable JSON, to named
files in `saves/`. Floor snapshots (`serialize_floor_snapshot` /
`restore_floor_snapshot`) capture non-player entities + tilemap per visited floor;
player state persists across floors outside any snapshot. `spawned_uniques` is
tracked globally to prevent legendary re-drops across a run.

---

## 3. Gameplay Systems

All of the following are event-driven systems living in `engine/systems/`,
constructed once, subscribing in `__init__`, never calling each other directly.

- **StatsSystem** — single `get_stat(entity_id, name, world)` read path for all
  derived stats. Modifier resolution (**"Option C"**): sum all `add` modifiers,
  then chain-multiply all `multiply` modifiers — `result = (base + Σadds) ×
  Πmultiplies`. Modifiers live in `StatsComponent.modifiers`, keyed by an
  instance-id tag, and are removed by tag prefix on expiry. **Base stat fields are
  never mutated directly** — this is the rule that makes buffs/debuffs/equipment/
  status effects all compose correctly without any of them knowing about each
  other. Tag conventions: `item_{eid}_{i}`, `affix_{id}_{i}`,
  `{instance_id}:{stat}`, `set_{id}_{tier}_{i}`.
- **EffectResolver** — the single pipeline every spell, potion, trap, equipment
  proc, and status tick resolves through. ~14 effect types (`damage`,
  `restore_hp/mp`, `stat_modifier`, `noise`, `vfx`, `script`, `identify_item`,
  `remove_curse`, `teleport_self`, `apply_status`, `lifesteal`, `reduce_armor`,
  `trigger_level_complete`, `learn_spell`). Unknown effect types are silently
  skipped — new effect types are additive, no dispatcher to update elsewhere.
  Formula fields support dice notation + arithmetic via `simpleeval`, with `INT`
  substituted before evaluation for spell scaling.
- **CombatSystem** — `resolve_hit` → hit-chance formula (config-driven, depth-aware
  difficulty multipliers) → damage roll → EffectResolver → death handling. Player
  death emits `player_died` and leaves the entity intact (game-over is a
  higher-level concern); monster death emits `loot_drop` and destroys the entity.
  `process_monster_turns()` runs all AI in deterministic entity-ID order.
- **AISystem** — sleep/wake (monsters default asleep — a full floor idles for
  nearly free), four data-parameterized behaviors (**chaser**, **ambusher**,
  **patroller**, **coward**), A* pathfinding (8-directional, Chebyshev heuristic,
  cached per entity, invalidated on player movement), multi-phase bosses via a
  `behavior_phases` condition list (HP-threshold or turn-count triggers swap the
  active behavior — no special-case boss code), and monster spellcasting that
  reuses the exact same SpellSystem path player casts use. Four behaviors ×
  data variation is the whole AI surface — this combinatorial-simplicity bet paid
  off and should be kept, not "expanded" into more named behaviors without strong
  cause.
- **SpellSystem** — IDLE/TARGETING state machine, five targeting modes (`self`,
  `aoe_self`, `single_enemy`, `targeted_tile`, `aoe_targeted`), MP → INT-gate →
  fail-chance-roll (with `backfire_effects` on fizzle) → effect resolution, one
  pending cast at a time.
- **StatusEffectsSystem** — called directly (not event-subscribed) by
  EffectResolver and the main loop's `tick()`. Same `effect_id` applied twice
  creates two independent `instance_id`s that tick and expire independently
  (stacking is unconditional, by instance).
- **InventorySystem** — pickup/drop/use/equip/unequip/identify. Cursed items
  auto-identify and lock on equip. Two-tier identification: `identify_type`
  (all instances of a base item, the classic "identify one potion, know them
  all" roguelike rule) vs `identify_instance` (curse reveal — personal to that
  item, doesn't leak the base type's identity).
- **AffixGenerator** — budget-by-rarity affix selection (`common=0` through
  `legendary=14`), weighted `1/cost` so cheap affixes are common and powerful ones
  rare, `conflict_tags` to prevent incompatible pairs, depth-gated tiers.
  Legendaries bypass the generator with fixed affix sets and are always
  pre-identified; uniqueness enforced via a `spawned_uniques` set carried in save
  state.
- **SetTrackerSystem** — reacts to `equip_changed`, counts equipped pieces per
  set, applies threshold bonuses as **full replacement lists** (not additive
  across tiers).
- **LootSystem** — monster drops (weighted `loot_table` entries, boss/elite
  legendary-first lookup) and ambient floor loot (`place_floor_loot`, scattered
  post-generation). Rarity is never stored on an item definition — it's rolled at
  spawn time from depth-window interpolation between an item's `min_depth` and
  `max_depth` (common-heavy near `min_depth`, rare/epic-heavy near `max_depth`).
- **ProgressionSystem** — XP on `death` (deduplicated per entity, awarded once),
  level-up thresholds with a repeating tail past the last defined entry, automatic
  stat gains + bonus points + a 3-option random spell choice per level.
- **VisionSystem** — room-wide lighting for lit rooms, personal `vision_range`
  Chebyshev radius otherwise, permanent tile reveal once seen, no shadow casting
  (simple and cheap, deliberately). Trap detection was **designed but never
  finished** — see §8.
- **VaultInjector** — stamps hand-authored room templates into procgen floors
  post-BSP, pre-entity-spawn. Guaranteed vaults placed first (retry-the-floor if
  no eligible room), weighted vaults fill remaining eligible rooms.
- **CampaignSystem / FloorManager** — campaign = ordered JSON level list;
  `depth` field drives all depth-aware lookups independently of sequence
  position, so a handcrafted level at depth 10 spawns depth-10 content regardless
  of where it sits in the list. Hub floors (`is_hub: true`) tracked separately.
  FloorManager snapshots/restores floors on transition and auto-saves after every
  one.

---

## 4. Content Data Model

Everything is JSON under `data/`, one file per definition, keyed by an `"id"`
field the registry uses as its dictionary key. The canonical shapes (trimmed):

```json
// data/entities/monsters/goblin.json
{
  "id": "goblin", "display_name": "Goblin", "sprite_ref": "goblin.png",
  "stats": {"hp": 8, "max_hp": 8, "strength": 3, "dexterity": 4, "vision_range": 6},
  "combat": {"damage_min": 1, "damage_max": 4, "damage_type": "physical"},
  "ai": {"behavior": "chaser", "state": "asleep"},
  "xp_value": 10, "noise_radius": 3, "death_sound": "sfx/death_goblin.ogg",
  "loot_table": {"entries": [{"item_id": "dagger", "weight": 5, "quantity": [1, 1]}]}
}
```

```json
// data/items/weapons/long_sword.json
{
  "id": "long_sword", "display_name": "Long Sword", "item_type": "weapon",
  "equippable": true, "slot": "main_hand",
  "stat_modifiers": [
    {"stat": "damage_min", "operation": "add", "value": 3},
    {"stat": "damage_max", "operation": "add", "value": 8}
  ]
}
```

Items carry no `rarity` field — rarity is a spawn-time roll, never stored data.
Spells, effects, dialogs, shops, campaigns, and vaults follow the same
data-first pattern; full shapes are in Appendix B. The eight canonical damage
types are `physical, fire, cold, lightning, poison, holy, arcane, necrotic`.

---

## 5. NPCs, Dialog & Shops — the "Python knows nothing" pattern

This is the cleanest system boundary the project produced and the template the
rebuild should generalize from, not just repeat for this one feature.

Python's entire involvement: one component (`InteractableComponent(data: dict)`,
an opaque blob Python never reads the contents of), three registry namespaces
(`npcs`, `dialogs`, `shops`), VaultInjector support to place them, and two engine
hooks that emit `entity_interacted` (bump-into-NPC, and an explicit interact key)
without knowing what an NPC, dialog, or shop *is*. Everything downstream —
graph walking, buy/sell, stock refresh, gold pools, curse reveals in dialog,
journal entries — is Lua (`npc_interaction.lua`, `dialog_walker.lua`, `shop.lua`).
Dynamic state (current dialog node, shop stock, gold) lives in Lua-managed
floor/campaign state, which is captured in the floor snapshot automatically with
zero special-case Python handling.

**Generalize this pattern:** any future "kind of interactable thing" — a lever, a
readable book, a crafting station — should default to being a new vault marker
type interpreted by a new Lua script, not a new Python component or system,
unless there's a concrete reason Python needs to reason about it directly (there
rarely is).

---

## 6. UI Architecture

A data-driven runtime, not hand-coded screens. `UIRuntime` parses widget-tree
JSON (`Panel`, `Label`, `Bar`, `Button`, `List`, `Grid`) and renders the live
panel stack every frame. The exact same tree format is used in three places:
`ui_skin.json` (screens Python must own), Python's own `create_panel` calls, and
Lua's `engine.create_panel` calls — there is no second, lesser UI format for
scripted content.

**Only two screens are Python-owned**, and only because they must run before Lua
initializes: `main_menu` and `save_select`. Everything else — inventory,
spellbook, level-up, spell choice, dialog, shop, journal, minimap — is created at
runtime by a Lua script reacting to an event. The targeting cursor overlay is a
third, special engine-owned mode (not a panel) triggered by `spell_cast_initiated`.

`data/config/ui_skin.json` holds screen geometry; Lua can create, update, and
destroy panels at runtime using the identical format, so a modder adding a quest
tracker HUD element writes one Lua script and zero Python.

---

## 7. Lua Scripting Layer

`lupa` (Lua 5.4). **Sandboxing is not automatic** — `lupa` exposes the full stdlib
by default, so the dangerous surface (`io`, `os`, `package`, `require`, `load`,
`loadfile`, `dofile`, `debug`, `rawget`, `rawset`) is explicitly nulled at init,
before any engine API is exposed. Kept: `math`, `string`, `table`, `ipairs`,
`pairs`, `type`, `tostring`, `tonumber`, `pcall`, `error`, `assert`, `select`,
`unpack` — enough for real logic, nothing that touches the host. All Lua errors
are caught and logged; a broken script never crashes the game, only itself.

The `engine.*` API surface (entity read/write, lifecycle, combat/effects, AI
control, inventory/rewards, world read/write, floor- and campaign-scoped
persistent state, narrative logging, panel control, dialog/shop screen openers,
event subscription with `tag_floor_scoped` for automatic floor-exit cleanup) is
the full contract between content and engine. `set_context()` must be called
after every floor load; `clear_floor_subscriptions()` on every floor exit.

**Design rule worth keeping explicit:** `set_stat` is for non-derived flags and
counters only — HP/MP/damage always route through `apply_effect` or
`deal_damage` so they go through the same resolution pipeline as everything
else. This convention was documented but easy to violate by accident; the
rebuild should consider enforcing it (e.g. reject `set_stat` calls on
combat-derived stat names) rather than relying on script authors reading the
docs.

The base script library (`scripts/*.lua`) served three roles simultaneously —
playable features, a canonical living reference for the API, and the snippet
source the visual quest editor generates against. That three-birds-one-stone
framing is worth preserving deliberately: any new base script should be written
to double as documentation and as a snippet-library entry, not bolted on after
the fact.

---

## 8. Heuristic Animation System

A late addition, and a good worked example of the event-driven discipline paying
off: a render-layer-only subscriber (`engine/ui/anim_system.py`) that turns combat
and status events into transient visuals — projectile/swoosh/impact-flash/aura-
pulse/area-burst/tile-fade — using heuristics (damage type → color, damage amount
→ scale) that live entirely in the renderer as configuration, not game data.
Removing it entirely leaves the engine and every JSON file untouched, because it
requires **zero new fields** on any event payload — combat already carried
everything it needed (positions, damage type/amount, hit/miss).

**Lesson for the rebuild:** this system could only be a pure listener with no
new fields *because* the combat/status events already carried rich payloads. That
wasn't planned — it was a lucky consequence of the event design. Next time,
design the animation/VFX contract at the same time as the first combat
implementation (even if the renderer piece comes later), so payload shape is
chosen deliberately rather than discovered to be sufficient after the fact.

---

## 9. Content Creation Editor Suite

A standalone pygame-ce application (`editor.py`), independent of the game
runtime, that grew into the dominant investment of the project's later phases —
more session time went into editor ergonomics than into the original engine
phases. That ratio is a planning input for the rebuild, not just a historical
note (see §10).

**Project Manager** is the entry point. A *project* is a working folder the
editor owns — loose files, git-trackable — never an archive; archives are a pure
export step. New Project → designer is in the map editor within seconds, blank
map + default tileset already loaded. Open Project also unpacks an existing
`.pak`/`.pkd` into a fresh project folder for editing. Pack Project validates and
writes `.pak`/`.pkd` build artifacts into `dist/` without touching the loose
source.

**Four core modes**, tab bar + F1–F4, each `handle_event()/update()/draw()`,
switching via callbacks so modes never know each other's internals — plus two
tabs added after the fact (Audio, Quest):

- **Map Editor** — paint tiles, place entities/NPCs/items/transitions, author
  vaults on the same toolset at vault-sized canvas, author campaigns (drag-reorder
  levels, hub flags), validation warnings (no stairs and no completion trigger,
  duplicate IDs).
- **Sprite Editor** — pixel-level painting against the same registry-aware
  palette; grew a Sheet Mode with animation preview, multi-size round/square
  brushes, and a mirror tool over time.
- **Data Editor** — schema-aware forms (not a raw JSON editor) per content type,
  a sub-editor for effect arrays, live JSON preview, saves directly into the
  project's data layout.
- **Skin Editor** — visual authoring for `ui_skin.json` with a live preview pane
  and a palette color-picker for `ui_config.json`.
- **Sprite Manager** (added later) — a needed-sprite/has-sprite catalogue across
  all entity types, with import-and-link-back so creating a sprite from the
  needs-list writes `sprite_ref` back onto the source JSON automatically. Folded
  in the tile/autotile-variant view once the 16-wall-variant gap (§10) surfaced.
- **Audio tab** (added later) — plain file-manager import/organize for `sfx/`
  and `music/`, mirroring the sprite import pattern.

**Context links** across modes (right-click a palette tile → jump to Sprite
Editor on that PNG; place an entity with no JSON def → jump to Data Editor
pre-filled) were a deliberate ergonomics investment, not a nice-to-have — worth
budgeting for explicitly in the rebuild rather than treating cross-navigation as
a stretch goal.

**Visual Quest & Dialog Editor** (Phase 9d) — the most structurally important
editor decision: **the GUI generates Lua, and the generated `.lua` file is the
one and only source of truth.** A quest built entirely by dragging nodes from a
snippet-driven palette (trigger/condition/action/reward/narrative/ui/world,
sourced from `data/scripts/snippets.json`) produces the identical file a
scripter could write by hand. A "View/Edit Script" escape hatch opens the
generated Lua directly; on save, the GUI re-parses it, renders recognized
patterns back as nodes, and preserves anything it can't parse as an opaque grey
"Custom Block" that survives every future round-trip untouched. This is what
let designers build 80% of a quest visually and a scripter finish the rest
without either side clobbering the other's work — a pattern worth generalizing
to any future "generate code from a GUI" feature, not just quests.

The Dialog Editor is a thinner case of the same idea, but simpler: it's a pure
GUI layer over the *existing* dialog-graph JSON format (no generation step, no
round-trip fidelity risk) — the visual editor and the hand-editable JSON are the
literally same file, just two views on it.

---

## 10. Modding & Archive System

Identical intent to Bethesda's BSA/load-order model: **loose files always win**,
archives are a shipping container, never an authority. `.pak` (data) and `.pkd`
(assets) are both renamed zip files, read identically regardless of extension —
the extension is a human-readable signal, not a functional distinction. Load
order: base archives → mod archives (`mods/load_order.txt`, top = lowest
priority) → loose files. Same file path or JSON `id` at higher priority silently
overrides; no conflict is ever an error. Mod folder structure mirrors the base
game layout exactly, so packing is always "zip the folder, rename the
extension" — no special tooling required conceptually, though the editor
provides a Pack button.

**Never implemented:** archive loading itself (`DataRegistry._load_archive`) was
a documented no-op stub for the entire project — "Phase 9/10 fills this in" was
written down early and never actually closed out, which meant the mod system was
conceptually complete but never exercised end-to-end. See §10.1.

---

## 11. Lessons Learned & Deliberate Course Corrections

This section is the point of writing this doc instead of just re-implementing
the phase plan verbatim. These are the specific things that went wrong or had to
be corrected mid-project, worth designing around from day one this time.

**1. "Built but not wired" was the recurring failure mode, not one-off bugs.**
The single biggest gap found in the bug-review pass wasn't a logic bug — it was
that the *entire loot pipeline* (LootSystem, AffixGenerator, floor/rarity
configs, the `loot_drop` event chain) was fully implemented and fully tested in
isolation, but `place_floor_loot()` was never actually called from the map
generation pipeline, and every monster's `loot_table` was empty. Items could not
appear in the game world by any method, despite the entire system existing.
The same shape repeated smaller: `vfx_play` was emitted with nothing subscribed
to render it; `damage` was subscribed to by the renderer for hit VFX while
`EffectResolver` actually emitted `damage_dealt` — the hit-slash animation
silently never fired. **Design response for the rebuild:** a system is not
"done" until its call site exists and something has walked the full event chain
end-to-end at least once, in-game, not just in a unit test of the system in
isolation. Treat "wire it into the actual pipeline" as part of the same task as
building the system, not a follow-up ticket.

**2. Editor ergonomics were consistently underweighted relative to the engine.**
An entire late-project pass (`docs/editor-bugs-todos.md`) existed solely to
retrofit: tooltips across four different editor panels, a visible Save button
(it worked via Ctrl+S but had no discoverable affordance), sprite-cache
invalidation after cross-editor saves, dropdown popups clipping off-screen,
missing column headers on form tables, ID fields that only accepted picker
selection and not typed input. None of these were engine bugs — they were "a
human other than the original author tries to use this tool" bugs, surfaced
late because the tools were dogfooded by their own author first. **Design
response:** budget an explicit usability pass as part of each editor tool's own
phase (tooltips, save affordance, cache invalidation, keyboard-only paths) rather
than as a separate deferred phase discovered by a bug-review session months
later.

**3. The autotile wall system was designed once and had to be re-exposed later.**
The renderer's 16-variant cardinal-bitmask wall autotiling was built into the
engine from early on, but the Sprite Manager and theme JSON schema only ever
exposed a single generic `"wall"` slot — so for a long stretch, creators had no
way to assign the 15 other variants the renderer was already capable of using,
and every wall rendered identically regardless of shape. **Design response:**
when a renderer or engine feature has a data contract (here: 16 named sprite
slots), the content-authoring surface for that contract should ship in the same
phase as the renderer feature, not be treated as a separate editor task
discovered later.

**4. Room/corridor structure was designed for procgen and never retrofitted to
handcrafted maps.** AI noise attenuation depends on `room_id`, which BSP procgen
populates automatically but handcrafted maps never got — the map editor never
grew room/corridor authoring tools (flagged as an incomplete task, §8.1), so
every handcrafted map silently loses noise-attenuation-by-room and falls back to
pure spatial radius. **Design response:** either make the gameplay system
degrade explicitly and visibly when authored data is thin (a warning in the
editor: "this map has no room_id data — noise attenuation will use radius only"),
or don't let two content-authoring paths (procgen vs. handcrafted) diverge in
what data they populate for the same downstream system.

**5. Some decisions, made once and never revisited, were unqualified wins.**
Chebyshev distance as the single distance function everywhere; equal-cost
diagonal movement with no corner-cutting check; "new component, never modify an
existing one" as the ECS growth rule; keeping NPCs/dialog/shops fully out of
Python. None of these needed correction later — they're listed here specifically
so the rebuild keeps making them as first decisions, not rediscovers them after
a messier alternative gets tried first.

**6. Known gaps at the point of termination** (not design flaws — just
unfinished work, listed so the rebuild can consciously decide to fix, defer, or
drop each one rather than rediscover it by surprise):
- Inventory and spellbook UI screens: the Lua panels (`inventory.lua`,
  `spellbook.lua`) exist and are documented, but `open_inventory`/
  `open_spellbook` were never wired to a keybind/handler in `main.py`.
- Trap detection: `trap_detect_radius` is a real derived stat, VisionSystem has
  the scan loop, but there's no `TrapComponent` and no trigger system — the stat
  has never had any functional effect.
- Difficulty scaling always reads `thresholds[0]` regardless of actual floor
  depth — the depth-aware multiplier the config schema supports was never wired
  up in `CombatSystem`.
- Hotbar active-slot highlighting was never implemented (`active = False` is a
  hardcoded placeholder in the renderer).
- Archive loading (`.pak`/`.pkd` reading) was a stub for the project's entire
  life — the mod system's distribution half was never exercised.
- Content volume was thin throughout: ~10 monsters, 5 spells, 3 status effects,
  1 NPC/dialog/shop, no ranged-ammo flow tested end-to-end, no vault with a
  guaranteed spawn ever authored to exercise VaultInjector's guaranteed path.
- The `assets/` directory never existed in the working tree — every session ran
  entirely on colored-rectangle fallbacks; visuals and audio were never actually
  seen/heard end-to-end, only exercised through the fallback path.

---

## 12. Explicitly Out of Scope for a v1 Rebuild

Carried forward as acknowledged deferrals, not oversights, so the rebuild can
choose deliberately rather than default into the same order:

- PyInstaller Windows packaging (Phase 10) — depends on a finished archive
  pipeline, which itself was never finished; sequence this correctly this time
  (archive read/write before packaging, not stubbed through both).
- Full room/corridor authoring tools for handcrafted maps (map editor task).
- Trap component/trigger system.
- Content volume (monster/spell/item/dialog/shop library) — an ongoing content
  task, not an engine task; don't let it block engine completeness sign-off
  again.

---

## Appendix A — Event Name Index (as of end-of-life)

`campaign_complete, campaign_selected, cancel_targeting, check_wake, damage,
damage_dealt, death, entity_died, entity_interacted, entity_moved,
entity_spawned, equip_changed, floor_changed, game_complete,
identify_type_request, item_dropped, item_pickup, item_used, level_up_pending,
load_game_selected, loot_drop, message, miss, new_game_selected,
noise_emitted, play_sound, player_died, player_moved, quit_selected,
ranged_attack_attempt, ranged_attack_blocked, resolve_hit, save_slot_selected,
set_bonus_changed, show_panel, spell_cast_cancelled, spell_cast_initiated,
spell_choice_pending, spell_chosen, spell_learned, spell_target_chosen,
stair_use, stat_allocated, stat_modifier_applied, status_applied,
status_expired, target_confirmed, trap_revealed, trigger_level_complete,
vfx_play` — plus the Phase 8.5 interaction set (`dialog_opened`,
`dialog_choice_selected`, `dialog_closed`, `shop_opened`, `buy_item`,
`sell_item`, `trade_completed`, `shop_refreshed`, `portal_target_changed`).

## Appendix B — Data Namespace Reference

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
| `configs` | `data/config/` (keyed by filename) |
| `npcs` | `data/entities/npcs/` |
| `dialogs` | `data/dialogs/` |
| `shops` | `data/shops/` |

## Appendix C — Source Material

This document was synthesized from the SLATE repository at its final state:
`CLAUDE.md`; `docs/system-reference-core.md` and `docs/system-reference-systems.md`
(as-built reference); `docs/phase-1.md` through `docs/phase-10.md` and the full
`phase-9a/9b/9c/9d` sub-phase family (editor, Lua layer, base scripts, visual
quest editor); `docs/modding.md`; `docs/heuristic-animations.md`;
`docs/editor-bugs-todos.md`; `docs/editor-polish-handoff.md`; `TODO.md`; and the
project's commit history.
