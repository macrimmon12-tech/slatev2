# tests/fixtures/

Test-only content trees and other fixture data, kept separate from the real
`data/` tree so unit/integration tests never depend on real game content
(which doesn't exist yet at the foundation layer, and will keep changing
shape as later components land).

Convention for anyone adding a fixture here:

- One subdirectory per fixture "purpose", named for what it exercises —
  e.g. `data_registry/` holds miniature content trees
  (`data_registry/base/`, `data_registry/mod_a/`, ...) shaped exactly like
  `data/` (CONTRACTS.md §4 namespaces) but with a handful of trivial JSON
  files per namespace, just enough to exercise load order, duplicate-ID
  handling, and namespace exclusion rules.
- Keep fixture content minimal and clearly fake (`"name": "Goblin (mod
  override)"`, not real game balance data) — these files exist to exercise
  loader mechanics, not to double as real content.
- Don't reuse a fixture tree across unrelated tests if doing so would let
  one test's assumptions leak into another (e.g. the same-priority
  duplicate-ID error case gets its own tiny tree,
  `data_registry/dup_same_priority/`, instead of polluting the main
  `data_registry/base/` tree that other tests load successfully).
- A component that needs a concrete instance to test against before its
  real dependency exists (CONTRACTS.md §8 stubbing) adds its own fixture
  file(s) here rather than waiting for the real content to land, and only
  deletes the fixture once the real dependency merges and the relevant
  integration test still passes against it.
