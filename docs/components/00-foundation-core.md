# Component 00 — Foundation Core

**Wave:** 0 (sequential, blocking — nothing else starts until this merges).
**Depends on:** nothing but `docs/CONTRACTS.md` and `docs/SLATE_REDESIGN_SPEC.md` §2.

## 1. Scope & Boundaries

You are building the substrate every other component imports. Nothing in
here is gameplay — no combat, no AI, no spells. If you find yourself
writing anything that looks like a gameplay rule, it belongs in a Wave 1
component instead; stop and leave a stub.

In scope:
- ECS core: entity allocation, `@dataclass` component storage, `world.query`.
- Event bus (`engine/core/events.py`).
- Data Registry (`engine/core/registry.py`), including load-order and
  duplicate-ID handling — **but not** archive (`.pak`/`.pkd`) reading; that's
  `12-modding-archive-system.md`. Wire the registry to accept an
  already-resolved list of (loose files) directories for now; the archive
  component will extend the source list later without changing this
  module's public shape.
- Spatial Hash (`engine/core/spatial_hash.py`) + the canonical
  `chebyshev_distance` function.
- Save system (`engine/core/save.py`): world serialization, floor
  snapshot/restore, `_COMPONENT_REGISTRY`, `spawned_uniques` tracking.
- Formula evaluation (`engine/core/formula.py`): `roll_dice`, `eval_formula`.
- Config loading conventions (how `data/config/*.json` becomes available via
  `registry.get("configs", "<filename>")`).
- Repo/project scaffolding: `pyproject.toml`, `pytest` setup, `CLAUDE.md`
  project-root conventions doc, `tests/fixtures/` directory convention,
  CI-runnable lint/test entrypoint (a `make test` or `scripts/check.sh` — pick
  one, document it in root `CLAUDE.md`).
