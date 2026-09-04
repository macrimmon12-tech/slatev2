# Component 01 — Stats & Combat

**Wave:** 1 (parallel).
**Depends on:** Wave 0 (`00-foundation-core.md`) + `docs/CONTRACTS.md`.

## 1. Scope & Boundaries

You are building the three systems every other gameplay component ultimately
routes numbers through: `StatsSystem` (the single read path for "what is this
entity's current X"), `EffectResolver` (the single pipeline every spell,
potion, trap, equipment proc, and status tick resolves through), and
`CombatSystem` (melee/ranged hit resolution and death handling).

**This is the most heavily-depended-on Wave 1 doc.** AISystem (`02`) calls
into your `CombatSystem.resolve_hit`. SpellSystem/StatusEffectsSystem (`03`)
and InventorySystem (`04`) call into your `EffectResolver.apply_effect`.
Everything that ever needs a derived stat calls your `StatsSystem.get_stat`.
Treat §2's function signatures as load-bearing — another session is writing
code against them right now without being able to ask you anything.

In scope:
- `StatsSystem` — derived stat resolution via `StatsComponent.modifiers`,
  "Option C" math, modifier lifecycle (apply/expire by tag).
- `EffectResolver` — the ~14-effect-type dispatch table, formula evaluation
  glue (via foundation's `engine/core/formula.py`), unknown-type skip.
- `CombatSystem` — `resolve_hit`, hit-chance formula, depth-aware difficulty
  multipliers, damage roll, death handling, `process_monster_turns()`.

Out of scope (leave as a documented boundary, not your job):
- Actually ticking status effect durations — that's `03-spells-status.md`'s
  `StatusEffectsSystem`. Your `apply_status` effect type calls into its
  application entry point (a function signature it owns and documents) and
  stops there; you do not track durations or re-implement ticking.
- AI decision-making — `02-ai-system.md`. You only provide the attack entry
  point AI calls into.
- Item/equipment logic (equip/unequip, affixes) — `04-inventory-items-loot.md`.
  You only *consume* `StatsComponent.modifiers` however it got populated;
  you do not own equip flow.
- Spell casting state machine — `03-spells-status.md`. You provide the
  effect-resolution pipeline spells resolve through; you do not own
  targeting, MP cost, or fail chance.

## 2. Provides (public API surface — other Wave 1 components code against this)

### 2.1 `StatsSystem`

```python
# engine/systems/stats.py

class StatsSystem:
    def __init__(self, world: World, event_bus: EventBus): ...

    def get_stat(self, entity_id: int, stat_name: str, world: World) -> float:
        """The single read path for every derived stat in the codebase.
        result = (base + sum(add modifiers)) * product(multiply modifiers)
        Base stat fields on StatsComponent are NEVER mutated to reflect
        buffs/debuffs/equipment — only this function's *return value*
        reflects them. Callers must not read StatsComponent.<field> directly
        expecting a "current" value; they must call get_stat.
        """

    def add_modifier(
        self, entity_id: int, tag: str, stat: str, op: str, value: float
    ) -> None:
        """op is 'add' or 'multiply'. Emits stat_modifier_applied."""

    def remove_modifiers_by_tag_prefix(self, entity_id: int, tag_prefix: str) -> int:
        """Removes every modifier whose tag starts with tag_prefix. Returns
        count removed. This is how equipment unequip, buff expiry, and set
        bonus recompute all clear their modifiers — by prefix, not by exact
        tag, because e.g. an item's tag `item_{eid}_{i}` may have several
        per-stat entries sharing that instance prefix."""
```

`StatsComponent` (new component, registered in `_COMPONENT_REGISTRY`):

```python
@component
@dataclass
class StatsComponent:
    base: dict[str, float]          # e.g. {"strength": 10, "max_hp": 30, ...}
    modifiers: dict[str, tuple[str, str, float]]
    # modifiers key = instance-id tag (str), value = (stat_name, op, value)
    # op in {"add", "multiply"}
```

**Tag conventions (binding — other components' removal calls depend on these
exact shapes):**

| Tag pattern | Owner / meaning |
|---|---|
| `item_{eid}_{i}` | `04`: one equipped item instance's `i`-th stat_modifier entry. Removed whole-prefix (`item_{eid}_`) on unequip. |
| `affix_{id}_{i}` | `04`: one rolled affix's `i`-th stat_modifier entry. |
| `{instance_id}:{stat}` | `03`/status effects: one active status instance's effect on one stat. Removed by exact tag (not prefix) on that instance's expiry, or by prefix `{instance_id}:` to clear all its stats at once. |
| `set_{id}_{tier}_{i}` | `04`'s `SetTrackerSystem`: one active set-bonus tier's `i`-th stat_modifier. Removed whole-prefix (`set_{id}_`) and reapplied on every `equip_changed` recompute, since set bonuses are full-replacement lists, not additive across tiers. |

Order within `modifiers` iteration does not matter for `add` (sum is
commutative); for `multiply` it also does not matter (product is
commutative) — this is exactly why "Option C" was chosen over an
order-sensitive chain.

### 2.2 `EffectResolver`

```python
# engine/systems/effects.py

def apply_effect(effect: dict, source_id: int, target_id: int, world: World,
                  event_bus: EventBus, position: tuple[int, int] | None = None) -> None:
    """THE single entry point every spell/potion/trap/equipment-proc/status
    tick calls to resolve one effect dict (see §5 schema). Dispatches on
    effect["type"]. Unknown types are silently skipped (logged once at
    DEBUG, never raises) — this is what lets 03/04 add new effect types
    later without touching this dispatcher.
    Formula-bearing fields (e.g. "amount": "2d6 + (INT * 0.5)") are
    evaluated via engine.core.formula.eval_formula with context built from
    StatsSystem.get_stat(source_id, ..., world) for every stat name the
    formula's identifiers could reference, uppercased (INT, STR, DEX, ...).
    roll_dice is used instead when the field is pure dice notation with no
    stat identifiers.
    """
```

Effect types and their required fields (all others silently ignored):

| `type` | Fields | Behavior | Emits |
|---|---|---|---|
| `damage` | `amount` (formula), `damage_type` | rolls amount, subtracts from target HP (via `get_stat`/direct HP field per §2.3), triggers death handling if HP <= 0 | `damage_dealt` |
| `restore_hp` | `amount` (formula) | adds to HP, clamped to max_hp | `message` (optional) |
| `restore_mp` | `amount` (formula) | adds to MP, clamped to max_mp | — |
| `stat_modifier` | `stat`, `op`, `value`, `duration` (turns, `0`/absent = permanent), `tag` (optional override; default `{new_instance_id}:{stat}`) | calls `StatsSystem.add_modifier`; if `duration` > 0, schedules expiry via StatusEffectsSystem-style bookkeeping is NOT this system's job — a timed non-status stat_modifier is registered with the foundation's tick hook is out of scope here; **default assumption:** any `stat_modifier` effect with a nonzero `duration` should instead be authored as an `apply_status` effect wrapping a status that carries the modifier — document this steering note verbatim in your code comments so content authors don't hit a dead end | `stat_modifier_applied` |
| `noise` | `radius` | emits at `position` (falls back to target's position) | `noise_emitted` |
| `vfx` | `vfx_id`, `data` (optional) | pure passthrough | `vfx_play` |
| `script` | `script_ref` | looks up the Lua script by ref via the (future) `10-lua-scripting-layer.md` host; **if the Lua host isn't merged yet, this is a documented no-op per "absence = zero cost"** — log once, don't raise | — |
| `identify_item` | `item_instance_id` (optional; defaults to a sensible target) | delegates to `04`'s InventorySystem `identify_instance` entry point (call-boundary only, do not duplicate identify logic) | — |
| `remove_curse` | `item_instance_id` | delegates to `04`'s InventorySystem curse-clear entry point | — |
| `teleport_self` | `destination` (`"random_floor_tile"` or explicit coords) | moves source entity via SpatialHash + PositionComponent | `entity_moved` |
| `apply_status` | `effect_id`, `duration` (turns), `magnitude` (optional, passed through) | calls `03`'s `StatusEffectsSystem.apply(entity_id, effect_id, duration, magnitude, world, event_bus) -> instance_id` **(call-boundary only — see §1 Out of Scope)** | `status_applied` (emitted by StatusEffectsSystem itself; EffectResolver does not double-emit) |
| `lifesteal` | `percent` | heals `source_id` for `percent` of the damage this same effect list already dealt (requires a `damage` effect earlier in the same list; reads the amount from the resolver's running context for this `apply_effect` call batch) | `restore_hp`-equivalent message |
| `reduce_armor` | `amount`, `duration` | sugar for a `stat_modifier` on `armor` with `op: "add"`, negative `value` | `stat_modifier_applied` |
| `trigger_level_complete` | — | passthrough | `trigger_level_complete` (consumed by `06`'s CampaignSystem) |
| `learn_spell` | `spell_id` | delegates to `05`'s ProgressionSystem spell-learning path (or directly grants if entity has a spellbook component — implementation default: emit and let `05` own the actual grant) | `spell_learned` |

An effect *list* (e.g. a spell's `effects: [...]`) is resolved by calling
`apply_effect` once per entry, in list order, sharing one resolution context
(needed for `lifesteal` reading the preceding `damage` roll — pass a mutable
`context: dict` through a private list-resolution helper,
`apply_effect_list(effects: list[dict], source_id, target_id, world, event_bus, position=None)`,
which is the function `03` and `04` actually call in practice; `apply_effect`
remains the single-effect primitive for anything invoking one effect ad hoc).

### 2.3 `CombatSystem`

```python
# engine/systems/combat.py

def resolve_hit(attacker_id: int, defender_id: int, world: World,
                 event_bus: EventBus) -> bool:
    """THE single melee/ranged hit-resolution entry point. Called by player
    input handling and by 02's AISystem for monster attacks — this is the
    other half of the public contract this doc exists to nail down.
    Pipeline:
      1. hit_chance = eval_formula(configs["difficulty"]["hit_chance_formula"],
         context={ATTACKER_DEX: ..., DEFENDER_DEX: ..., DEPTH: current_depth,
                   ...}) -- see §5 for the config shape and depth-aware
         difficulty multiplier lookup.
      2. roll; on miss: emit `miss` {attacker_id, defender_id}, return False.
      3. on hit: roll damage via the attacker's weapon/base damage formula
         (from the attacker's entity/item data, resolved through get_stat
         for damage_min/damage_max), build a `damage` effect dict, and call
         EffectResolver.apply_effect_list([...], attacker_id, defender_id,
         world, event_bus, position=<defender's position>).
      4. EffectResolver's `damage` handling emits damage_dealt and reduces
         HP; if resulting HP <= 0, CombatSystem's death handling fires (see
         below) rather than EffectResolver itself deciding death, so that
         death handling stays one place regardless of what dealt the killing
         blow (combat, a spell, a status tick, etc. — all route damage
         through EffectResolver, but only CombatSystem's post-damage check
         performs death handling, invoked as a small helper EffectResolver
         calls back into: `combat.handle_potential_death(target_id, killer_id,
         world, event_bus)` — this is a still a single-direction call
         EffectResolver makes into CombatSystem's public helper, not a
         circular dependency, since it's one named function, not
         cross-importing internals).
      Returns True on hit, False on miss.
    """

def handle_potential_death(target_id: int, killer_id: int, world: World,
                            event_bus: EventBus) -> None:
    """Checks target's current HP via get_stat; no-ops if still alive.
    If target is the player entity (per foundation's player-id convention):
      emit `player_died` {entity_id}. Entity is NOT destroyed — game-over
      flow is a higher-level (07/08) concern.
    Else (monster):
      emit `death` {entity_id, killer_id, xp_value} (xp_value read from the
      monster's data definition) BEFORE `entity_died`, since `death` is what
      05's ProgressionSystem awards XP from and `entity_died` is the general
      "this entity is gone" notification other listeners (AI target
      invalidation, quest hooks) want.
      emit `entity_died` {entity_id, killer_id}.
      emit `loot_drop` {position, entries} — entries is the monster's raw
      loot_table entries (unresolved; 04's LootSystem is what rolls them
      into real item instances — see 04-inventory-items-loot.md §"loot_drop
      handoff" for the exact split of responsibility).
      world.destroy_entity(target_id).
    """

def process_monster_turns(world: World, event_bus: EventBus) -> None:
    """Runs one AI turn for every awake monster, in ascending entity-ID
    order (deterministic — required for replay/testing determinism per v1
    convention). This function's body is a thin loop that calls into 02's
    AISystem per-entity decision function; CombatSystem owns the loop and
    turn-order guarantee, 02 owns what each monster decides to do. If 02
    isn't merged yet, this loops and no-ops per CONTRACTS.md §8 stubbing —
    document the exact call it makes (`ai_system.take_turn(entity_id, world,
    event_bus)`) so 02's session builds to match without needing your code.
    """
```

## 3. Consumes

Nothing gameplay-level upstream (Wave 0 only: ECS, event bus, registry,
formula, spatial hash). Documented for awareness, not required at build
time:
- `02-ai-system.md`'s AISystem will call `resolve_hit` for monster melee
  attacks and expects the signature above exactly.
- `03-spells-status.md`'s SpellSystem and `04-inventory-items-loot.md`'s
  InventorySystem will call `EffectResolver.apply_effect_list` for spell
  and item-use effect resolution.
- `04`'s `equip_changed` event populates `StatsComponent.modifiers` (via
  its own calls to `StatsSystem.add_modifier`) — you do not subscribe to
  `equip_changed` yourself; `04` calls your `add_modifier` directly as a
  documented cross-component call into your public API (this is allowed
  per CONTRACTS.md §2.4 — it's calling a system's *public interface*
  function, not reaching into its internals; the alternative of routing a
  stat-modifier request through an event round-trip was considered and
  rejected as needless indirection for a synchronous same-tick operation,
  consistent with how `01`/`03`/`04` all call `EffectResolver` directly
  rather than eventing it).

## 4. File/Directory Ownership

You own, exclusively:
- `engine/systems/stats.py`
- `engine/systems/effects.py`
- `engine/systems/combat.py`
- `tests/unit/test_stats.py`, `test_effects.py`, `test_combat.py`
- `tests/fixtures/effects/` (sample effect JSON fixtures for your own tests)
- `data/config/difficulty.json` (hit-chance formula + depth multiplier
  table — see §5)
- `data/effects/` fixture content sufficient to exercise all 14 effect
  types in tests (real content volume is out of scope per spec §12, but a
  handful of real files must exist so the `effects` registry namespace
  isn't empty)

You add entries to (shared, additive-only):
- `engine/core/save.py:_COMPONENT_REGISTRY` — add `StatsComponent`.
- `docs/CONTRACTS.md` §3.2 — only if you find yourself needing an event not
  already in the table (flag it, don't silently add one without a strong
  reason; the table already covers `damage_dealt`, `miss`, `death`,
  `entity_died`, `player_died`, `loot_drop`, `stat_modifier_applied`,
  `status_applied`, which is everything this component needs).

## 5. Data Schemas

### 5.1 `StatsComponent.modifiers` (see §2.1) — no JSON file, in-memory/save only.

### 5.2 Item stat_modifiers (spec §4, `long_sword.json`)

```json
{
  "id": "long_sword",
  "display_name": "Long Sword",
  "item_type": "weapon",
  "equippable": true,
  "slot": "main_hand",
  "stat_modifiers": [
    {"stat": "damage_min", "operation": "add", "value": 3},
    {"stat": "damage_max", "operation": "add", "value": 8}
  ]
}
```

`operation` is `"add"` or `"multiply"` — maps 1:1 to `StatsSystem.add_modifier`'s
`op` parameter (`04` is responsible for reading this list on equip and
calling `add_modifier` once per entry with tag `item_{eid}_{i}`). You do not
own this file's schema (that's `04`'s), but you must accept exactly this
`operation` vocabulary in `add_modifier` — reject/log any other op string
once at DEBUG rather than silently treating it as `"add"`.

### 5.3 `data/config/difficulty.json` (you own this file)

```json
{
  "schema_version": 1,
  "hit_chance_formula": "0.75 + (ATTACKER_DEX - DEFENDER_DEX) * 0.02",
  "hit_chance_min": 0.05,
  "hit_chance_max": 0.95,
  "depth_multipliers": [
    {"min_depth": 1, "max_depth": 5, "monster_damage_mult": 1.0, "monster_hp_mult": 1.0},
    {"min_depth": 6, "max_depth": 10, "monster_damage_mult": 1.25, "monster_hp_mult": 1.3},
    {"min_depth": 11, "max_depth": 999, "monster_damage_mult": 1.6, "monster_hp_mult": 1.75}
  ]
}
```

`depth_multipliers` entries are matched by `min_depth <= current_depth <=
max_depth`; the current floor's depth is read from `06`'s FloorManager state
(a plain int the combat system reads off world/global run state — document
the exact accessor once `06` merges; until then, default to `depth = 1` and
log once that depth-aware scaling isn't wired, per CONTRACTS.md §8). **v1
lesson called out explicitly in the spec:** difficulty scaling always read
`thresholds[0]` regardless of actual floor depth in v1 — this file's lookup
must actually index by `current_depth`, not hardcode `[0]`; add a unit test
that asserts two different depths produce two different multipliers to
guard against silently regressing into the v1 bug.

### 5.4 Effect dict shape (used inside spells/items/loot — canonical for all producers)

```json
{"type": "damage", "amount": "2d6 + (INT * 0.3)", "damage_type": "fire"}
```

```json
{"type": "apply_status", "effect_id": "poisoned", "duration": 5, "magnitude": 2}
```

See §2.2's table for the full field vocabulary per type.

## 6. Lessons from v1 applied here

- **`damage` vs `damage_dealt` naming bug.** v1 emitted both for the same
  concept and the renderer subscribed to the wrong one, silently killing
  hit VFX. **v2 emits `damage_dealt` only — there is no `damage` event
  anywhere in this codebase.** If you catch yourself typing
  `event_bus.emit("damage", ...)` anywhere, that's a bug — fix it before
  committing. Add a grep-based or AST-based unit test
  (`test_no_damage_event_emitted`) that scans `engine/systems/*.py` for the
  literal string `"damage"` passed to `emit(` and fails the build if found
  (allow `"damage_dealt"`, `"damage_type"`, `"damage_min"`, etc. — match the
  event-name argument position specifically, not any substring).
- **Difficulty scaling silently only reading index 0.** Covered in §5.3 —
  make the depth lookup real and tested, not a placeholder.
- **"Built but not wired."** `resolve_hit` and `apply_effect_list` are
  useless if nothing calls them. Since you own the loop
  (`process_monster_turns`) but not the AI decision inside it, and you own
  effect resolution but not the callers in 03/04, your integration test
  (§8) must simulate a real caller — not just call your own functions in
  isolation — to prove the pipeline actually produces `damage_dealt` →
  death → `loot_drop` end-to-end.

## 7. Definition of Done

- [ ] `StatsSystem.get_stat` implements Option C exactly:
      `(base + sum(adds)) * product(multiplies)`, unit tested with multiple
      adds and multiplies on the same stat, and a case proving order of
      insertion doesn't matter.
- [ ] `add_modifier` / `remove_modifiers_by_tag_prefix` implemented and
      tested against all four tag conventions in §2.1's table.
- [ ] Base `StatsComponent.base` fields are never mutated by any code in
      this component — enforced by a test that calls `add_modifier` several
      times and asserts `base` dict is unchanged while `get_stat` reflects
      the modifiers.
- [ ] `EffectResolver.apply_effect` / `apply_effect_list` implemented for
      all 14 effect types in §2.2's table, each with at least one unit test.
      Unknown type is silently skipped (test asserts no exception and no
      event emitted for a bogus `"type": "made_up_effect"`).
- [ ] `lifesteal` correctly reads the preceding `damage` roll from the same
      `apply_effect_list` call's context (tested with a two-effect list).
- [ ] `CombatSystem.resolve_hit` implements the full pipeline in §2.3,
      hit-chance formula sourced from `data/config/difficulty.json`,
      depth-aware multiplier actually indexes by depth (not `[0]` — see
      §5.3/§6).
- [ ] `handle_potential_death` distinguishes player vs. monster death per
      spec exactly: player → `player_died`, entity intact; monster →
      `death` then `entity_died` then `loot_drop`, entity destroyed.
- [ ] `process_monster_turns` iterates awake monsters in ascending
      entity-ID order — tested with 3+ monsters asserting call order.
- [ ] `StatsComponent` added to `_COMPONENT_REGISTRY`, alphabetically.
- [ ] No literal `"damage"` event emission anywhere (§6 grep test) —
      `damage_dealt` is the only damage event in the codebase.
- [ ] Every event this component emits (`damage_dealt`, `miss`, `death`,
      `entity_died`, `player_died`, `loot_drop`, `stat_modifier_applied`,
      `status_applied` via the apply_status call-boundary) is documented in
      CONTRACTS.md §3.2 already — no new events needed; confirm and don't
      add any without flagging per §7 rule.
- [ ] `pytest` green.

## 8. Test Plan

Unit tests (`tests/unit/test_stats.py`, `test_effects.py`, `test_combat.py`):
- Option C math correctness (adds-only, multiplies-only, mixed, zero
  modifiers = base unchanged).
- Tag-prefix removal correctness for each of the 4 tag conventions.
- Each of the 14 effect types resolves and emits the right event(s) with
  the right payload shape.
- `resolve_hit` hit and miss branches, with a fixed RNG seed to make the
  roll deterministic in tests.
- Depth-multiplier lookup returns different multipliers for depth 3 vs.
  depth 12 (guards the v1 regression named in §5.3/§6).
- Death handling: player entity survives `handle_potential_death` with
  `player_died` emitted; monster entity is destroyed with `death` →
  `entity_died` → `loot_drop` emitted in that order.

Integration test (`tests/integration/test_combat_pipeline.py` — required
per CONTRACTS.md §7.3, not just unit tests of isolated functions):
- Build a real `World` + event bus + a fixture monster entity with
  `StatsComponent` (low HP) and a fixture player entity with enough damage
  to one-shot it.
- Subscribe test listeners to `damage_dealt`, `death`, `entity_died`,
  `loot_drop`.
- Call `resolve_hit(player_id, monster_id, world, event_bus)` directly (the
  real public entry point — not a mock of `EffectResolver` or of death
  handling) and assert: `damage_dealt` fired with correct amount/type,
  `death` fired with correct `xp_value` from the monster's data definition,
  `entity_died` fired, `loot_drop` fired with the monster's raw
  `loot_table.entries`, and the monster entity no longer exists in `world`
  (`world.get_component(monster_id, StatsComponent) is None` or equivalent
  existence check).
- A second integration case: a full effect list
  `[{"type": "damage", ...}, {"type": "lifesteal", "percent": 50}]` run
  through `apply_effect_list` end-to-end against real `StatsSystem`/`World`
  instances, asserting the source entity's HP actually increased by the
  correct lifesteal amount — proves the shared-context mechanism works
  against real components, not a mocked context dict.

## 9. Open Questions & Defaults

- **Timed non-status `stat_modifier` effects** (a `stat_modifier` effect
  with `duration > 0` that isn't wrapped in `apply_status`): default is to
  treat this as a content-authoring anti-pattern rather than a code path —
  document it (done in §2.2's table) and don't build separate expiry
  bookkeeping for it in this component. If a real content need for a
  "timed modifier with no associated status" surfaces later, that's a
  CONTRACTS.md-level decision (new mechanism), not something to improvise
  here.
- **Current floor depth accessor for difficulty scaling**: defaults to a
  module-level fallback of `depth = 1` with a "not wired yet" log-once
  until `06-worldgen-campaign.md` merges and exposes the real accessor;
  update the one call site when it does. Note this explicitly in your PR
  so Wave 3 integration verification checks it got updated.
- **`script` effect type without the Lua host merged**: no-op, logged once,
  per CONTRACTS.md §8 and the foundation's "absence = zero cost" rule —
  do not stub a fake Lua evaluator.
