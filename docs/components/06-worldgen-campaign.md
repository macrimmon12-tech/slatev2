# Component 06 — World Generation & Campaign

**Wave:** 1 (parallel).
**Depends on:** Wave 0 (`00-foundation-core.md`) + `docs/CONTRACTS.md`.
Hard dependency on `04-inventory-items-loot.md`'s
`place_floor_loot(world, floor_id, depth)` — see §1.1, the single most
important wiring point in this doc. Soft dependency on `01`/`02`'s entity
spawning conventions (you spawn monsters from data; you don't build combat
or AI).

## 1. Scope & Boundaries

You are building procedural floor generation (BSP dungeon layout),
`VaultInjector` (hand-authored room templates stamped into procgen
floors), and `CampaignSystem`/`FloorManager` (ordered level lists, depth
resolution, hub floors, floor transition + snapshot/restore).

### 1.1 THE `place_floor_loot` wiring requirement — read this before anything else

Per `04-inventory-items-loot.md` §1.2: v1's single biggest documented
failure was that a fully-built, fully-tested loot placement function was
never actually called from the map generation pipeline. **This is a hard
requirement on this component, not a suggestion:**

- Your floor-generation pipeline **must call**
  `place_floor_loot(world, floor_id, depth)` (exact signature owned by
  `04-inventory-items-loot.md` §1.2/§2.4) as a real step — after BSP
  generation and vault injection have produced the floor's final tile/room
  layout, and either before or interleaved with entity/monster spawning
  (loot placement and monster spawning must not stomp the same tiles; do
  eligibility checks against both when placing either).
- Your floor-generation pipeline **must also call real entity/monster
  spawning from data** (per each floor/campaign level's monster spawn
  table — see §5.3) — not just generate tiles and rooms. **Floor
  generation is not "done" until it demonstrably calls both loot placement
  and monster spawning, not just tile/room generation.** This directly
  targets the v1 "built but not wired" failure mode (spec §11 lesson 1)
  where the equivalent call was simply missing.
- If `04` genuinely isn't merged when you build this, write against its
  documented signature per CONTRACTS.md §8 (mock the call boundary, not the
  whole module) and call the real thing once it lands — do not skip the
  call site "for now." Your Definition of Done requires the actual call to
  exist in your generation function's source, not a TODO comment.
- Add an integration test (§8) that runs your **real** floor generation and
  asserts (a) `room_id` is populated on every tile, (b) items exist on the
  tile map afterward (via `04`'s real `place_floor_loot`, not a mock), and
  (c) monsters exist as real entities afterward (via real spawning, not a
  mock). This is the CONTRACTS.md §7.3 integration test for this
  component — not a unit test of `_generate_bsp_layout()` in isolation.

### 1.2 The `room_id` hard requirement (spec §11 lesson 4)

v1's room/corridor structure was designed for procgen (which populated
`room_id` per tile automatically) and never retrofitted to handcrafted
maps — AI noise attenuation depends on `room_id`, and every handcrafted
map silently lost that attenuation and fell back to pure spatial radius,
with no warning to the content author. **This is called out explicitly so
the rebuild doesn't repeat it:**

- **Every floor this component generates procedurally MUST populate
  `room_id` on every walkable tile**, with zero exceptions — this is a
  Definition of Done gate (§7), not best-effort.
- **Handcrafted maps** (authored via the future map editor,
  `13-editor-core-authoring.md`) will need a room/corridor authoring path
  to populate the same field, or the downstream systems that depend on it
  (`02`'s AI noise attenuation, `05`'s VisionSystem lit-room lookup) must
  explicitly degrade rather than crash when it's absent — both `02` and
  `05`'s docs already specify graceful degradation on their end. **This is
  flagged here as a cross-cutting concern for `13`'s editor and for
  `15-integration-verification.md`** — v1 never retrofitted this, and it's
  an explicit lesson-learned this rebuild should not repeat by omission.
  You are not responsible for building `13`'s authoring tool; you are
  responsible for (a) making sure your own procgen path is airtight on
  `room_id`, and (b) stating this requirement loudly enough in this doc
  that `13`'s session and `15`'s session both see it.

