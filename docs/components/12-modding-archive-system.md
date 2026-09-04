# Component 12 — Modding & Archive System

**Wave:** 1 (parallel).
**Depends on:** Wave 0 (`00-foundation-core.md`) — specifically extends
`DataRegistry`'s documented extension point for archive sources. Build
against `00-foundation-core.md`'s public `DataRegistry` shape (§2.3 of that
doc) directly; that component is a Wave 0 blocking merge, so its real code
should exist by the time this session starts.

## 1. Scope & Boundaries

You implement `.pak`/`.pkd` archive reading for real. This is not a design
exercise — the entire reason this component exists is that v1 never closed
this out (§1.1 below). The mod system's *semantics* (load order, override
rules) were already conceptually complete in v1 and are already built into
`00-foundation-core.md`'s `DataRegistry` (loose-file directory resolution,
same-priority-duplicate-is-an-error, different-priority-is-silent-override).
Your job is to make **archive sources** participate in that same resolution
pipeline as first-class citizens, not to redesign the override semantics.

In scope:
- `engine/modding/archive.py`: opens `.pak`/`.pkd` files (renamed zip
  archives, read identically regardless of extension), exposes their
  contents as a virtual file source the registry can enumerate and read
  from.
- `engine/modding/load_order.py`: parses `mods/load_order.txt`, resolves
  the ordered list of mod archive paths (top = lowest priority, per
  CONTRACTS.md §4), and produces the final ordered source list: base
  archives → mod archives (load-order sequence) → loose files.
- Extending `00-foundation-core.md`'s `DataRegistry.load()` call site so
  archive sources are included in resolution — **without changing that
  method's public shape**, per that component's own doc ("the archive
  component will extend the source list later without changing this
  module's public shape"). Concretely: you add a parameter/argument at the
  call site (main.py's construction of the registry, or a documented
  optional `archive_paths` argument `DataRegistry.load()` already reserves
  room for) — read `00-foundation-core.md`'s actual merged code for the
  exact extension seam before writing this; if it reserved a specific hook,
  use it verbatim rather than inventing a parallel one.
- `mods/load_order.txt` convention/format (§5).

Out of scope:
- PyInstaller/Windows packaging (spec §12) — explicitly deferred for the
  whole v1 rebuild, and doubly explicitly not this component's job even
  though it's the direct prerequisite. Do not scope-creep into it. Note in
  your PR that packaging remains deferred per `docs/SLATE_REDESIGN_SPEC.md`
  §12 and `docs/ORCHESTRATION.md` §6.
- The editor's Pack Project feature (writing `.pak`/`.pkd` build artifacts
  into `dist/`) — that's `13-editor-core-authoring.md`'s Project Manager;
  you provide the *read* path, they provide (or soft-depend on you for) the
  *write* path. If `13`'s session needs a `write_archive()` helper, a
  small one here is reasonable to add and document, but the primary
  archive-writing logic and its UI belong to `13`.
- Any change to `DataRegistry`'s duplicate-ID error/override semantics —
  reuse exactly what `00-foundation-core.md` already built; if you find a
  gap in that logic while integrating archives, fix it by extending, never
  by re-implementing the override/error decision elsewhere.

### 1.1 The v1 failure this component exists to close out

Per spec §10/§10.1: in v1, `DataRegistry._load_archive` was a **documented
no-op stub for the entire project's life** — "Phase 9/10 fills this in" was
written early and never closed. The mod system was conceptually complete
(loose-file override semantics worked correctly and were tested) but
**never exercised end-to-end for actual archive reading** — nobody ever
proved a real `.pak` file's contents actually reached the registry. This
component's entire reason to exist is to finally implement that, for real,
not to stub it again. This is not a nice-to-have Definition of Done item —
see §8, the real-zip-fixture integration test is the load-bearing
requirement of this whole doc.

## 2. Provides

```python
# engine/modding/archive.py
class ArchiveSource:
    def __init__(self, path: Path): ...          # opens a .pak or .pkd (both = zip)
    def list_files(self) -> list[str]: ...        # archive-relative paths
    def read(self, relative_path: str) -> bytes: ...
    def exists(self, relative_path: str) -> bool: ...

def open_archive(path: Path) -> ArchiveSource: ...  # extension-agnostic

# engine/modding/load_order.py
def parse_load_order(load_order_path: Path, mods_dir: Path) -> list[Path]: ...
    # returns mod archive paths in priority order, lowest first (top of file = lowest priority)

def resolve_source_list(
    base_archive_dir: Path | None,
    mods_dir: Path,
    loose_dirs: list[Path],
) -> list["ContentSource"]: ...
    # final ordered list: base archives (if any) -> mod archives (load_order.txt order)
    # -> loose files, lowest priority first, matching CONTRACTS.md §4 exactly
```

- `ArchiveSource` treats `.pak` and `.pkd` **identically** — both are zip
  files; the extension is a human-readable signal (data vs. assets) with
  **zero functional branching** in the reader. Do not special-case the
  extension anywhere except cosmetic logging/naming.
