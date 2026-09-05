"""JSON schema validators for content/config formats.

CONTRACTS.md §9: "Every new JSON schema introduced by a component must
ship a ``jsonschema`` (or equivalent lightweight validator) under
``engine/core/schemas/``." This directory is a shared, additive location —
analogous to ``engine/core/save.py``'s ``_COMPONENT_REGISTRY`` — each
component that introduces a new JSON schema adds its own new file here
(``ui_skin_schema.py``, ``controls_schema.py``, ``dialog.schema.json``,
etc. per the various component docs' File/Directory Ownership sections)
rather than editing another component's file. ``00-foundation-core.md``
claims "``engine/core/`` (all files)" as its own exclusive ownership, but
that doc predates this per-component-schema convention that
``CONTRACTS.md`` §9 and several sibling component docs (07, 08, 10, 11, 15)
each explicitly assign a distinct new filename under this exact directory
to a *different* Wave 1 component — precisely the same additive, everyone
-adds-their-own-file shape as the component registry. Per CONTRACTS.md's
own precedence rule ("if your component doc and this doc ever disagree,
this doc wins"), this component (``08-ui-runtime.md``) creates this
package and its own ``ui_skin_schema.py`` file here, and flags the
apparent tension with 00's blanket wording in its PR rather than silently
picking a different location.

``jsonschema`` is not a project dependency (``pyproject.toml`` is
foundation-owned and not touched here), so every validator in this
package is a small hand-rolled function — CONTRACTS.md §9 explicitly
allows "equivalent lightweight validator" as an alternative.
"""
