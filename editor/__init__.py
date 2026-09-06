"""SLATE v2 content editor — a standalone pygame-ce application.

See ``docs/components/13-editor-core-authoring.md``. This package never
imports ``engine.main`` or any gameplay system; it shares only the JSON
data schemas documented in ``docs/CONTRACTS.md`` (the one deliberate
exception is ``editor/modes/skin_editor.py``, which imports
``engine.ui.ui_runtime`` purely for read-only live-preview rendering — see
that module's docstring).
"""