- `resolve_source_list` is the function `00-foundation-core.md`'s
  `DataRegistry.load()` call site invokes (or is invoked by, depending on
  the exact seam that component reserved) to get the final ordered source
  list that replaces/extends the "already-resolved list of loose-file
  directories" that component's doc says it was built to accept. Each
  `ContentSource` in the returned list exposes the same minimal read
  interface (`list_files`/`read`/`exists`) whether it's backed by an
  archive or a loose directory — the registry's per-namespace directory
  walk should not need to know which kind it's looking at. If
  `00-foundation-core.md`'s actual merged code has a different
  `ContentSource`-shaped abstraction already, conform to that shape exactly
  rather than introducing a parallel one — read the real file before
  writing code, not just this doc's guess at the shape.
- Mod folder structure **mirrors the base game layout exactly** — a mod
  archive/folder's internal paths look like `data/entities/monsters/foo.json`,
  `assets/sprites/foo.png`, etc., identical to the base game's own
  `data/`/`assets/` tree, so the same namespace-to-directory resolution
  table (CONTRACTS.md §4) applies uniformly regardless of source.

## 3. Consumes

Nothing at runtime as an event-bus subscriber — this is a load-time-only
component, invoked once during startup before the event bus or gameplay
systems are meaningfully running, consistent with `00-foundation-core.md`'s
"Data Registry is loaded once at startup, zero disk I/O during gameplay"
rule (CONTRACTS.md §2.10). You are additional disk I/O *at* startup, folded
into that same one-time load, never during gameplay.

## 4. Emits

Nothing. Load-time-only; no event-bus interaction.

## 5. Data Schemas / Conventions

### 5.1 `mods/load_order.txt`

Plain text, one mod folder/archive name per line, blank lines and lines
starting with `#` ignored. **Top of file = lowest priority** (per
CONTRACTS.md §4 verbatim) — later lines override earlier ones, and loose
files still override everything regardless of position in this file.

```
# mods/load_order.txt
# Lines are mod archive filenames (relative to mods/) or mod folder names.
# Top = lowest priority. Loose files always win regardless of this order.
balance_tweaks.pak
new_monsters.pak
new_monsters_assets.pkd
```

A mod entry may be a `.pak`/`.pkd` archive file living directly under
`mods/`, or (for mod development without packing) an unpacked folder under
`mods/<name>/` mirroring the base layout — both resolve through the same
`ContentSource` interface; an unpacked mod folder is treated as another
loose-file tier at that priority position, not specially. Document which
you support in your implementation; supporting both is recommended since
it matches "loose folder = fast iteration, packed archive = distribution"
which is the whole point of the Bethesda/MO2 model this mirrors.

### 5.2 Archive internal layout

No new schema — a `.pak`/`.pkd` file's internal structure is a zip of the
exact same `data/`/`assets/` tree layout documented in CONTRACTS.md §1.
Nothing about JSON content shape changes when it's read from inside an
archive vs. loose disk — `ArchiveSource.read(relative_path)` returns the
identical bytes a loose-file read would, so every downstream JSON
parsing/schema-validation path in the registry is completely unaware of
where the bytes came from.

## 6. File/Directory Ownership

You own, exclusively:
- `engine/modding/archive.py`
- `engine/modding/load_order.py`
- `mods/load_order.txt` (the convention/format and a real default file —
  ship an empty-but-valid one, e.g. just the comment header, so a fresh
  checkout has zero mods active and boots identically to no-mod-system-at-all)
- `tests/unit/test_archive.py`, `test_load_order.py`
- `tests/fixtures/archives/` (fixture `.pak`/`.pkd` files built at test time
  — see §8, these should be generated by the test itself via `zipfile`,
  not committed binary blobs, so they stay reviewable and don't bloat the
  repo)

You extend, without changing its public shape:
- `00-foundation-core.md`'s `DataRegistry.load()` call site / source
  resolution — read that component's actual merged `engine/core/registry.py`
  before writing this integration, and document in your PR exactly which
  seam you used (constructor argument, an injected source-list builder
  function, etc.).

## 7. Lessons from v1 applied here

