# SLATE v2 — Project Conventions

A from-scratch rebuild of SLATE, a tile-based roguelike (Python + pygame-ce
for the runtime, Lua via `lupa` as a dormant scripting layer, a fully
data-driven JSON content pipeline, and an in-house content editor).

**Start at [`docs/ORCHESTRATION.md`](docs/ORCHESTRATION.md)** if you're
picking up a component — it explains how the build is split into
independent, parallelizable sessions and links to the per-component docs
under `docs/components/`. **Read [`docs/CONTRACTS.md`](docs/CONTRACTS.md)
before writing any code** — it's the binding interface reference every
component doc assumes as ground truth; if a component doc and
`CONTRACTS.md` ever disagree, `CONTRACTS.md` wins.

## Repository layout

```
engine/
  core/            # ecs.py, events.py, registry.py, spatial_hash.py, save.py, formula.py
  systems/         # one module per gameplay system (added by Wave 1+ components)
  ui/              # ui_runtime.py, anim_system.py
  render/          # renderer.py, autotile.py
  audio/           # audio_system.py
  input/           # input_handler.py
  lua/             # lua_host.py, api/
  modding/         # archive.py, load_order.py
  main.py          # process entry point; wires systems, runs the game loop
data/
  entities/ items/ spells/ effects/ affixes/ campaigns/ maps/ config/ dialogs/ shops/ scripts/
scripts/           # base Lua library (npc_interaction.lua, dialog_walker.lua, shop.lua, ...)
editor/
  editor.py project.py modes/
saves/
mods/
  load_order.txt
tests/
  unit/            # tests/unit/test_<module>.py, one per module
  integration/      # cross-system / full-pipeline tests
docs/
  ORCHESTRATION.md CONTRACTS.md SLATE_REDESIGN_SPEC.md components/
```

See `docs/CONTRACTS.md` §1 for the authoritative version of this table,
including namespace/exclusion details — this copy is for orientation, that
one is the contract.

## Running tests

```
pip install -e ".[dev]"
tools/check.sh           # runs pytest; the project's single CI entrypoint
# or directly:
pytest
```

`tools/check.sh` lives under `tools/`, not `scripts/` — `scripts/` is
reserved for the base Lua content library (`10-lua-scripting-layer.md`),
not Python dev tooling; see `docs/components/00-foundation-core.md` §4.

## The `@component` marker

Every ECS component in this codebase is a data-only `@dataclass` decorated
with `@component` from `engine.core.ecs`, applied **above** `@dataclass`:

```python
from dataclasses import dataclass
from engine.core.ecs import component

@component
@dataclass
class PositionComponent:
    x: int
    y: int
```

`@component`:

1. Marks the class so the component-registry lint check
   (`tests/unit/test_component_registry.py`) can find it.
2. Fills in `to_dict()`/`from_dict()` via
   `dataclasses.asdict`/`cls(**data)` unless the class defines its own —
   so a plain component needs no serialization boilerplate.

Every component class must also be added, by name, to
`engine.core.save._COMPONENT_REGISTRY` **in the same PR that introduces
it** — a component missing from that dict is silently dropped on
save/load (this bit SLATE v1 more than once; see `docs/CONTRACTS.md` §2
rule 3 and §5). `_COMPONENT_REGISTRY` is shared and additive-only across
every component — keep entries alphabetical, never remove another
component's entry, expect merge churn there.

## Core rules that apply everywhere (full list: `docs/CONTRACTS.md` §2)

- Entities are bare integer IDs; components are data-only; systems never
  call each other directly — only through `engine.core.events` or shared
  components via `world.query(...)`.
- Distance is **Chebyshev** everywhere (`engine.core.spatial_hash.chebyshev_distance`) —
  no exceptions, no inlined distance formulas.
- Movement is 8-way; diagonal and cardinal cost the same.
- **Absence = zero cost.** Missing content, sprites, sounds, or scripts
  fall back silently and log once — never raise, block, or degrade
  performance.
- **Data over code.** Tunable numbers/strings live in JSON, evaluated
  through `engine.core.formula` (`roll_dice`, `eval_formula`) — never
  hand-rolled per system.
- The Data Registry (`engine.core.registry.DataRegistry`) is the only
  place that reads content JSON off disk, loaded once at startup.

## For anyone picking up a component

1. Read `docs/CONTRACTS.md` in full.
2. Read your component's doc under `docs/components/`.
3. Work on your assigned branch; touch only the files/directories your
   component doc and `CONTRACTS.md` §1 assign to you.
4. Follow your component doc's Definition of Done and Test Plan exactly.
5. `tools/check.sh` (pytest) must be green before you open a PR.

See `docs/ORCHESTRATION.md` for the full session/wave mechanics.
