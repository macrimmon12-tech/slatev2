# Component 04 — Inventory, Items & Loot

**Wave:** 1 (parallel).
**Depends on:** Wave 0 (`00-foundation-core.md`) + `docs/CONTRACTS.md`.
Hard dependency on `01-stats-combat.md`'s `StatsSystem.add_modifier`/
`remove_modifiers_by_tag_prefix` (equip/unequip) and `EffectResolver.apply_effect_list`
(item use) — see §1.1. Cross-referenced by `06-worldgen-campaign.md`, which
**must** call this component's `place_floor_loot()` — see §1.2, the single
most important wiring point in this doc.

## 1. Scope & Boundaries

You are building four systems: `InventorySystem` (pickup/drop/use/equip/
unequip/identify), `AffixGenerator` (rarity-budgeted affix rolling),
`SetTrackerSystem` (equipment set bonus tracking), and `LootSystem` (both
monster-drop resolution and ambient floor loot placement).

### 1.1 Building against `01` before it merges

Write against `01-stats-combat.md`'s documented signatures
(`StatsSystem.add_modifier(entity_id, tag, stat, op, value)`,
`remove_modifiers_by_tag_prefix(entity_id, tag_prefix)`,
`EffectResolver.apply_effect_list(effects, source_id, target_id, world,
event_bus, position=None)`). Mock the event bus boundary per CONTRACTS.md
§8 if `01` isn't merged yet when you write tests; re-verify against the
real thing once it lands.

### 1.2 THE `place_floor_loot` wiring requirement — read this before anything else

**v1's single biggest failure (spec §11, lesson 1) was this exact
function.** `place_floor_loot()` was fully built, fully unit-tested, and
**never called** from the map generation pipeline. Every monster's
`loot_table` was also empty end-to-end. The result: items could not appear
in the game world by any method, despite the entire loot system existing
and passing its own tests.

This is not going to be re-litigated as an abstract lesson — it is a hard,
concrete requirement on this component's Definition of Done:

- You **own and export** a function with this exact call signature:
  ```python
  def place_floor_loot(world: World, floor_id: str, depth: int) -> list[dict]:
      """Scatters ambient floor loot across the already-generated floor
      identified by floor_id, at the given depth (drives depth-window
      rarity rolls per §5.4). Returns the list of placed item-instance
      records (for testing/telemetry). Reads eligible floor tiles via
      SpatialHash/tilemap query (owned by 06 — this function receives
      `world` fully populated with the floor's tiles/rooms already in
      place; it does not generate geometry itself). Must be a complete
      no-op-but-harmless call on a floor with zero eligible tiles (absence
      = zero cost) — never raises on an empty/degenerate floor.
      """
  ```
- **Your Definition of Done (§7) requires an integration test that runs
  real floor generation from `06-worldgen-campaign.md` and asserts items
  actually exist on the tile map afterward** — not just that
  `place_floor_loot()`'s internals return correct data when called
  directly in isolation. See §8's integration test for the exact shape.
- **This doc is the canonical source for that call signature.** Whoever
  picks up `06-worldgen-campaign.md` is told (in that doc, cross-linked
  back here) that its floor-generation pipeline **must call
  `place_floor_loot(world, floor_id, depth)`** as a real pipeline step,
  post-BSP/post-vault-injection, pre- or alongside entity spawning. If
  you're building this component before `06` exists, you cannot force `06`
  to call you — but you must (a) make the signature and this requirement
  impossible to miss (this section), (b) write your own integration test
  against a **minimal fixture floor generator** (a fixture tilemap/room
  fixture living under `tests/fixtures/`, not real BSP) that proves your
  own function places items correctly when called, and (c) explicitly flag
  in your PR description that `06`'s wiring call is an external dependency
  your component cannot itself satisfy, so Wave 3 integration verification
  checks it.