- **"Built but not wired" (spec §11.1), archive edition.** This entire
  component's existence is a direct response to `_load_archive` being a
  no-op for the project's whole life while everything around it (load
  order parsing, override semantics, the Pack button in the editor) was
  built as if it worked. The Definition of Done in §8 makes the real
  integration test non-negotiable specifically to prevent this component
  from repeating the exact same failure shape one level down (e.g.
  "`ArchiveSource.read()` works in isolation, but the registry never
  actually calls it").
- **Loose-files-always-win is not renegotiable.** Do not let archive
  integration accidentally invert priority (e.g. by resolving archives
  after loose files in the source list, or by giving the *last-listed*
  archive source's directory a higher walk-order than loose files in a
  naive glob merge). The integration test in §8 explicitly checks this
  ordering with all three tiers present at once.
- **PyInstaller packaging depends on this being finished** (spec §12) —
  worth stating here so a future Wave that does pick up packaging doesn't
  have to rediscover that this is its prerequisite; not a reason to do any
  packaging work now.

## 8. Definition of Done

- [ ] `ArchiveSource` reads `.pak` and `.pkd` files identically (a test
      builds one fixture zip, saves it with both extensions, and asserts
      identical `list_files()`/`read()` results from both).
- [ ] `parse_load_order` correctly orders mod archives from
      `load_order.txt` (top = lowest priority), skips comments/blanks, and
      handles a missing/empty file by returning an empty list (absence =
      zero cost — no mods active is not an error).
- [ ] `resolve_source_list` produces the exact CONTRACTS.md §4 priority
      order (base archives → mod archives in load-order sequence → loose
      files) with a test asserting the final resolved value for a
      same-id-different-priority case comes from the loose file even when
      archives are also present and even when an archive is listed last in
      load order.
- [ ] Same-id-same-priority-across-two-archives still raises the
      `00-foundation-core.md` duplicate-ID error (reusing that logic, not
      reimplementing it) — test with two mod archives at the same load-order
      "slot" is out of scope for load_order.txt's line-ordered model (each
      line is a distinct priority), so this specifically tests two entries
      that resolve to the *same* priority tier if your design allows it
      (e.g. base archives if multiple base archives exist at the same
      tier) — document if your implementation makes every load_order.txt
      line strictly its own priority (recommended; makes this case moot
      for mods specifically and simplifies reasoning).
- [ ] **Real integration test** (see §9) builds an actual zip fixture on
      disk via Python's `zipfile` module, points the extended
      `DataRegistry` at it through this component's resolution seam, and
      asserts content loads correctly from inside the archive, at the
      correct priority relative to loose files and other archives — this
      is the specific, non-negotiable test called for by CONTRACTS.md §7.3
      and this doc's §1.1.
- [ ] `mods/load_order.txt` ships as a real, valid (empty/commented) file
      in the repo so a fresh checkout boots with zero mods and no errors.
- [ ] `pytest` green.

## 9. Test Plan

Unit tests per §8. Integration test (CONTRACTS.md §7.3 — the load-bearing
one for this entire component):

- `tests/integration/test_archive_pipeline.py`:
  1. Build a real base-content loose directory fixture with e.g.
     `entities/monsters/goblin.json` (`id: "goblin"`, some base stats).
  2. Build a real `.pak` fixture via `zipfile.ZipFile` in a temp dir
     containing an override `entities/monsters/goblin.json` with a
     different stat value, plus a brand-new `entities/monsters/orc.json`.
  3. Build a real second `.pak` mod fixture with yet another override of
     `goblin.json` and register both in a fixture `mods/load_order.txt`
     (asserting the *later* line wins between the two mod archives).
  4. Add one more loose-file override of `goblin.json` in a mod-dev loose
     folder positioned as "loose," per §4's priority tier — this one must
     win over every archive regardless of load-order position.
  5. Construct a real `00-foundation-core.md` `DataRegistry`, feed it
     through this component's `resolve_source_list` seam, call `.load()`
     for real (no mocking of the registry or the archive reader), then
     assert: `registry.get("entities", "goblin")` reflects the loose-file
     override's stat value (highest priority), `registry.get("entities",
     "orc")` exists and was pulled from inside the base `.pak` (additive
     content from an archive actually loads), and the intermediate mod
     archive's goblin override is *not* what won (proving mod-archive
     internal ordering is respected but still loses to loose files).
  This test is the direct, concrete proof that closes out the v1 gap in
  §1.1 — it must exercise real zip files on real disk through the real
  registry, not mocks of any of these three things.

## 10. Open Questions & Defaults

- **Exact `DataRegistry` extension seam**: `00-foundation-core.md`'s doc
  says the registry "will extend the source list later without changing
  this module's public shape" but doesn't pin the exact mechanism (a
  constructor kwarg, a separate `set_archive_sources()` call before
  `.load()`, an injected callable). Default: read the actual merged
  `engine/core/registry.py` and use whatever concrete seam exists there
  (it should be evident from the `(directory, exclude_subdirs, key_field)`
  dict-based table structure that component's doc describes — extend that
  table's directory resolution to accept a `ContentSource` abstraction
  instead of a bare `Path`, if it doesn't already). If genuinely no seam
  exists (i.e. `00`'s implementation hardcoded `Path`-only directory
  walking with no injection point), that's a real gap — implement the
  minimal non-breaking extension yourself (e.g. wrap `Path` behind a thin
  `ContentSource`-compatible adapter so existing loose-dir code needs no
  change), and note the retrofit explicitly in your PR rather than
  silently forking registry logic.
- **Whether an unpacked mod folder (not yet packed to `.pak`/`.pkd`)
  counts as a distinct load_order.txt entry or as a loose-file tier**:
  default to supporting both — a `load_order.txt` line naming a bare
  folder under `mods/` (no extension) is read as an unpacked mod at that
  exact priority slot, functionally identical to an archive at that slot
  except the reader is a plain directory walk instead of a zip read. State
  this choice in your PR since it affects how designers iterate on mods
  pre-packing.
