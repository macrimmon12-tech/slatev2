# Component 07 — Input, Renderer, Audio

**Wave:** 1 (parallel).
**Depends on:** Wave 0 (`00-foundation-core.md`) only. Build against
`CONTRACTS.md` event/schema shapes directly — do not import other Wave 1
components' code.

## 1. Scope & Boundaries

You own the three systems that sit between the player and the simulation:
input translation, tile/HUD rendering, and sound playback. Nothing here
decides game *rules* — you translate keys into action-level events, draw
whatever state the ECS currently holds, and play whatever sound an event
says to play. If you find yourself deciding hit chance, spell legality, or
inventory contents, that belongs to another component; stop and emit/consume
an event instead.

In scope:
- `InputHandler` (`engine/input/input_handler.py`): loads
  `data/config/controls.json` at startup, translates raw pygame key events
  into logical actions scoped by context (`game`, `targeting`, `ui`), emits
  action-level events.
- `Renderer` (`engine/render/renderer.py`): pygame-ce window, resizable,
  80×50 tile viewport + 240px HUD panel, camera follow, sprite lookup with
  colored-rect fallback.
- `Autotile` (`engine/render/autotile.py`): 4-bit cardinal bitmask
  computation for wall tiles post-generation, and the 16-variant sprite
  lookup contract (see §5.1 — this is a hard contract other components,
  notably `13-editor-core-authoring.md`, build against).
- `AudioSystem` (`engine/audio/audio_system.py`): `pygame.mixer` wrapper,
  data-driven event→sound mapping via `data/config/audio_config.json`,
  per-payload `"sound"` override, missing-file caching.
- Schemas for `controls.json` and `audio_config.json` (§5).

Out of scope:
- Actual visual effect rendering (particles, projectiles, flashes) — that's
  `09-animation-vfx.md`. You only care about `vfx_play`/combat events insofar
  as they might carry a sound to play; you never draw a VFX shape.
- Panel/widget rendering (inventory, HUD text layout beyond the raw HUD
  frame) — that's `08-ui-runtime.md`. Your renderer draws the tile viewport
  and hands the HUD panel rect to UIRuntime to draw into; you do not parse
  widget-tree JSON.
- Deciding whether a cast/attack/item-use is *legal* — you only emit the
  request-level event; the owning system (spells/inventory/combat) decides
  legality and no-ops or refuses silently per CONTRACTS.md §2.7.
- Sprite/theme *authoring* tooling — that's `13-editor-core-authoring.md`'s
  Sprite Manager. You consume the theme JSON; you don't provide a GUI to
  edit it.

## 2. Provides

### 2.1 InputHandler

```python
class InputHandler:
    def __init__(self, event_bus, controls_config: dict, context: str = "game"): ...
    def set_context(self, context: str) -> None: ...  # "game" | "targeting" | "ui"
    def handle_pygame_event(self, pygame_event) -> None: ...
    def rebind(self, action: str, context: str, new_key: str) -> None: ...
```

