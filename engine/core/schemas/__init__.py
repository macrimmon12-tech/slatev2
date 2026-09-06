"""Lightweight, dependency-free JSON content validators.

CONTRACTS.md §9: "Every new JSON schema introduced by a component must ship
a ``jsonschema`` (or equivalent lightweight validator) under
``engine/core/schemas/`` and a round-trip test loading every fixture file
under its ``data/`` directory." This package is additive-only and shared
across components, exactly like ``engine.core.save._COMPONENT_REGISTRY`` —
each component that introduces a new JSON schema adds its own new module
here (named after the namespace/schema it validates, e.g.
``ui_skin_schema.py``, ``status_effects.py``) and never edits another
component's module.

``00-foundation-core.md`` claims "``engine/core/`` (all files)" as its own
exclusive ownership, but that doc predates this per-component-schema
convention that ``CONTRACTS.md`` §9 and several sibling component docs
(07, 08, 10, 11, 15) each explicitly assign a distinct new filename under
this exact directory to a *different* Wave 1 component — precisely the
same additive, everyone-adds-their-own-file shape as the component
registry. Per CONTRACTS.md's own precedence rule ("if your component doc
and this doc ever disagree, this doc wins"), multiple components
(``03-spells-status.md`` and ``08-ui-runtime.md`` both independently
created this package before either had merged) create their own modules
here and flag the apparent tension with 00's blanket wording in their PRs
rather than silently picking a different location.

No third-party ``jsonschema`` dependency is added (``pyproject.toml`` is
foundation-owned and not touched here); every validator in this package is
a small hand-rolled function, which CONTRACTS.md §9 explicitly allows
("or equivalent lightweight validator").
"""