In scope:
- BSP dungeon generation: the spec does not give full BSP algorithm
  detail, so this doc designs a reasonable standard BSP room+corridor
  generator (§2.1) producing a tile map with `room_id` per tile — commit to
  this shape rather than leaving it to be independently reinvented.
- `VaultInjector`: stamps hand-authored room templates into procgen floors
  post-BSP, pre-entity-spawn. Guaranteed vaults placed first, with
  retry-the-floor (regenerate and try again) if no eligible room exists for
  a guaranteed vault. Weighted vaults fill remaining eligible rooms
  afterward.
- `CampaignSystem`/`FloorManager`: campaign = ordered JSON level list.
  `depth` field drives all depth-aware content lookups (monster/item/affix
  eligibility, difficulty scaling) **independently of the level's sequence
  position** — a handcrafted level placed anywhere in the list at
  `depth: 10` spawns depth-10 content regardless of where it sits in the
  ordering. Hub floors (`is_hub: true`) are tracked separately (excluded
  from normal linear progression assumptions — e.g. never auto-advanced
  past, always revisitable). `FloorManager` snapshots the outgoing floor
  and restores the incoming one via foundation's
  `serialize_floor_snapshot`/`restore_floor_snapshot`, and auto-saves
  (`serialize_world`) after every floor transition.

Out of scope:
- Actual loot resolution/spawning logic — you call `04`'s
  `place_floor_loot`, you don't reimplement it.