- The component-registry lint check described in CONTRACTS.md §2.3 (a test
  that scans for the project's `@component` decorator/marker and fails if an
  entry isn't in `_COMPONENT_REGISTRY`).

Out of scope (leave as clearly-marked TODOs with a pointer to the owning doc):
- The actual game loop content (main.py wiring of real systems) — Wave 1
  components add their own subscriptions; you provide a minimal `main.py`
  that constructs the registry/event bus/world and would run an empty game
  (blank window, no crash) so later components have somewhere to hook in.
- Archive/mod loading — `12-modding-archive-system.md`.
- Rendering, input, audio — `07-input-renderer-audio.md`.

## 2. Provides (the interfaces every other component codes against)

### 2.1 ECS

```python
# engine/core/ecs.py
class World:
    def create_entity(self) -> int: ...
    def destroy_entity(self, entity_id: int) -> None: ...
    def add_component(self, entity_id: int, component: Any) -> None: ...
    def remove_component(self, entity_id: int, component_type: type) -> None: ...
    def get_component(self, entity_id: int, component_type: type) -> Any | None: ...
    def query(self, *component_types: type) -> Iterable[tuple[int, ...]]: ...
```

Provide a `@component` decorator (or equivalent marker, e.g. inheriting a
`Component` marker base class) used by every dataclass anywhere in the
codebase — this is what the registry lint check in §1 scans for. Document
the exact marker mechanism in this file once decided (see §11 Open
Questions) so every other component doc's dataclasses use it consistently.

### 2.2 Event bus

Exactly the API in `CONTRACTS.md` §3. Include the snapshot-before-dispatch
behavior as a unit test (subscribe-inside-handler must not receive the
in-flight event).

### 2.3 Data Registry

Exactly the API in `CONTRACTS.md` §4. Concretely:

```python
class DataRegistry:
    def load(self, data_root: Path, mod_load_order: list[Path] | None = None) -> None: ...
    def get(self, namespace: str, id: str) -> dict | None: ...
    def all(self, namespace: str) -> dict[str, dict]: ...
    def reload(self) -> None: ...
```

- Build the namespace → directory table from `CONTRACTS.md` §4 as data, not
  as a chain of if/elif — a dict of `(directory, exclude_subdirs, key_field)`
  so `12-modding-archive-system.md` can extend the directory list per
  namespace without touching this logic.
- `configs` namespace keys by filename stem, not `id` — every other
  namespace keys by the JSON `"id"` field. Enforce this as a load-time
  assertion so a malformed content file fails fast and loud in dev, never
  silently at runtime.
- Duplicate `id` at the same load priority → raise at load time with the
  two conflicting file paths in the message. Duplicate at different
  priority → higher priority wins, no warning (this is deliberate per
  CONTRACTS.md §4 — don't add a warning log here, it'll get noisy under
  normal modding use).

### 2.4 Spatial Hash

```python
class SpatialHash:
    def insert(self, entity_id: int, position: tuple[int, int]) -> None: ...
    def move(self, entity_id: int, old_pos: tuple[int, int], new_pos: tuple[int, int]) -> None: ...
    def remove(self, entity_id: int) -> None: ...
    def query_radius(self, center: tuple[int, int], radius: int) -> list[int]: ...
    def query_rect(self, top_left, bottom_right) -> list[int]: ...

def chebyshev_distance(a: tuple[int, int], b: tuple[int, int]) -> int: ...
```

`SpatialHash` is authoritative for position; `PositionComponent` is a
convenience mirror other systems query alongside other components, but any
code answering "what's near X" must go through `SpatialHash`, never
`world.query(PositionComponent)` with a manual distance loop. Document this
loudly in the module docstring — it's the #1 thing a Wave 1 session might
get wrong by not reading this doc closely.

### 2.5 Save system

```python
def serialize_world(world: World) -> dict: ...
def deserialize_world(data: dict, world: World) -> None: ...
def serialize_floor_snapshot(world: World, floor_id: str) -> dict: ...
def restore_floor_snapshot(world: World, data: dict) -> None: ...

_COMPONENT_REGISTRY: dict[str, type]  # name -> component class, additive, alphabetical
```

Player state persists across floors outside any snapshot — snapshots only
capture non-player entities + tilemap per visited floor. `spawned_uniques`
(a `set[str]` of legendary item IDs already dropped this run) is part of
top-level save state, not per-floor.

### 2.6 Formula evaluation

```python
def roll_dice(expr: str, rng: random.Random | None = None) -> int: ...
def eval_formula(expr: str, context: dict[str, float]) -> float: ...
```

Use `simpleeval`'s `EvalWithCompoundTypes`/`SimpleEval` with a restricted
function/name set — no arbitrary attribute access, no `__`-prefixed names
reachable. This is the one place in the codebase allowed to touch
`simpleeval` directly; every system imports these two functions.

## 3. Consumes

Nothing at runtime — this is the base layer. At dev time, its own tests
consume fixture JSON under `tests/fixtures/`.

## 4. File/Directory Ownership

You own, exclusively:
- `engine/core/` (all files)
- `engine/main.py` (minimal skeleton only — later components add to it,
  but you create it)
- `pyproject.toml`, root `CLAUDE.md`, `scripts/check.sh` (or your chosen
  lint/test entrypoint — note it does **not** collide with `scripts/*.lua`,
  the base Lua library directory from `10-lua-scripting-layer.md`; if that
  bothers you, put the Python dev script at `tools/check.sh` instead and
  say so in your PR)
- `tests/unit/test_ecs.py`, `test_events.py`, `test_registry.py`,
  `test_spatial_hash.py`, `test_save.py`, `test_formula.py`,
  `test_component_registry.py`
- `tests/fixtures/` directory scaffolding (empty-but-present with a
  `README.md` explaining the convention — later components add fixture
  files here)

## 5. Data Schemas

None owned here — `configs` namespace just loads whatever JSON exists
under `data/config/`. Create one real file so the registry has something to
load in its own tests: `data/config/game.json` with a trivial
`{"schema_version": 1}` body is sufficient; don't invent gameplay config
fields that belong to a later component.

## 6. Lessons from v1 applied here

- **Component registry omission was a silent-drop bug class in v1** —
  that's why §1's lint check exists as a hard requirement, not a nice-to-have.
  Do not skip it to save time; every downstream component depends on it
  existing so their own components don't silently fail to save/load.
- **Chebyshev distance, unified movement cost, and "new component not
  modified component"** were explicitly called out as v1 wins worth
  repeating unchanged (spec §11.5) — this component is where all three
  become structurally enforced (one distance function, one component
  marker/registry mechanism) rather than merely conventions.

## 7. Definition of Done

- [ ] `World` supports create/destroy/add/remove/get/query with unit tests
      covering multi-component queries and destroy-mid-query safety.
- [ ] Event bus matches CONTRACTS.md §3 exactly, including the
      snapshot-before-dispatch guarantee, with a test proving it.
- [ ] `DataRegistry` loads all 13 namespaces from CONTRACTS.md §4 against a
      fixture `data/` tree, enforces same-priority-duplicate-is-an-error and
      different-priority-duplicate-is-silent-override, with tests for both.
- [ ] `SpatialHash` + `chebyshev_distance` implemented and unit tested
      (insert/move/remove/query_radius/query_rect correctness, including a
      case that would give a different answer under Euclidean distance to
      prove Chebyshev is actually in effect).
- [ ] Save system round-trips a world with several component types,
      including a floor-snapshot round trip and `spawned_uniques`
      persistence, via a test.
- [ ] `_COMPONENT_REGISTRY` lint check exists, fails on an intentionally
      unregistered test component, passes once registered.
- [ ] `roll_dice`/`eval_formula` implemented with tests covering dice
      notation (`"3d6"`, `"1d8+2"`), arithmetic-with-context
      (`"0.4 - (INT * 0.03)"`), and a rejected-unsafe-expression case
      (e.g. an attribute-access or import attempt is rejected, not executed).
- [ ] `engine/main.py` boots to an empty running loop with zero content
      loaded and doesn't crash (proves "absence = zero cost" holds at the
      foundation level).
