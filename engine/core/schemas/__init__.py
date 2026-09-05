"""Lightweight, dependency-free JSON content validators.

CONTRACTS.md §9: "Every new JSON schema introduced by a component must ship
a ``jsonschema`` (or equivalent lightweight validator) under
``engine/core/schemas/`` and a round-trip test loading every fixture file
under its ``data/`` directory." This package is additive-only and shared
across components, exactly like ``engine.core.save._COMPONENT_REGISTRY`` —
each component adds its own module here (named after the namespace/schema
it validates) and never edits another component's module.

No third-party ``jsonschema`` dependency is added (the project doesn't
depend on it — see ``pyproject.toml``); each validator here is a small
hand-rolled function, which CONTRACTS.md §9 explicitly allows ("or
equivalent lightweight validator").
"""
