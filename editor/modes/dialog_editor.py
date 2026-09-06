"""Dialog Editor (``docs/components/14-editor-visual-quest-dialog.md``
§1.2/§4) — the "simple case" half of this component: a pure GUI layer
directly over `11-npc-dialog-shop-content.md`'s dialog-graph JSON format
(``data/dialogs/*.json``, schema in that doc's §5.1). There is no
generation step and no round-trip-fidelity risk here (unlike the Visual
Quest Editor): the visual editor and the hand-editable JSON are literally
the same file, two views on one piece of data. Loading reads the JSON;
saving writes it back, structurally, with no intermediate representation
to keep in sync — every edit here mutates ``self.data`` (the in-memory
dict loaded straight from the file) in place, so an untouched field is
never touched, and dict/JSON key insertion order (preserved by Python's
``dict``/``json`` since 3.7) survives a load → edit → save round trip
unless the edit itself targets that exact key.

This component does not own `11`'s dialog schema — read that doc's §5.1
for the field vocabulary; nothing here redefines it. The one new schema
this module *does* own is its own editor-only sidecar (doc §5.1):
``data/dialogs/<id>.layout.json``, node canvas positions only, never
loaded by the Data Registry and never referenced by `11`'s Lua or the
runtime — losing it is harmless (re-auto-layout on next open).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from editor.modes import EditorContext, SaveAffordanceMixin, TooltipMixin

logger = logging.getLogger(__name__)

_LAYOUT_SCHEMA_VERSION = 1
_AUTO_LAYOUT_X_STEP = 260
_AUTO_LAYOUT_Y_STEP = 140

_TOOLTIPS = {
    "save_button": "Save this dialog back to data/dialogs/ (Ctrl+S)",
    "add_choice_button": "Add a new choice to this node",
    "condition_field": "A boolean expression over campaign./floor./stat. — false hides this choice",
    "goto_field": "Next node id — mutually exclusive with Action",
    "action_field": "A fixed action (e.g. open_shop, close) instead of a goto",
    "start_node_field": "The node dialog_walker.lua opens on first visit",
}


class DialogEditorMode(TooltipMixin, SaveAffordanceMixin):
    """Registers as a tab per `13-editor-core-authoring.md`'s mode
    interface (doc §3)."""

    def __init__(self, context: EditorContext) -> None:
        self._context = context
        self.tooltips = dict(_TOOLTIPS)
        self.dirty = False
        self.save_button_rect: tuple[int, int, int, int] = (0, 0, 80, 24)

        self.dialog_id: str | None = None
        self.data: dict[str, Any] = {}
        self.layout: dict[str, dict[str, int]] = {}
        self._loaded_path: Path | None = None
        self._layout_path: Path | None = None

    # -- loading -------------------------------------------------------------

    def _dialogs_dir(self) -> Path:
        project = self._context.project()
        if project is None:
            raise RuntimeError("dialog_editor: no active project")
        return project.data_path("dialogs")

    def load(self, dialog_id: str) -> None:
        """Read ``data/dialogs/<dialog_id>.json`` (11's schema) directly —
        one node per ``nodes[<id>]`` entry."""
        path = self._dialogs_dir() / f"{dialog_id}.json"
        self.data = json.loads(path.read_text(encoding="utf-8"))
        self.dialog_id = dialog_id
        self._loaded_path = path
        self._layout_path = path.with_suffix(".layout.json")
        self.layout = self._load_or_build_layout()
        self.mark_saved()

    def open_new(self, dialog_id_hint: str) -> None:
        """Context-link receiving entry point (doc §3): Map Editor
        (`13-editor-core-authoring.md`, out of this component's scope)
        placing an NPC with no ``dialog_id`` assigned yet jumps here via
        ``context.navigate_to("dialog_editor", {"new_dialog_id": <hint>})``
        — see :meth:`receive_context` for the exact payload key this side
        expects. Seeds a blank, valid dialog in memory (a single
        ``greet`` start node, no choices); nothing is written to disk
        until :meth:`save`."""
        self.dialog_id = dialog_id_hint
        self.data = {
            "id": dialog_id_hint,
            "start_node": "greet",
            "nodes": {"greet": {"text": "", "choices": []}},
        }
        self._loaded_path = self._dialogs_dir() / f"{dialog_id_hint}.json"
        self._layout_path = self._loaded_path.with_suffix(".layout.json")
        self.layout = {"greet": {"x": 80, "y": 120}}
        self.mark_dirty()

    def receive_context(self, context: dict[str, Any]) -> None:
        """The `13`-facing half of the §2.7/§3 context link:
        ``editor.navigate_to("dialog_editor", {"new_dialog_id": "<id>"})``
        arrives here via ``Editor.switch_mode``'s ``receive_context``
        callback (`13-editor-core-authoring.md` §2.3). Documented so `13`'s
        Map Editor side can call this exact shape once it implements the
        initiating click — not yet wired on 13's side today (13's Map
        Editor only implements its own entities/npcs/items/spells
        context link, not a dialog-specific one; see this component's PR).
        """
        dialog_id_hint = context.get("new_dialog_id")
        if dialog_id_hint:
            self.open_new(dialog_id_hint)

    # -- layout sidecar (doc §5.1) --------------------------------------------

    def _load_or_build_layout(self) -> dict[str, dict[str, int]]:
        if self._layout_path is not None and self._layout_path.exists():
            try:
                payload = json.loads(self._layout_path.read_text(encoding="utf-8"))
                positions = payload.get("node_positions")
                if isinstance(positions, dict):
                    return positions
            except (json.JSONDecodeError, OSError):
                logger.warning(
                    "dialog_editor: malformed layout sidecar %s — re-auto-laying-out", self._layout_path
                )
        return self._auto_layout()

    def _auto_layout(self) -> dict[str, dict[str, int]]:
        """A simple auto-layout on first load (doc §4): breadth-first from
        ``start_node``, columns by depth. Losing the layout file is
        harmless — this just runs again."""
        nodes = self.data.get("nodes", {})
        start = self.data.get("start_node")
        positions: dict[str, dict[str, int]] = {}
        visited: set[str] = set()
        frontier = [start] if start in nodes else []
        depth = 0
        while frontier:
            next_frontier: list[str] = []
            for row, node_id in enumerate(frontier):
                if node_id in visited or node_id not in nodes:
                    continue
                visited.add(node_id)
                positions[node_id] = {"x": depth * _AUTO_LAYOUT_X_STEP, "y": row * _AUTO_LAYOUT_Y_STEP}
                for choice in nodes[node_id].get("choices", []):
                    goto = choice.get("goto")
                    if goto and goto not in visited and goto not in next_frontier:
                        next_frontier.append(goto)
            frontier = next_frontier
            depth += 1

        # Anything unreachable from start_node (dead content, or a graph
        # authored out of order) still gets a position — nothing is left
        # unplaced on the canvas.
        for offset, node_id in enumerate(nid for nid in nodes if nid not in positions):
            positions[node_id] = {"x": 0, "y": (len(positions) + offset) * _AUTO_LAYOUT_Y_STEP}
        return positions

    def move_node_layout(self, node_id: str, x: int, y: int) -> None:
        self.layout[node_id] = {"x": x, "y": y}
        self.mark_dirty()

    # -- editing (direct in-memory JSON edits, doc §4) ------------------------

    def node_ids(self) -> list[str]:
        return list(self.data.get("nodes", {}).keys())

    def get_node(self, node_id: str) -> dict[str, Any]:
        return self.data["nodes"][node_id]

    def set_node_text(self, node_id: str, text: str) -> None:
        self.data["nodes"][node_id]["text"] = text
        self.mark_dirty()

    def set_start_node(self, node_id: str) -> None:
        self.data["start_node"] = node_id
        self.mark_dirty()

    def add_node(self, node_id: str, text: str = "") -> None:
        self.data.setdefault("nodes", {})[node_id] = {"text": text, "choices": []}
        self.layout[node_id] = {"x": 0, "y": len(self.layout) * _AUTO_LAYOUT_Y_STEP}
        self.mark_dirty()

    def remove_node(self, node_id: str) -> None:
        self.data.get("nodes", {}).pop(node_id, None)
        self.layout.pop(node_id, None)
        self.mark_dirty()

    def add_choice(
        self,
        node_id: str,
        text: str,
        goto: str | None = None,
        action: str | None = None,
        shop_id: str | None = None,
        condition: str | None = None,
    ) -> int:
        """Add a choice to ``nodes[node_id].choices`` (doc §4) —
        ``goto``/``action`` are mutually exclusive per 11's schema; this
        does not enforce that beyond accepting whichever the caller
        passes (a real inspector UI is expected to only show one at a
        time). Returns the new choice's index."""
        node = self.data["nodes"][node_id]
        choice: dict[str, Any] = {"text": text}
        if goto is not None:
            choice["goto"] = goto
        if action is not None:
            choice["action"] = action
            if shop_id is not None:
                choice["shop_id"] = shop_id
        if condition is not None:
            choice["condition"] = condition
        node.setdefault("choices", []).append(choice)
        self.mark_dirty()
        return len(node["choices"]) - 1

    def remove_choice(self, node_id: str, index: int) -> None:
        del self.data["nodes"][node_id]["choices"][index]
        self.mark_dirty()

    def reorder_choices(self, node_id: str, new_order: list[int]) -> None:
        choices = self.data["nodes"][node_id]["choices"]
        self.data["nodes"][node_id]["choices"] = [choices[i] for i in new_order]
        self.mark_dirty()

    def set_choice_condition(self, node_id: str, index: int, condition: str | None) -> None:
        """``condition`` strings are edited as plain text here (doc §4) —
        no semantic validation of the expression grammar; a condition that
        fails to evaluate is `11`'s runtime concern (defaults to
        false/hidden), not something this editor blocks saving over."""
        choice = self.data["nodes"][node_id]["choices"][index]
        if condition is None:
            choice.pop("condition", None)
        else:
            choice["condition"] = condition
        self.mark_dirty()

    # -- canvas-facing view ----------------------------------------------------

    def edges(self) -> list[dict[str, Any]]:
        """One entry per ``choices[]`` — a ``goto`` renders as an arrow to
        another node; an ``action`` (e.g. ``open_shop``) renders as a
        distinct terminal marker instead, since it doesn't point at
        another node in this file (doc §4)."""
        result: list[dict[str, Any]] = []
        for node_id, node in self.data.get("nodes", {}).items():
            for index, choice in enumerate(node.get("choices", [])):
                if "goto" in choice:
                    result.append({"from": node_id, "choice_index": index, "kind": "goto", "to": choice["goto"]})
                elif "action" in choice:
                    result.append(
                        {
                            "from": node_id,
                            "choice_index": index,
                            "kind": "action",
                            "action": choice["action"],
                        }
                    )
        return result

    # -- persistence -----------------------------------------------------------

    def save(self) -> Path:
        """Write ``self.data`` back verbatim (doc §4) — no
        parse/regenerate step exists here (unlike the Visual Quest
        Editor), so this is a direct re-serialization of the exact
        in-memory dict every edit method above mutated in place. Also
        persists the layout sidecar (never read by the runtime)."""
        if self._loaded_path is None:
            raise ValueError("dialog_editor: no destination — load() or open_new() first")
        self._loaded_path.parent.mkdir(parents=True, exist_ok=True)
        self._loaded_path.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")
        self._save_layout()
        self.mark_saved()
        return self._loaded_path

    def _save_layout(self) -> None:
        if self._layout_path is None:
            return
        payload = {"schema_version": _LAYOUT_SCHEMA_VERSION, "node_positions": self.layout}
        self._layout_path.parent.mkdir(parents=True, exist_ok=True)
        self._layout_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    # -- mode protocol (13-editor-core-authoring.md §2.3) ----------------------

    def on_activate(self) -> None:
        pass

    def on_deactivate(self) -> None:
        pass

    def handle_event(self, event: Any) -> None:
        pass

    def update(self, dt: float) -> None:
        pass

    def draw(self, surface: Any) -> None:
        pass