- Reads `registry.get("configs", "controls")` (i.e. `data/config/controls.json`,
  keyed by filename stem per CONTRACTS.md §4) at startup. Never hardcodes a
  key anywhere in the module — every keycode reachable only through the
  loaded config, including defaults; if the config is missing, log once
  (per CONTRACTS.md §9's "log once" convention) and fall back to a built-in
  default dict baked as a Python constant at the top of the module, not
  scattered through the handler logic.
- A context is a named scope (`game`, `targeting`, `ui`). The same physical
  key can map to different logical actions per context (e.g. `Escape` means
  "open menu" in `game` context, "cancel_targeting" in `targeting` context).
  Exactly one context is active at a time; `set_context` swaps it (called by
  `08-ui-runtime.md`'s targeting-overlay lifecycle and by menu/panel state).
- Multiple physical keys may map to the same action natively — the config
  schema (§5.2) stores a *list* of keys per action, never a single key
  string, so "arrows + WASD + numpad all bound to move_north simultaneously"
  requires zero special-casing in code.
- Action → event translation table (own this mapping in code, data only
  supplies which physical key triggers which named action):

| Action (from controls.json) | Context | Event emitted | Payload |
|---|---|---|---|
| `move_north/south/east/west/ne/nw/se/sw` | `game` | `player_moved`-triggering movement request — see Open Questions §9 for exact internal call vs. event | — |
| `wait` | `game` | (movement system's turn-pass path) | — |
| `interact` | `game` | `entity_interacted` | `actor_id, target_id` (resolved via adjacent-entity lookup through `SpatialHash`) |
| `use_stairs` | `game` | `stair_use` | `entity_id, direction` |
| `cast_spell_<slot>` / `open_spellbook` | `game` | `spell_cast_initiated` (only when a spell is already selected/hotbarred — the actual cast legality and targeting-mode setup is `03-spells-status.md`'s `SpellSystem`; you emit the *request*, e.g. via a preceding `item_used`-style request event if that's how 03 documents intake — see Open Questions) | `entity_id, spell_id` |
| `use_item_<slot>` | `game` | `item_used`-request path (per `04-inventory-items-loot.md`'s intake contract) | `entity_id, item_instance_id` |
| `confirm_target` | `targeting` | `target_confirmed` | `target` |
| `cancel_targeting` | `targeting`, `game` | `cancel_targeting` | `entity_id` |
| `move_cursor_*` | `targeting` | internal cursor move (no event; renderer-local cursor state) | — |
| `menu`, `inventory`, `spellbook`, `journal`, `minimap` | `ui`/`game` | `show_panel` | `panel_id, data` |
| `quit_to_menu` | `ui` | `quit_selected` | — |

  This table is the split contract with `08-ui-runtime.md` and
  `03-spells-status.md`: **input owns raw key→action translation and emits
  action-level events**; UI runtime owns rendering panels *in response* to
  those events (including the targeting overlay teardown), never routing
  raw keys itself once a mode is active. If `03-spells-status.md`'s doc
  defines a different literal event name for "cast key pressed," treat that
  as the source of truth for the exact string and update this table's
  wording in your PR — the split responsibility (input translates keys,
  never decides game legality) is the part that's binding.
- Movement: emits through whatever the foundation/movement path is (see
  `00-foundation-core.md` §2.1 — movement itself is arguably a Wave 1
  concern of `01-stats-combat.md`/AI shared code; if no owning component
  documents a `request_move` event, InputHandler calls a documented
  movement entry point directly for the player entity only, since player
  input is inherently a direct-call boundary, not cross-system event
  fan-out — note this choice in your PR if `01-stats-combat.md` doesn't
  already claim ownership of a player-move function).

### 2.2 Renderer

```python
class Renderer:
    def __init__(self, world, registry, event_bus, window_size=(1280, 800)): ...
    def resize(self, new_size: tuple[int, int]) -> None: ...
    def draw_frame(self) -> None: ...  # tile viewport + hands HUD rect to UIRuntime
    def get_hud_rect(self) -> pygame.Rect: ...
    def get_viewport_rect(self) -> pygame.Rect: ...
    def world_to_screen(self, tile_pos: tuple[int, int]) -> tuple[int, int]: ...
```

- Window is resizable (`pygame.RESIZABLE`), never fullscreen. On every
  resize event, recompute tile size: `tile_size = min(viewport_w // 80,
  viewport_h // 50)` where `viewport_w = window_w - 240` (HUD panel is a
  fixed 240px-wide column, letterboxed against the tile viewport — the tile
  viewport is centered/letterboxed within its remaining area to keep square
  tiles at every window size, not stretched).
- Sprite lookup: `sprite_ref` field on entity/tile data → `assets/` path.
  Missing file → solid color from `data/config/palette` (a `configs`-
  namespace file), keyed by content type or damage/faction hint if the
  palette schema documents one; log the miss exactly once per distinct
  missing path using the module-level `set()` pattern from CONTRACTS.md §9
  — never re-stat or re-warn on the same missing path twice.
- Camera follows the player entity: subscribes to `entity_moved` and
  `player_moved`, recenters viewport origin on the player's `PositionComponent`
  each frame (or on receipt of the event — either is acceptable, document
  your choice).
- `floor_changed` triggers a full map reload: drop cached tile surfaces for
  the previous floor, rebuild the autotile bitmask for the new floor's
  tilemap.

### 2.3 Autotile — the 16-variant wall contract

This is the single most important contract this component publishes for
downstream consumers (`13-editor-core-authoring.md`'s Sprite Manager
specifically). Document it explicitly, don't leave it implicit — v1's
exact failure (spec §11 lesson 3) was building this renderer capability
while the content-authoring surface exposed only one generic `"wall"` slot,
so 15 of 16 variants silently never got used for a long stretch.

```python
def compute_wall_bitmask(tilemap, x: int, y: int) -> int: ...  # 0-15
def wall_sprite_key(bitmask: int) -> str: ...  # "wall_0".."wall_15"
def is_diagonal_corner(bitmask: int) -> bool: ...  # True for 3, 6, 9, 12
def draw_diagonal_corner_fallback(surface, rect, bitmask: int, color) -> None: ...
```

- Bitmask bit order (fix this exactly, document it verbatim so
  `13-editor-core-authoring.md`'s Sprite Manager preview matches):
  `bit0=North wall-neighbor present, bit1=East, bit2=South, bit3=West`.
  `bitmask = N | (E<<1) | (S<<2) | (W<<3)`, computed post-generation once
  per floor load (and incrementally if a tile changes at runtime — rare,
  but don't assume immutable).
- **The 16 named slots are the contract**: `wall_0` through `wall_15`,
  keyed by that exact bitmask integer, one PNG each in the active theme's
  sprite set. The theme/sprite-lookup JSON (owned by
  `13-editor-core-authoring.md`'s Sprite Manager as an authoring surface,
  but the *lookup contract* — i.e. what key names must exist for the
  renderer to find them — is defined here) must expose all 16 named slots
  as distinct, individually assignable entries — never a single generic
  `"wall"` key that silently aliases to one sprite for every bitmask value.
  Concretely, wherever theme/sprite-ref JSON stores a wall tile's sprite,
  it must resolve through a map shaped like:
  ```json
  {
    "wall_variants": {
      "wall_0": "tiles/wall_0.png", "wall_1": "tiles/wall_1.png",
      "wall_2": "tiles/wall_2.png", "wall_3": "tiles/wall_3.png",
      "wall_4": "tiles/wall_4.png", "wall_5": "tiles/wall_5.png",
      "wall_6": "tiles/wall_6.png", "wall_7": "tiles/wall_7.png",
      "wall_8": "tiles/wall_8.png", "wall_9": "tiles/wall_9.png",
      "wall_10": "tiles/wall_10.png", "wall_11": "tiles/wall_11.png",
      "wall_12": "tiles/wall_12.png", "wall_13": "tiles/wall_13.png",
      "wall_14": "tiles/wall_14.png", "wall_15": "tiles/wall_15.png"
    }
  }
  ```
  Any theme JSON missing a `wall_N` key falls back to the colored-rect
  fallback for that specific variant only (never collapses every variant to
  one sprite) — this preserves "absence = zero cost" per-variant instead of
  per-tile-type.
- Diagonal-corridor inner-corner variants (bitmask **3, 6, 9, 12** — the
  two-adjacent-cardinal-neighbors-at-a-right-angle cases) get a **hardcoded
  right-triangle polygon fallback** (`draw_diagonal_corner_fallback`) drawn
  in the palette wall color, oriented per bitmask, so diagonal corridors
  render legibly with **zero sprite assets present** — this fallback exists
  independently of the theme JSON and fires whenever `wall_3`/`wall_6`/
  `wall_9`/`wall_12` resolve to "missing," not just when no theme exists at
  all.
- **Per-tile `sprite_ref` override always wins** over the autotile lookup:
  if a tile's own data carries an explicit `sprite_ref`, the renderer uses
  it directly and skips bitmask computation entirely for that tile.

### 2.4 AudioSystem

```python
class AudioSystem:
    def __init__(self, event_bus, registry): ...
    def play(self, sound_id: str, position: tuple[int, int] | None = None) -> None: ...
```

- Loads `data/config/audio_config.json` (`configs` namespace, keyed by
  filename stem `audio_config`) at startup: a pure event-name → sound-id
  mapping, no hardcoded pairing in Python.
- Subscribes to every event named as a key in `audio_config.json`'s
  `event_sounds` map, plus always subscribes unconditionally to
  `play_sound` (the universal any-system override channel per CONTRACTS.md
  §3.2).
- **Per-event override:** on any subscribed event, if the payload dict
  carries a `"sound"` key, play that sound_id/path instead of the
  config-mapped default — zero config edits needed to give one goblin a
  different death sound (`{"sound": "sfx/goblin_death_2.ogg"}` on that
  entity's `damage_dealt`/`death` payload). This works because you check
  `payload.get("sound")` before falling back to the static config lookup on
  every dispatch, not because of any per-entity registration.
- Missing audio file: `pygame.mixer.Sound(path)` failure (or a pre-flight
  `Path.exists()` check) results are cached in a module-level `dict[str,
  bool]` keyed by resolved path — first miss logs once (per CONTRACTS.md
  §9) and caches `False`; every subsequent play request for that same path
  checks the cache and returns immediately with **no disk stat at all**,
  forever, for the life of the process.

## 3. Consumes

| Event | Why |
|---|---|
| `damage_dealt` | audio: play hit/damage-type sound if mapped; also feeds renderer nothing (VFX is 09's job) |
| `miss` | audio: play whiff sound if mapped |
| `status_applied` / `status_expired` | audio: play status sound if mapped |
| `vfx_play` | audio only, and only for the sound-relevant portion — if `audio_config.json` maps a sound to this event (or the payload carries `"sound"`), play it; the `data` payload's visual parameters (color, scale, position detail) are **09-animation-vfx.md's concern exclusively** — this component never inspects `vfx_play`'s visual fields |
| `play_sound` | universal audio override channel, always subscribed |
| `entity_moved` / `player_moved` | renderer camera follow |
| `floor_changed` | renderer map/tile-cache reload |

Boundary note: this component and `09-animation-vfx.md` both subscribe to
the same five combat/status events. That's intentional and requires no
coordination — each subscriber only reads the fields it cares about
(audio reads none of the positional/visual fields; it only checks for a
config mapping or a `"sound"` override key) and the event bus dispatches to
both independently per CONTRACTS.md §3.

## 4. Emits

| Event | When |
|---|---|
| `entity_interacted` | interact key pressed on an adjacent entity |
| `stair_use` | use-stairs key pressed |
| `spell_cast_initiated`-adjacent request / `cancel_targeting` / `target_confirmed` | per §2.1's action table, when input handles targeting-mode key routing directly (mouse/key click in `targeting` context resolves to a tile/entity and emits `target_confirmed`; the cancel key in `targeting` context emits `cancel_targeting`) |
| `show_panel` | menu/inventory/journal/minimap key pressed (mechanism only — panel content is `08-ui-runtime.md`'s job) |
| `quit_selected` | quit-to-menu key pressed in `ui` context |

## 5. Data Schemas

### 5.1 `data/config/controls.json`

```json
{
  "schema_version": 1,
  "contexts": {
    "game": {
      "move_north": ["Up", "w", "KP8"],
      "move_south": ["Down", "s", "KP2"],
      "move_east":  ["Right", "d", "KP6"],
      "move_west":  ["Left", "a", "KP4"],
      "move_ne": ["KP9"], "move_nw": ["KP7"],
      "move_se": ["KP3"], "move_sw": ["KP1"],
      "wait": ["KP5", "z"],
      "interact": ["e", "Return"],
      "use_stairs": ["Greater", "Less"],
      "cast_spell_1": ["1"], "cast_spell_2": ["2"],
      "use_item_1": ["q"],
      "inventory": ["i"], "spellbook": ["p"],
      "journal": ["j"], "minimap": ["m"],
      "menu": ["Escape"]
    },
    "targeting": {
      "move_cursor_north": ["Up", "w"], "move_cursor_south": ["Down", "s"],
      "move_cursor_east": ["Right", "d"], "move_cursor_west": ["Left", "a"],
      "confirm_target": ["Return", "e"],
      "cancel_targeting": ["Escape"]
    },
    "ui": {
      "confirm": ["Return"], "cancel": ["Escape"],
      "nav_up": ["Up", "w"], "nav_down": ["Down", "s"],
      "quit_to_menu": ["Escape"]
    }
  }
}
```

Each action's value is always a **list** of key names (never a bare
string), even for a single binding — this keeps rebinding code uniform and
makes "multiple keys, one action" the default shape, not a special case.
Key names are pygame key-name strings (`pygame.key.key_code`-compatible).

### 5.2 `data/config/audio_config.json`

```json
{
  "schema_version": 1,
  "event_sounds": {
    "damage_dealt": {
      "default": "sfx/hit_generic.ogg",
      "by_damage_type": {
        "fire": "sfx/hit_fire.ogg",
        "physical": "sfx/hit_physical.ogg"
      }
    },
    "miss": { "default": "sfx/whiff.ogg" },
    "status_applied": {
      "default": "sfx/status_generic.ogg",
      "by_effect_id": { "poisoned": "sfx/status_poison.ogg" }
    },
    "status_expired": { "default": "sfx/status_end.ogg" },
    "entity_died": { "default": "sfx/death_generic.ogg" }
  },
  "music": {
    "main_menu": "music/menu_theme.ogg",
    "default_floor": "music/dungeon_loop.ogg"
  }
}
```

`by_damage_type`/`by_effect_id` are optional secondary lookup keys read
from the triggering payload's own field (`damage_type`, `effect_id`) when
present, falling back to `default` when absent or unmapped. This is config
structure, not a new event field — it reads fields the payload already
carries per CONTRACTS.md §3.2. A `"sound"` key directly on the payload (see
§2.4) always wins over both of these.

## 6. File/Directory Ownership

You own, exclusively:
- `engine/input/input_handler.py`
- `engine/render/renderer.py`
- `engine/render/autotile.py`
- `engine/audio/audio_system.py`
- `data/config/controls.json`, `data/config/audio_config.json`
- `engine/core/schemas/controls_schema.py` (or equivalent validator file —
  match whatever convention `00-foundation-core.md` established for schema
  files under `engine/core/schemas/`)
- `tests/unit/test_input_handler.py`, `test_renderer.py`, `test_autotile.py`,
  `test_audio_system.py`
- `tests/fixtures/controls_minimal.json`, `tests/fixtures/audio_config_minimal.json`

## 7. Lessons from v1 applied here

- **The 16-variant wall autotile gap (spec §11 lesson 3) is the direct
  reason §2.3 exists as written.** v1 built the renderer capability
  correctly the first time; the failure was purely on the content-authoring
  side never exposing the other 15 slots. This doc's Definition of Done
  requires the `wall_0`..`wall_15` lookup contract be written down
  explicitly (not left as "the renderer will figure it out") specifically
  so `13-editor-core-authoring.md`'s Sprite Manager builds its UI against
  the same 16 named slots from day one instead of shipping a single
  generic slot and rediscovering the gap later.
- **Absence = zero cost** applies identically to sprites and sounds: a
  missing sprite never raises, always falls back to a palette color, logs
  once; a missing sound never raises, is cached as permanently-missing
  after the first check, never re-stats the filesystem. Both are
  first-class behaviors here, not error paths to clean up later.
- **v1's hotbar active-slot highlighting was a hardcoded placeholder** — a
  known gap (spec §11.6). Not this component's job to fix (that's HUD
  content, `08-ui-runtime.md`'s domain), but noted here so this component
  doesn't accidentally reintroduce a hardcoded highlight state inside the
  renderer's HUD-rect handoff.

## 8. Definition of Done

- [ ] `InputHandler` loads `controls.json`, supports rebind, supports
      multiple keys per action, and correctly scopes the same physical key
      to different actions across `game`/`targeting`/`ui` contexts, with
      unit tests for all three.
- [ ] `Renderer` resizes correctly (tile size recalculated, HUD panel stays
      240px, viewport letterboxed) with a test driving multiple window
      sizes and asserting tile size and viewport rect math.
- [ ] Missing-sprite fallback draws a palette color and logs exactly once
      across repeated frames referencing the same missing sprite (test
      asserts log call count == 1 across N draw calls).
- [ ] `autotile.compute_wall_bitmask` produces correct bitmasks for all 16
      neighbor configurations, `wall_sprite_key` returns `wall_0`..`wall_15`
      correctly, and the four diagonal-corner variants (3, 6, 9, 12) render
      via the polygon fallback with zero sprite assets present (test
      renders to an offscreen surface and asserts non-background pixels
      exist in the expected triangle region).
- [ ] The `wall_variants` 16-slot lookup contract (§5.1... §2.3) is written
      down verbatim in this doc (done) and referenced by name in
      `13-editor-core-authoring.md` — cross-check this at merge time if
      both docs land close together.
- [ ] Per-tile `sprite_ref` override is proven to bypass autotile lookup
      with a test.
- [ ] `AudioSystem` loads `audio_config.json`, subscribes to every event
      named in it plus `play_sound`, resolves `by_damage_type`/`by_effect_id`
      sub-lookups, and honors a payload `"sound"` override over all of them,
      each with a unit test.
- [ ] Missing audio file is cached after first miss; test asserts the
      underlying file-check function is called exactly once across N play
      requests for the same missing path.
- [ ] Camera follow reacts to `entity_moved`/`player_moved`; `floor_changed`
      clears the tile cache — both with tests.
- [ ] `pytest` green, including one real integration test (§9 Test Plan).

## 9. Test Plan

Unit tests per §8. Integration test (per CONTRACTS.md §7.3 — must drive the
real entry point, not a mock):

- `tests/integration/test_input_render_audio_pipeline.py`: build a real
  `World` + event bus + a fixture floor tilemap with a mix of wall/floor
  tiles (including at least one diagonal-corridor corner configuration);
  construct the real `Renderer` and `AudioSystem` against fixture
  `controls.json`/`audio_config.json`; drive a synthetic pygame key-down
  event for `move_east` through the real `InputHandler` and assert the
  resulting `player_moved`/movement call actually changes the entity's
  `SpatialHash` position; emit a real `damage_dealt` event on the bus and
  assert `AudioSystem` attempts to play the config-mapped sound (mock only
  `pygame.mixer.Sound.play` at the boundary, not the event dispatch); call
  `Renderer.draw_frame()` against the fixture floor and assert the autotile
  bitmask lookup selects `wall_3`/`wall_6`/`wall_9`/`wall_12` fallback
  rendering for the corridor-corner tiles in the fixture.

## 10. Open Questions & Defaults

- **Exact player-movement call boundary** (event vs. direct call): default
  to a direct call into a documented movement entry point for the player
  entity specifically (input is inherently privileged for the single local
  player), while all monster/AI movement stays event/system-internal. If
  `01-stats-combat.md` or `00-foundation-core.md` already documents a
  `request_move`-style event or function, prefer that and note the
  resolution in your PR instead of inventing a parallel path.
- **Exact cast-key intake event name**: this doc emits
  `spell_cast_initiated`-adjacent requests per CONTRACTS.md's table, which
  lists `spell_cast_initiated` as emitted *by SpellSystem*, not input. If
  `03-spells-status.md` documents a distinct "cast key pressed" request
  event name (e.g. `spell_cast_requested`) that SpellSystem consumes and
  turns into `spell_cast_initiated` itself, use that name here instead —
  default assumption absent such a doc: input emits `spell_cast_initiated`
  directly with `targeting_mode` omitted/unresolved and SpellSystem fills
  in targeting_mode from the spell definition, since CONTRACTS.md's table
  doesn't currently list a separate request event. Flag this in your PR;
  it may need a CONTRACTS.md addition.
- **Palette config file name/shape**: assumed `data/config/palette.json`
  keyed by content-type or a flat fallback-color list; if no such file is
  specified elsewhere, define a minimal schema here and document it in your
  PR as a new `configs`-namespace file this component introduces.