- Actual monster AI/combat — you spawn monster entities from data (with
  their `ai`/`stats`/`combat` blocks intact, per `02`/`01`'s schemas), you
  don't drive their behavior.
- Map editor authoring tools — `13-editor-core-authoring.md`.
- NPC/dialog/shop placement logic beyond `VaultInjector` support for
  placing an `InteractableComponent`-bearing entity if a vault template
  names one (spec §5's "Python knows nothing" pattern — you place it, you
  don't interpret it).

## 2. Provides

### 2.1 BSP dungeon generation

```python
# engine/systems/worldgen.py

def generate_floor(level_def: dict, world: World, rng: random.Random) -> str:
    """Top-level entry point: given one campaign level definition (§5.1),
    produces a fully-populated floor and returns its floor_id. Pipeline,
    in order:
      1. _generate_bsp_layout(width, height, rng) -> tilemap with room_id
         populated on every walkable tile (§1.2 — non-negotiable).
      2. VaultInjector.inject(tilemap, level_def.get("vaults", []), rng)
         (§2.2) — guaranteed vaults first (retry whole floor gen if none
         placeable), then weighted vaults into remaining eligible rooms.
      3. Place stairs (down, and up if not the campaign's first floor) in
         valid rooms.
      4. Spawn monsters from level_def["monster_spawns"] (§5.3) as real
         entities with AIComponent/StatsComponent/etc. per 01/02's schemas
         — real spawns, not placeholders. Emits entity_spawned per monster.
      5. place_floor_loot(world, floor_id, level_def["depth"]) — 04's
         function, called for real (§1.1). Do this AFTER monster spawning
         so loot placement can avoid tiles monsters already occupy (query
         SpatialHash before placing), or interleave both against the same
         occupancy check — either ordering is fine as long as they don't
         collide; pick one and be consistent.
      6. Register the floor with FloorManager's floor registry (§2.3).
      Returns floor_id (a stable string derived from level_def["id"]).
    """

def _generate_bsp_layout(width: int, height: int, rng: random.Random,
                          min_room_size: int = 5, max_depth: int = 5) -> "TileMap":
    """Standard recursive BSP: partition the width x height area
    recursively (alternating split axis or random axis choice per node,
    implementation's call) down to max_depth or until a partition is
    smaller than 2x min_room_size, carve one room per leaf partition
    (randomized size/position within the leaf, respecting a margin so
    rooms don't touch partition edges), then connect sibling partitions
    with an L-shaped (one bend) corridor between the center of one room and
    the center of the other, walking back up the tree so every leaf is
    reachable from every other leaf. Every carved room tile AND every
    carved corridor tile gets a room_id: room tiles get their own room's
    id (e.g. "r_00", "r_01", ...); corridor tiles get a distinct "corridor"
    room_id sentinel (e.g. "corridor" or a per-corridor id like "c_00_01"
    naming the two rooms it connects) — the requirement is that
    EVERY walkable tile has a non-null room_id, corridors included, not
    just room interiors (02/05's room-based logic can treat corridor tiles
    as their own zero-attenuation zone; the requirement is presence of the
    field, not a specific semantic for corridors beyond 'not part of any
    room's lit/noise grouping').
    """
```

`TileMap` (owned by this component; a plain data structure, not
necessarily an ECS component — it's per-floor world geometry, referenced
by `SpatialHash`/renderer, not an entity):

```python
@dataclass
class Tile:
    walkable: bool
    room_id: str | None       # REQUIRED non-None on every walkable tile
    sprite_ref: str | None = None
    is_lit_room: bool = False

@dataclass
class TileMap:
    width: int
    height: int
    tiles: dict[tuple[int, int], Tile]
    rooms: dict[str, dict]     # room_id -> {"lit": bool, "tiles": [...], "bounds": ...}
```

### 2.2 `VaultInjector`

```python
# engine/systems/vaults.py

def inject(tilemap: TileMap, vault_refs: list[dict], depth: int,
           rng: random.Random) -> bool:
    """vault_refs: level_def's vault list (§5.1: guaranteed + weighted
    entries). Guaranteed vaults (entries with `guaranteed: true`) are
    placed FIRST, each requiring a room whose dimensions fit the vault
    template (registry namespace `vaults`) and whose depth-eligibility
    (vault's own min_depth/max_depth, if present) includes `depth`. If NO
    eligible room exists for a guaranteed vault, this function returns
    False (signaling the caller — generate_floor — to regenerate the whole
    floor with a new BSP layout and retry; this is the documented
    'retry-the-floor' behavior, not a silent skip). Once all guaranteed
    vaults are placed, weighted vaults (remaining vault_refs, each with a
    `weight`) are rolled to fill some/all of the remaining eligible rooms
    (a per-floor fill-probability config controls how many of the
    remaining rooms get a weighted vault vs. staying plain procgen rooms —
    see data/config/worldgen.json, §5.4). Returns True on success (all
    guaranteed vaults placed; weighted vaults are best-effort, never cause
    a retry).
    Stamping a vault OVERWRITES the target room's tiles with the vault
    template's tile grid (§5.2), but PRESERVES the room's room_id
    (vault tiles inherit the room they're stamped into's room_id — a
    vault's own template does not carry room_id, since the same template
    can be stamped into different rooms across different floors).
    """
```

### 2.3 `CampaignSystem` / `FloorManager`

```python
# engine/systems/campaign.py

class CampaignSystem:
    def __init__(self, world: World, event_bus: EventBus): ...
    # subscribes to stair_use, trigger_level_complete in __init__

    def load_campaign(self, campaign_id: str) -> None:
        """Loads campaign_id from the `campaigns` registry namespace (§5.1
        shape), stores the ordered level list, does NOT generate any floor
        yet — generation happens lazily on first visit via FloorManager."""

    def _on_stair_use(self, payload: dict) -> None:
        """payload: {entity_id, direction}. Determines the target level_def
        from the campaign list based on current position + direction (or,
        for a specific down-stair tied to a portal_target_changed-style
        override, the explicit target if one was set — not required unless
        a vault/NPC sets one). Calls FloorManager.transition_to(level_def).
        """

    def _on_trigger_level_complete(self, payload: dict) -> None:
        """payload: {entity_id}. Marks the current level complete; if the
        campaign list has no further non-hub levels after this one in
        sequence AND this isn't a hub floor, emits campaign_complete
        {campaign_id}; if this was the last campaign in a multi-campaign
        run (out of this component's scope to define further — a single
        campaign is the unit here), a higher-level driver would emit
        game_complete, but this component only emits it when it owns that
        determination (e.g. a campaign flagged as the final one in a meta
        list) — if no such meta-campaign concept exists yet, emit
        campaign_complete only and leave game_complete for the entry point
        (main.py) to decide when to fire, since 06 doesn't own overall game
        structure beyond one campaign."""


class FloorManager:
    def __init__(self, world: World, event_bus: EventBus): ...

    def transition_to(self, level_def: dict) -> None:
        """1. Snapshot the current floor (if any) via foundation's
        serialize_floor_snapshot(world, current_floor_id) and store it in
        the floor registry (in-memory + persisted via save system).
        2. If the target floor (keyed by level_def["id"]) has never been
        generated: call generate_floor(level_def, world, rng) (§2.1) fresh.
        If it HAS been visited before: restore_floor_snapshot(world, stored
        snapshot) instead of regenerating — a visited floor's state
        (remaining monsters, picked-up loot gone, etc.) persists across
        visits, it is not regenerated from scratch on revisit.
        3. Emit floor_changed {from_depth, to_depth}.
        4. Auto-save: call foundation's serialize_world(world) and write to
        the active save slot — after EVERY transition, per spec §2.7/
        CONTRACTS.md's save contract, not just on request.
        Is_hub floors (level_def.get('is_hub')) follow the exact same
        transition/snapshot logic — 'tracked separately' means the campaign
        sequencing logic (§'_on_stair_use') doesn't treat a hub as part of
        the linear progression order (e.g. doesn't auto-advance past it or
        assume there's exactly one path through it), not that it uses a
        different save mechanism.
        """
```

## 3. Consumes

| Event | Used for |
|---|---|
| `stair_use` | `CampaignSystem` resolves the next/target floor and calls `FloorManager.transition_to` |
| `trigger_level_complete` | `CampaignSystem` marks completion, may emit `campaign_complete` |

Calls into (direct):
- `04-inventory-items-loot.md`'s `place_floor_loot(world, floor_id, depth)`
  — **required**, see §1.1.
- foundation's `serialize_floor_snapshot` / `restore_floor_snapshot` /
  `serialize_world`.

Emits:
- `entity_spawned` `{entity_id, kind, position}` — for every monster spawned
  during floor generation.
- `floor_changed` `{from_depth, to_depth}`
- `campaign_complete` `{campaign_id}`
- `game_complete` — only if this component owns that determination (see
  `_on_trigger_level_complete` note above); otherwise left to `main.py`.

## 4. File/Directory Ownership

You own, exclusively:
- `engine/systems/worldgen.py`, `vaults.py`, `campaign.py`
- `tests/unit/test_worldgen.py`, `test_vaults.py`, `test_campaign.py`
- `tests/fixtures/campaigns/`, `tests/fixtures/vaults/`
- `data/campaigns/` (at least one real fixture campaign exercising
  multiple levels + a hub, per Definition of Done)
- `data/maps/vaults/` (at least one guaranteed and one weighted vault
  fixture)
- `data/config/worldgen.json` (BSP tuning + vault fill-probability, §5.4)

You do not own, but must call correctly:
- `04-inventory-items-loot.md`'s `place_floor_loot` (§1.1).

## 5. Data Schemas

### 5.1 Campaign JSON schema (spec §4/Appendix B don't give a full example —
committing to this shape)

```json
{
  "id": "the_sunken_keep",
  "display_name": "The Sunken Keep",
  "levels": [
    {
      "id": "keep_l1",
      "depth": 1,
      "is_hub": false,
      "width": 60,
      "height": 40,
      "monster_spawns": [
        {"entity_id": "goblin", "count": [4, 7]},
        {"entity_id": "goblin_shaman", "count": [0, 1]}
      ],
      "vaults": [
        {"vault_id": "goblin_shrine", "guaranteed": true},
        {"vault_id": "small_treasure_room", "weight": 5}
      ]
    },
    {
      "id": "keep_hub",
      "depth": 1,
      "is_hub": true,
      "width": 30,
      "height": 20,
      "monster_spawns": [],
      "vaults": []
    },
    {
      "id": "keep_l10_vault_special",
      "depth": 10,
      "is_hub": false,
      "width": 50,
      "height": 50,
      "monster_spawns": [{"entity_id": "keep_boss", "count": [1, 1]}],
      "vaults": [{"vault_id": "boss_arena", "guaranteed": true}]
    }
  ]
}
```

Field notes:
- `levels` order is authoring/sequence order (what "next floor" means for
  a plain down-staircase) — but `depth` is looked up independently for
  every depth-aware content query (item/monster eligibility windows,
  difficulty multipliers), so `keep_l10_vault_special` being 3rd in the
  list but `depth: 10` spawns real depth-10 content correctly regardless
  of its list position (per §1's requirement).
- `is_hub: true` — excluded from "auto-advance to next level" assumptions;
  a hub is a place players return to, not a rung on a ladder.
- `monster_spawns` — `count` is a `[min, max]` inclusive range rolled once
  per floor generation, per entry.
- `vaults` — list of `{"vault_id": ..., "guaranteed": bool}` or
  `{"vault_id": ..., "weight": int}` entries (guaranteed entries never
  carry `weight`; weighted entries never carry `guaranteed`).

### 5.2 Vault template schema (spec §4/Appendix B don't give a full example)

```json
{
  "id": "goblin_shrine",
  "display_name": "Goblin Shrine",
  "min_depth": 1,
  "max_depth": 6,
  "width": 9,
  "height": 7,
  "tiles": [
    "#########",
    "#.......#",
    "#..###..#",
    "#..#.#..#",
    "#..###..#",
    "#.......#",
    "#########"
  ],
  "entity_spawns": [
    {"entity_id": "goblin_shaman", "x": 4, "y": 3}
  ],
  "loot_spawns": [
    {"item_id": "health_potion", "x": 2, "y": 1}
  ]
}
```

- `tiles` — a row-major ASCII grid, `#` = wall (not walkable), `.` =
  floor (walkable); the vault system maps these characters to `Tile`
  instances when stamping, inheriting the target room's `room_id`.
  `width`/`height` must match the grid dimensions exactly (validated at
  load, load-time error on mismatch).
- `entity_spawns`/`loot_spawns` — coordinates relative to the vault's own
  top-left corner (0,0), translated to absolute floor coordinates at
  stamp time based on where the target room places the vault's origin.
- A vault with no `min_depth`/`max_depth` is eligible at any depth.

### 5.3 `monster_spawns` — see §5.1 (inline in campaign level def, not a
separate file).

### 5.4 `data/config/worldgen.json` (you own this file)

```json
{
  "schema_version": 1,
  "bsp": {"min_room_size": 5, "max_split_depth": 5, "room_margin": 1},
  "vault_fill_probability": 0.4,
  "corridor_room_id_prefix": "corridor"
}
```

`vault_fill_probability` — the chance any given remaining eligible room
(after guaranteed vaults are placed) receives a weighted vault instead of
staying a plain procgen room; rolled independently per eligible room.

## 6. Lessons from v1 applied here

- **"Built but not wired" is this component's single biggest risk, twice
  over** — once for loot placement (§1.1, `04`'s exact v1 failure) and
  once implicitly for monster spawning (§1.1's second bullet — floor
  generation producing tiles but no real entities would be the same
  failure shape applied to monsters instead of items). Both are hard
  Definition of Done gates here, verified by a real integration test, not
  narrative reassurance.
- **`room_id` never retrofitted to handcrafted maps (spec §11 lesson 4)**
  — your procgen path must be airtight on this (§1.2); flag it loudly for
  `13`'s editor and `15`'s integration verification rather than assuming
  someone will remember.
- **Difficulty/depth decoupled from sequence position** — a deliberate v1
  design (spec §3's CampaignSystem bullet) worth preserving exactly: never
  let any code path infer depth from a level's index in the list. Always
  read `level_def["depth"]` explicitly.
- **Save-on-every-transition** (spec §2.7) — don't make this
  request/on-demand; it's unconditional after every `transition_to` call,
  matching v1's proven pattern.

## 7. Definition of Done

- [ ] `_generate_bsp_layout` produces a fully-connected tile map (every
      room reachable from every other room) with `room_id` populated on
      **every walkable tile including corridors**, zero exceptions — unit
      tested by asserting no walkable tile in a generated map has
      `room_id is None`.
- [ ] `VaultInjector.inject` places guaranteed vaults first, returns
      `False` (triggering floor regeneration) when no eligible room exists
      for a guaranteed vault — unit tested with a vault template larger
      than any room a tiny fixture BSP layout could produce.
- [ ] Weighted vaults fill remaining eligible rooms probabilistically per
      `vault_fill_probability`, never causing a retry on failure to
      place — unit tested.
- [ ] Vault stamping preserves the target room's `room_id` on every
      stamped tile — unit tested.
- [ ] `CampaignSystem`/`FloorManager` correctly resolves `depth` from
      `level_def["depth"]` independent of list position — unit tested with
      a fixture campaign where a later-in-sequence level has a lower
      `depth` than an earlier one, asserting depth-dependent behavior
      (e.g. a monster spawn eligibility check) uses the field, not the
      index.
- [ ] Hub floors are excluded from linear-advance assumptions — unit
      tested.
- [ ] `FloorManager.transition_to` snapshots the outgoing floor, restores
      (not regenerates) a previously-visited floor, generates fresh only on
      first visit, and auto-saves after every transition — unit tested.
- [ ] **`generate_floor` actually calls `place_floor_loot(world, floor_id,
      depth)`** — verified by a test that inspects the call happened (a
      spy/mock on `04`'s function counting invocations) AND by the
      integration test below proving real items land on the map.
- [ ] **`generate_floor` actually spawns real monster entities from
      `monster_spawns` data** — verified by the integration test below
      finding real entities with `AIComponent`/`StatsComponent` on the
      generated floor, not zero entities.
- [ ] `pytest` green.

## 8. Test Plan

Unit tests (`tests/unit/test_worldgen.py`, `test_vaults.py`,
`test_campaign.py`): see each Definition of Done bullet above.

Integration test (`tests/integration/test_floor_generation_pipeline.py` —
required per CONTRACTS.md §7.3, and explicitly the test `04`'s doc points
back to):
- Build a real `World` + event bus + a fixture campaign level definition
  (a small width/height, a couple of monster spawn entries, one guaranteed
  vault fixture).
- Call `generate_floor(level_def, world, rng)` directly — the real
  top-level entry point, not a mock of its internals.
- Assert:
  1. Every walkable tile in the resulting `TileMap` has a non-`None`
     `room_id` (the §1.2 hard requirement, checked programmatically over
     the whole map, not spot-checked).
  2. The guaranteed vault's tiles actually appear in the generated map at
     some room's location (grid pattern match).
  3. Real monster entities exist in `world` afterward matching the
     `monster_spawns` table's entity IDs and roughly the right counts
     (within the `[min, max]` ranges) — found via `world.query` for
     `AIComponent`, not via a spawn-call spy alone.
  4. Real item entities exist on the tile map afterward via `04`'s real
     `place_floor_loot` — found via `world.query` for
     `ItemInstanceComponent` (or whatever `04` settled on — see
     `04-inventory-items-loot.md` §9 for its component representation
     choice) at positions on walkable tiles. **This is the exact
     assertion `04-inventory-items-loot.md` §1.2/§8 calls for from "the
     other side" — this test is what closes that loop for real, not just
     in isolation on each side.**
- A second integration case for `FloorManager`: transition from floor A to
  floor B and back to A, and assert A's state (e.g. a monster that was
  killed before leaving) persisted across the round trip via the real
  snapshot/restore call path (not regenerated fresh the second time) —
  driven through real `stair_use` events, not direct internal method
  calls, to also prove `CampaignSystem`'s event subscription is correctly
  wired to `FloorManager`.

## 9. Open Questions & Defaults

- **Multi-campaign / overall game structure beyond one campaign list**
  (when does `game_complete` fire): default is that this component only
  emits `campaign_complete`; `game_complete` is left to `main.py`/a future
  meta-campaign concept to decide, since a single `CampaignSystem` instance
  here is scoped to one campaign at a time. If a real multi-campaign
  "game" concept is needed, that's a CONTRACTS.md-level addition to
  resolve later, not assumed here.
- **BSP split axis / room sizing randomization specifics** — the spec
  gives no algorithm detail; §2.1's description (recursive partition,
  randomized leaf room size/position within margin, L-shaped
  sibling-connecting corridors) is this doc's committed default. Any
  internally-consistent variation (e.g. always-alternate axis vs.
  random-axis-per-node) is acceptable as long as full connectivity and
  100% `room_id` coverage hold — those two properties are the hard
  requirements, not the exact partition heuristic.
- **Corridor `room_id` semantics for downstream noise/lighting** — default:
  corridors get their own non-null `room_id` (e.g. `"corridor"` sentinel or
  per-corridor ids) rather than inheriting an adjacent room's id, so `02`'s
  noise attenuation and `05`'s lit-room lookup can treat corridors as
  "never lit, never room-wide-noise-shared" uniformly. If a design need
  arises later for corridor-specific lighting/noise rules, that's additive
  data on the corridor's room entry, not a schema break.