In scope:
- `InventorySystem`: pickup, drop, use, equip, unequip, identify.
  Cursed items auto-identify (curse revealed) and lock (cannot unequip)
  the moment they're equipped. Two-tier identification: `identify_type`
  (resolves for every instance of a base item ID at once — "identify one
  potion, know them all") vs. `identify_instance` (curse reveal only,
  personal to that specific item instance, never leaks the base type's
  general identity).
- `AffixGenerator`: budget-by-rarity affix selection, `common=0` through
  `legendary=14` (CONTRACTS.md §10), weighted `1/cost` (cheap affixes
  common, expensive ones rare), `conflict_tags` preventing incompatible
  pairs, depth-gated affix tiers. Legendaries bypass this generator
  entirely — fixed affix sets, always pre-identified, uniqueness enforced
  via foundation's `spawned_uniques` save-state set.
- `SetTrackerSystem`: reacts to `equip_changed`, counts currently-equipped
  pieces per `set_id`, applies threshold bonuses as **full replacement
  lists**, not additive stacking across tiers (equipping a 4th piece
  replaces the 2-piece bonus's modifiers entirely with the 4-piece
  tier's list, it does not add both).
- `LootSystem`: monster drops (weighted `loot_table` entries per monster
  data, boss/elite entities check a legendary-first lookup before the
  normal table) **and** ambient floor loot via `place_floor_loot()` (§1.2).
- Rarity is **never** stored on an item definition — it's rolled at spawn
  time by interpolating between the item's `min_depth`/`max_depth` window
  (common-heavy near `min_depth`, rare/epic-heavy near `max_depth`).

Out of scope:
- Actually applying stat modifiers on equip beyond calling `01`'s
  `add_modifier` — you own the equip *flow* (which item, which slot, which
  tags), `01` owns the modifier math.
- Floor/room generation itself — `06` owns geometry; you own what gets
  placed on top of already-generated geometry.
- UI panels for inventory/spellbook screens — spec §6/§10-lua notes these
  as Lua-authored panels reacting to your events; not this component's job.

## 2. Provides

### 2.1 `InventorySystem`

```python
# engine/systems/inventory.py

class InventorySystem:
    def __init__(self, world: World, event_bus: EventBus): ...

    def pickup(self, entity_id: int, item_instance_id: str) -> bool: ...
    def drop(self, entity_id: int, item_instance_id: str, position: tuple[int, int]) -> bool: ...
    def use(self, entity_id: int, item_instance_id: str) -> bool:
        """Consumables: resolves the item definition's `effects` list via
        01's EffectResolver.apply_effect_list(effects, entity_id, entity_id,
        world, event_bus). Removes the item from inventory on success if
        `consumable: true`."""
    def equip(self, entity_id: int, item_instance_id: str, slot: str) -> bool:
        """Moves item to equipped slot. Reads the item's resolved
        stat_modifiers (base item + rolled affixes, see §5.1/§5.2) and calls
        StatsSystem.add_modifier once per modifier entry with tag
        `item_{item_instance_id}_{i}`. If the item instance is cursed
        (ItemInstance.cursed == True), immediately calls identify_instance
        on it (curse reveal) and sets ItemInstance.locked = True (unequip
        refused while locked — see unequip below). Emits equip_changed."""
    def unequip(self, entity_id: int, slot: str) -> bool:
        """Refuses (returns False, no state change) if the currently
        equipped item in that slot has ItemInstance.locked == True (cursed
        and revealed). Otherwise removes the item, calls
        StatsSystem.remove_modifiers_by_tag_prefix(entity_id,
        f"item_{item_instance_id}_"), emits equip_changed with
        item_instance_id=None."""
    def identify_type(self, item_base_id: str) -> None:
        """Marks every instance of item_base_id, anywhere (inventory, world,
        future drops), as identified — a registry-keyed identified-set, not
        a per-instance flag, since 'identify one potion, know them all' is
        global to the base type for the remainder of the run. Triggered by
        consuming identify_type_request."""
    def identify_instance(self, item_instance_id: str) -> None:
        """Reveals curse status on exactly this one instance, personal —
        does not affect identification of the base type or any other
        instance of the same base item."""
```

`ItemInstance` (new component or component-held dataclass, registered in
`_COMPONENT_REGISTRY` if it's itself a component; if items are stored as
plain dicts in an `InventoryComponent` list rather than as separate
entities, `ItemInstance` is a plain dataclass field shape, not a component
— pick one and document your choice in your PR; default recommendation:
items ARE entities (so they can exist on the floor as pickups, in
inventory, or equipped, uniformly) carrying an `ItemInstanceComponent`):

```python
@component
@dataclass
class ItemInstanceComponent:
    base_id: str                       # e.g. "long_sword" — registry key into `items`
    instance_id: str                   # unique per rolled instance
    rarity: str                        # rolled at spawn, "common".."legendary" — NEVER read from the item def
    affixes: list[dict] = field(default_factory=list)   # rolled affix entries, see §5.2
    identified_type: bool = False
    cursed: bool = False
    curse_identified: bool = False
    locked: bool = False               # true once a cursed item is equipped+revealed
    quantity: int = 1
```

`InventoryComponent`:

```python
@component
@dataclass
class InventoryComponent:
    item_instance_ids: list[str] = field(default_factory=list)
    equipped: dict[str, str | None] = field(default_factory=dict)  # slot -> instance_id
```

### 2.2 `AffixGenerator`

```python
# engine/systems/affixes.py

def roll_affixes(item_base_id: str, rarity: str, depth: int) -> list[dict]:
    """Selects a weighted-random affix set for a newly-spawned item
    instance. Budget per rarity (CONTRACTS.md §10):
      common=0, uncommon=?, rare=?, epic=?, legendary=14 (endpoints fixed
      by CONTRACTS.md; intermediate tier budgets are this component's call
      — see §5.3 for the committed values).
    Affixes are drawn from the `affixes` registry namespace, filtered to
    those whose depth-gate (`min_depth`/`max_depth`) includes `depth`,
    weighted by `1 / affix["cost"]` (cheap = common), respecting
    `conflict_tags` (an affix sharing a conflict_tag with an already-picked
    affix in this roll is excluded from further picks this roll), summing
    `cost` until the rarity's budget is met or no eligible affixes remain
    (fewer affixes than budget target is a valid, silent outcome — never an
    error). Legendary items do NOT call this function — they use fixed
    affix sets from their own definition (§5.5).
    """
```

### 2.3 `SetTrackerSystem`

```python
# engine/systems/sets.py

class SetTrackerSystem:
    def __init__(self, world: World, event_bus: EventBus): ...
    # subscribes to equip_changed in __init__

    def _recompute(self, entity_id: int, world: World, event_bus: EventBus) -> None:
        """On every equip_changed for entity_id: count currently-equipped
        pieces per set_id (an equipped item's base item def may carry a
        `set_id` field, see §5.1). For each set_id with >=1 equipped piece,
        find the highest-threshold tier in the set's data
        (registry namespace `sets`) whose `pieces_required` is met by the
        current count. Clear ALL previously-applied set-bonus modifiers for
        that set_id via StatsSystem.remove_modifiers_by_tag_prefix(entity_id,
        f"set_{set_id}_") FIRST, then apply the matched tier's
        stat_modifiers list via add_modifier with tag
        `set_{set_id}_{tier}_{i}` — full replacement, never additive across
        tiers (a 4-piece tier's bonus list is applied instead of the
        2-piece tier's, not on top of it). If no tier's threshold is met
        (e.g. 1 piece with a 2-piece minimum), no modifiers are applied and
        any previous ones stay cleared. Emits set_bonus_changed
        {entity_id, set_id, active_tier} (active_tier is None if no
        threshold met)."""
```

### 2.4 `LootSystem`

```python
# engine/systems/loot.py

def resolve_loot_table(loot_table: dict, depth: int, is_boss: bool) -> list[dict]:
    """Given a monster's raw loot_table (§5.6) or a floor loot pool, rolls
    weighted entries into concrete item spawn requests
    ({item_base_id, quantity}). If is_boss (or entity has an `elite: true`
    flag), first checks a legendary-first lookup: a per-depth-window
    legendary drop-chance roll against the `legendaries` namespace filtered
    by depth eligibility and NOT already in spawned_uniques (foundation's
    save-state set) — on success, returns that legendary instead of rolling
    the normal table; on failure, falls through to the normal weighted
    table."""

def spawn_item_instance(item_base_id: str, depth: int, position: tuple[int, int],
                         world: World) -> str:
    """Creates a real ItemInstanceComponent-bearing entity at position,
    rolling rarity via depth-window interpolation (§5.4) and calling
    AffixGenerator.roll_affixes for its affixes (or applying a legendary's
    fixed set + adding to spawned_uniques if item_base_id is in the
    `legendaries` namespace). Returns the new instance_id."""

def place_floor_loot(world: World, floor_id: str, depth: int) -> list[dict]:
    """See §1.2 — THE function 06's floor-generation pipeline must call.
    Scatters ambient loot across eligible floor tiles (not on walls, not on
    stairs, not overlapping existing entities — reads eligibility via
    SpatialHash/tilemap already populated by 06). Item pool and density
    driven by data/config/loot_tables.json's floor-loot section (§5.7),
    depth-window rarity applies the same as monster drops. Returns the list
    of {instance_id, item_base_id, position} placed."""
```

**The `loot_drop` handoff (clarifying the split with `01`, per task
brief):** `01`'s `CombatSystem.handle_potential_death` emits `loot_drop`
`{position, entries}` purely as a signal that a death occurred with
drop-worthy loot — `entries` is the monster's *raw, unresolved*
`loot_table.entries` list, nothing has been rolled yet. **This component's
`LootSystem` is what subscribes to `loot_drop` and does the actual work**:
calling `resolve_loot_table` on those entries, then `spawn_item_instance`
for each result at `position`. `01` never calls `resolve_loot_table` or
`spawn_item_instance` itself — it only signals that a drop should happen
and hands over the raw table.

## 3. Consumes

| Event | Used for |
|---|---|
| `loot_drop` | `LootSystem` resolves the raw `entries` into real item instances at `position` — see handoff note above |
| `identify_type_request` | `InventorySystem.identify_type(item_base_id)` |
| `equip_changed` | `SetTrackerSystem` recompute (also read by `01`'s `StatsSystem`, per CONTRACTS.md's table — both are legitimate independent subscribers) |

Calls into (direct):
- `01`'s `StatsSystem.add_modifier` / `remove_modifiers_by_tag_prefix`
  (equip/unequip, set bonuses).
- `01`'s `EffectResolver.apply_effect_list` (consumable item use).

Emits:
- `item_pickup` `{entity_id, item_instance_id}`
- `item_dropped` `{entity_id, item_instance_id, position}`
- `item_used` `{entity_id, item_instance_id}`
- `equip_changed` `{entity_id, slot, item_instance_id | None}`
- `set_bonus_changed` `{entity_id, set_id, active_tier}`
- `loot_drop` is **consumed**, not emitted, by this component (emitted by
  `01` — see handoff note).

## 4. File/Directory Ownership

You own, exclusively:
- `engine/systems/inventory.py`, `affixes.py`, `sets.py`, `loot.py`
- `tests/unit/test_inventory.py`, `test_affixes.py`, `test_sets.py`, `test_loot.py`
- `tests/fixtures/items/`, `tests/fixtures/affixes/`, `tests/fixtures/sets/`
- `tests/fixtures/floors/` — a **minimal fixture floor** (tilemap + rooms,
  hand-authored, not real BSP) used by your own `place_floor_loot`
  integration test until `06` merges (see §1.2, §8).
- `data/items/` (excluding `items/legendary/`, `items/sets/` — those
  namespaces' content structure is yours too, just noted as separate
  registry namespaces per CONTRACTS.md §4).
- `data/affixes/`
- `data/items/sets/`
- `data/config/loot_tables.json` (floor-loot pool + density config, §5.7)

You add entries to:
- `engine/core/save.py:_COMPONENT_REGISTRY` — add `ItemInstanceComponent`,
  `InventoryComponent`, alphabetically.
- `docs/components/06-worldgen-campaign.md` is told (in that doc itself,
  written independently) to call your `place_floor_loot` — you do not edit
  that doc; the cross-reference exists in both directions by construction
  of this batch.

## 5. Data Schemas

### 5.1 Item JSON schema (spec §4, extended with `set_id`/`min_depth`/`max_depth`)

```json
{
  "id": "long_sword",
  "display_name": "Long Sword",
  "item_type": "weapon",
  "equippable": true,
  "slot": "main_hand",
  "min_depth": 1,
  "max_depth": 12,
  "set_id": null,
  "consumable": false,
  "stat_modifiers": [
    {"stat": "damage_min", "operation": "add", "value": 3},
    {"stat": "damage_max", "operation": "add", "value": 8}
  ]
}
```

No `rarity` field, ever — enforced by a load-time assertion (fail loudly if
one is present in content, matching "rarity is never stored on an item
definition"). A consumable potion carries `"consumable": true` and its own
top-level `"effects"` list (same shape as `01`'s effect list) instead of
`stat_modifiers`.

### 5.2 Affix JSON schema

```json
{
  "id": "affix_of_the_bear",
  "display_name": "of the Bear",
  "cost": 3,
  "min_depth": 1,
  "max_depth": 20,
  "conflict_tags": ["strength_affix"],
  "stat_modifiers": [
    {"stat": "strength", "operation": "add", "value": 4}
  ]
}
```

A rolled affix entry stored on `ItemInstanceComponent.affixes` is the full
affix dict (or at minimum `{"id": ..., "stat_modifiers": [...]}`) so
`InventorySystem.equip` can apply its modifiers without a registry
re-lookup mid-equip.

### 5.3 Rarity budget table (CONTRACTS.md §10 fixes the endpoints; committing
intermediate values here since neither the spec nor CONTRACTS.md gives them)

| Rarity | Affix cost budget |
|---|---|
| common | 0 |
| uncommon | 4 |
| rare | 8 |
| epic | 12 |
| legendary | 14 (bypassed — fixed sets, §5.5) |

### 5.4 Depth-window rarity interpolation

Given an item's `min_depth`/`max_depth` and the current spawn `depth`,
compute `t = clamp((depth - min_depth) / max(1, max_depth - min_depth), 0, 1)`.
Rarity weight table interpolates linearly between a common-heavy
distribution at `t=0` and a rare/epic-heavy distribution at `t=1`:

```json
{
  "t_0": {"common": 70, "uncommon": 20, "rare": 8, "epic": 2, "legendary": 0},
  "t_1": {"common": 10, "uncommon": 20, "rare": 30, "epic": 30, "legendary": 10}
}
```

Weights at intermediate `t` are linearly interpolated per rarity, then
normalized and rolled. This table lives in `data/config/loot_tables.json`
(§5.7) so it's tunable without a code change, per "data over code."

### 5.5 Legendary schema (fixed affix set, always pre-identified)

```json
{
  "id": "sunfang",
  "display_name": "Sunfang",
  "item_type": "weapon",
  "slot": "main_hand",
  "min_depth": 8,
  "stat_modifiers": [
    {"stat": "damage_min", "operation": "add", "value": 10},
    {"stat": "damage_max", "operation": "add", "value": 20},
    {"stat": "fire_damage_bonus", "operation": "multiply", "value": 1.25}
  ]
}
```

No `affixes` field — legendaries carry their full bonus directly in
`stat_modifiers`; `AffixGenerator.roll_affixes` is never called for them.
`spawn_item_instance` sets `identified_type = True` unconditionally for any
`legendaries`-namespace item and adds `item_base_id` to
`spawned_uniques` at spawn time (not at pickup) so a second legendary of
the same ID never rolls again this run, matching foundation's save-state
contract.

### 5.6 Monster `loot_table` (spec §4 baseline)

```json
{
  "loot_table": {
    "entries": [
      {"item_id": "dagger", "weight": 5, "quantity": [1, 1]},
      {"item_id": "health_potion", "weight": 10, "quantity": [1, 3]}
    ]
  }
}
```

`weight` is relative (not required to sum to 100); `quantity` is a
`[min, max]` inclusive range rolled per drop.

### 5.7 `data/config/loot_tables.json` (you own this file — floor-loot pool +
interpolation table)

```json
{
  "schema_version": 1,
  "rarity_interpolation": {
    "t_0": {"common": 70, "uncommon": 20, "rare": 8, "epic": 2, "legendary": 0},
    "t_1": {"common": 10, "uncommon": 20, "rare": 30, "epic": 30, "legendary": 10}
  },
  "floor_loot": {
    "items_per_room_min": 0,
    "items_per_room_max": 2,
    "eligible_item_pool": "all",
    "gold_pile_weight": 5
  }
}
```

`eligible_item_pool: "all"` means any item in the `items` namespace whose
depth window includes the floor's depth is eligible; a future refinement
could scope this per-floor via campaign data, but that's not required for
this component's Definition of Done.

## 6. Lessons from v1 applied here

- **This is the exact system that had the v1 "built but not wired"
  failure.** See §1.2 — treated as a hard Definition of Done gate, not a
  narrative aside. Don't consider this component done on green unit tests
  alone.
- **Rarity is a spawn-time roll, never definition data** — enforced by a
  load-time assertion rejecting any `items`/`legendaries` JSON carrying a
  `rarity` field, so a content author can't accidentally reintroduce it.
- **Set bonuses are full-replacement, not additive** — a common and easy
  mistake to get backwards; the unit test in §8 specifically covers
  equipping a 4th set piece and asserting the 2-piece tier's modifiers are
  gone, not merely that the 4-piece tier's are also present.

## 7. Definition of Done

- [ ] `ItemInstanceComponent` / `InventoryComponent` implemented,
      registered in `_COMPONENT_REGISTRY` alphabetically.
- [ ] `InventorySystem` pickup/drop/use/equip/unequip/identify_type/
      identify_instance all implemented and unit tested.
- [ ] Cursed-item auto-identify-and-lock on equip verified by a test:
      equipping a cursed item reveals curse status and a subsequent
      `unequip` call is refused.
- [ ] `identify_type` affects every instance of a base item globally;
      `identify_instance` affects only the curse status of one instance —
      tested with two instances of the same base item, only one of which
      is cursed, confirming `identify_instance` on the cursed one doesn't
      leak identification to the other.
- [ ] `AffixGenerator.roll_affixes` respects budget-by-rarity (§5.3),
      `1/cost` weighting (statistical test over many rolls skewing toward
      cheap affixes), `conflict_tags` exclusion, and depth-gating — all
      unit tested.
- [ ] Legendaries bypass the generator entirely, are always pre-identified,
      and are added to `spawned_uniques` at spawn (tested: a second spawn
      attempt of the same legendary ID after one is already in
      `spawned_uniques` is rejected/skipped).
- [ ] `SetTrackerSystem` full-replacement behavior verified: equipping
      pieces up to a 4-piece tier results in ONLY the 4-piece tier's
      modifiers being present, not the 2-piece tier's plus the 4-piece
      tier's.
- [ ] `resolve_loot_table` correctly implements weighted rolling and the
      boss/elite legendary-first lookup (falls through to normal table on
      legendary-roll failure) — unit tested.
- [ ] Rarity is never present on any `items`/`legendaries` fixture file —
      enforced by a load-time assertion with a test proving a `rarity`
      field in fixture JSON is rejected/errors.
- [ ] `spawn_item_instance` rolls rarity via depth-window interpolation —
      tested with two different depths producing statistically different
      rarity distributions (guards against a hardcoded/ignored depth
      parameter).
- [ ] `place_floor_loot(world, floor_id, depth)` exists with exactly the
      signature in §1.2, is a no-op-but-harmless on an empty/degenerate
      floor, and its own integration test (§8) proves items land on a real
      (even if fixture-minimal) tile map — not just that its internals
      return correct data in isolation.
- [ ] PR description explicitly flags, in its own section, that
      `06-worldgen-campaign.md`'s floor-generation pipeline must call
      `place_floor_loot` — this is an external wiring dependency this
      component cannot itself satisfy and Wave 3 must verify it.
- [ ] `pytest` green.

## 8. Test Plan

Unit tests (`tests/unit/test_inventory.py`, `test_affixes.py`,
`test_sets.py`, `test_loot.py`): see each Definition of Done bullet above
— one test per bullet minimum.

Integration tests (`tests/integration/` — required per CONTRACTS.md §7.3):

1. `test_loot_drop_pipeline.py` — build a real `World` + event bus + a
   fixture monster entity with a non-empty `loot_table`. Emit `loot_drop`
   `{position, entries}` directly (simulating what `01`'s real
   `CombatSystem` emits) and assert: `LootSystem`'s subscriber actually
   fired, real `ItemInstanceComponent`-bearing entities now exist in
   `world` at `position` with base IDs matching the resolved table — not a
   mock assertion that resolution "would have" happened.
2. `test_floor_loot_placement.py` (the §1.2-mandated one) — using your own
   minimal fixture floor (`tests/fixtures/floors/`, a small hand-built
   tilemap with a couple of rooms and `room_id` populated, standing in for
   what `06`'s real BSP generator will produce), call
   `place_floor_loot(world, floor_id, depth)` directly and assert: the
   returned list is non-empty for a floor with eligible rooms, and for
   each returned entry, a real `ItemInstanceComponent`-bearing entity
   exists in `world` at the claimed position, matching the actual tile
   map's floor tiles (not a wall, not a stair tile). **When `06` merges,
   add a second version of this test (or extend it) that calls `06`'s real
   floor-generation entry point and re-runs this same assertion against
   real BSP output** — note this follow-up explicitly in your PR so it
   isn't lost.
3. `test_equip_set_bonus_pipeline.py` — a real entity with `InventoryComponent`
   + `StatsComponent`, equip 2 then 4 pieces of a fixture 4-piece set in
   sequence via real `equip()` calls, and assert via `StatsSystem.get_stat`
   that the final derived stat values reflect ONLY the 4-piece tier's
   bonus (full replacement verified against real modifier resolution, not
   a mock of `SetTrackerSystem`'s internals).

## 9. Open Questions & Defaults

- **Whether items are their own entities or embedded records**: default
  recommendation is real entities with `ItemInstanceComponent` (see §2.1),
  so floor pickups/inventory items/equipped items are represented
  uniformly and `place_floor_loot` can place them via the same
  `world.create_entity`/`SpatialHash.insert` path as any other spawn. If
  you choose an embedded-record model instead, document the deviation
  clearly in your PR — it changes how `06`'s worldgen and `07`'s renderer
  are expected to query "what items are on this tile."
- **Intermediate rarity budget values** (§5.3): CONTRACTS.md only fixes
  `common=0`/`legendary=14`; the uncommon/rare/epic values here (4/8/12)
  are this component's committed default. Adjust freely if playtesting
  later suggests otherwise — it's a data/config change, not a contract
  change, since nothing else depends on the exact intermediate numbers.
- **Gold as a "loot" concept**: `loot_tables.json`'s `gold_pile_weight`
  assumes gold piles are a distinct spawn type from items (no
  `ItemInstanceComponent`, just a currency pickup). If a `GoldComponent` or
  equivalent is needed, add it here (not in `01`) since it's loot-adjacent,
  and register it in `_COMPONENT_REGISTRY` alongside your other additions.