- [ ] Root `CLAUDE.md` documents: repo layout (mirrors CONTRACTS.md §1),
      how to run tests, the `@component` marker convention, and links to
      `docs/ORCHESTRATION.md` as the entry point for anyone picking up a
      component.
- [ ] `pytest` green.

## 8. Test Plan

- Unit tests per module as listed in §4/§7.
- One `tests/integration/test_foundation_boot.py`: builds a `World` +
  `DataRegistry` + event bus from a fixture data tree, spawns a few
  entities with mixed components, saves, tears down, reloads, and asserts
  entity/component state matches — this is the integration test that
  proves the whole substrate coheres, not just each piece in isolation.

## 9. Open Questions & Defaults

- **Component marker mechanism** (decorator vs. base class vs. naming
  convention): default to a `@component` class decorator that both marks
  the class for the lint scan and auto-registers `to_dict`/`from_dict` via
  `dataclasses.asdict`/`cls(**data)` if the dataclass doesn't override them.
  This keeps every Wave-1 component doc's dataclasses one line simpler
  (`@component \n @dataclass \n class FooComponent: ...`) instead of hand
  writing serialization per component. If you pick something else, update
  this doc and CONTRACTS.md §2.3's wording to match before other sessions
  start.
- **Lint check implementation** (AST scan vs. runtime registry diff vs.
  import-and-inspect all modules under `engine/`): default to
  import-and-inspect — walk `engine/core` and `engine/systems` packages,
  collect all classes carrying the `@component` marker, assert each is a
  value in `_COMPONENT_REGISTRY`. Simpler than AST, and correct as long as
  every component module is actually imported somewhere reachable from the
  test (add an explicit import list if package auto-discovery is awkward).
