"""Visual Quest Editor (``docs/components/14-editor-visual-quest-dialog.md``
§1.1/§2) — the hard half of this component: a node-graph canvas over
`10-lua-scripting-layer.md`'s `-- @node`/`-- @endnode` marker convention,
where **the generated `.lua` file is the one and only source of truth**
(doc §1.1/§6). There is no separate "quest data" representation that could
drift out of sync with it; the in-memory node graph built by
:meth:`QuestEditorMode.load`/:meth:`QuestEditorMode.add_node` is rebuilt
from the file itself on every load, and :meth:`QuestEditorMode.save`
writes nothing the graph didn't already know how to regenerate.

Two module-level free functions carry the actual mechanism so they can be
unit-tested without a live project (doc §8's unit-test list):

- :func:`parse_lua` — the file → node-graph pass (doc §2.2), a
  **line-based scan**, deliberately not a Lua AST parse.
- :func:`generate_lua` — the node-graph → file pass (doc §2.3), the exact
  inverse, and simpler: no linking step, no edge bookkeeping — a node's
  body is already complete, self-contained Lua.

Round-trip fidelity (doc §2.2's "recognized vs. Custom Block" decision,
restated): a marked block is rendered as a normal, editable node **only**
if its `type` resolves to a currently-loaded snippet **and** re-
instantiating that snippet's `lua_template` with the block's own captured
`@params` reproduces the captured body (after whitespace normalization).
Anything else — an unknown `type`, a deleted snippet, a hand-tweaked body
that no longer matches its own declared params — demotes to an opaque grey
**Custom Block** whose payload is the entire original block, markers
included, byte-for-byte. This is the single mechanism this whole doc
exists to get right (doc §6): silently dropping or mangling anything here
is a correctness bug, not a polish item.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union

from editor.modes import EditorContext, SaveAffordanceMixin, TooltipMixin

logger = logging.getLogger(__name__)

# -- the marker format (10-lua-scripting-layer.md §5.1, restated as this --
# -- component's parsing target per doc §2.1) ------------------------------

_NODE_OPEN_RE = re.compile(r'^-- @node id="(.+)" type="(.+)" x=(-?\d+) y=(-?\d+)$')
_PARAMS_RE = re.compile(r"^-- @params (.*)$")
_ENDNODE_LINE = "-- @endnode"

#: A single ``key=value`` token: a quoted string (backslash-escapes
#: honored, embedded spaces allowed), a bare int/float, or a bare
#: true/false (doc §2.2 step 3 / §8's tokenizing unit tests).
_PARAM_TOKEN_RE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|-?\d+\.\d+|-?\d+|true|false)')

_TOOLTIPS = {
    "palette": "Drag a snippet onto the canvas to add a node",
    "view_edit_script": "Open the generated .lua file directly for hand editing",
    "save_button": "Generate and save the quest script (Ctrl+S)",
    "custom_block": "Unrecognized or hand-edited Lua, preserved verbatim — not editable here",
    "node_inspector": "Edit this node's parameters",
    "undo_button": "Undo the last node/param/layout change",
}


# -- node-graph representation ----------------------------------------------


@dataclass
class QuestNode:
    """A recognized, editable node (doc §2.2's "Recognized" outcome).

    ``raw_body`` is the last-known regenerated/captured interior body text
    (markers excluded) — kept around purely so :func:`generate_lua` has a
    last-known-good fallback if this node's ``type`` stops resolving
    between load and save (e.g. a snippet was deleted mid-session); it is
    never itself compared against on the *next* load — that comparison
    always re-derives a fresh body from ``params`` (doc §2.2 step 5).
    """

    node_id: str
    type: str  # "<category>:<snippet_id>"
    x: int
    y: int
    params: dict[str, Any] = field(default_factory=dict)
    raw_body: str = ""


@dataclass
class CustomBlock:
    """An opaque, inert-to-the-editor's-own-logic block (doc §2.2's
    "Unrecognized" outcome, and §2.2 step 6's stray/malformed text).

    ``raw_text`` is the *entire* payload to re-emit verbatim on generation
    — for a demoted marked block this includes its own `@node`/`@endnode`
    markers; for stray text or a malformed-marker tail it's whatever
    surrounding text was captured. ``node_id``/``x``/``y`` are populated
    only when derivable from a (possibly unrecognized-type) marker line —
    informational for the canvas only, never load-bearing for generation.
    """

    raw_text: str
    node_id: str | None = None
    x: int | None = None
    y: int | None = None


GraphBlock = Union[QuestNode, CustomBlock]


def _lua_literal(value: Any) -> Any:
    """The exact token ``.format()`` should splice into a ``lua_template``
    placeholder: Lua booleans are bare, lowercase ``true``/``false`` — not
    Python's ``True``/``False`` (which ``str.format`` would otherwise
    produce via ``bool.__str__``)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _format_param_token(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_params_line(params: dict[str, Any]) -> str:
    tokens = " ".join(f"{key}={_format_param_token(value)}" for key, value in params.items())
    return f"-- @params {tokens}"


def _tokenize_params(raw: str) -> dict[str, Any]:
    """Parse an ``@params`` line's ``key=value ...`` tokens (doc §2.2 step
    3). An empty/blank line yields an empty dict — valid for a
    parameterless snippet."""
    params: dict[str, Any] = {}
    for match in _PARAM_TOKEN_RE.finditer(raw):
        key, token = match.groups()
        value: Any
        if token.startswith('"'):
            value = token[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        elif token == "true":
            value = True
        elif token == "false":
            value = False
        elif "." in token:
            value = float(token)
        else:
            value = int(token)
        params[key] = value
    return params


def _trim_blank_boundary(lines: list[str]) -> list[str]:
    start, end = 0, len(lines)
    while start < end and lines[start].strip() == "":
        start += 1
    while end > start and lines[end - 1].strip() == "":
        end -= 1
    return lines[start:end]


def _normalize_body(text: str) -> str:
    """Whitespace normalization for the "does the body match what
    regeneration would produce" comparison (doc §2.2 step 5) — trailing
    per-line whitespace and leading/trailing blank lines are insignificant;
    everything else must match exactly."""
    lines = [ln.rstrip() for ln in text.split("\n")]
    return "\n".join(_trim_blank_boundary(lines))


def instantiate_template(snippet: dict[str, Any], node_id: str, x: int, y: int, params: dict[str, Any]) -> str:
    """The **one** template-instantiation code path (doc §2.2's closing
    note), shared between "generate a brand-new node" (:func:`generate_lua`)
    and "does this loaded block's body match what its own declared type +
    params would regenerate" (:func:`parse_lua`'s recognition check)."""
    fmt_kwargs = {key: _lua_literal(value) for key, value in params.items()}
    return snippet["lua_template"].format(id=node_id, x=x, y=y, **fmt_kwargs)


def extract_body(full_block_text: str) -> str:
    """Given one complete ``-- @node ... -- @endnode`` block (as produced
    by :func:`instantiate_template`, so its shape is already known-good),
    return the interior body text — everything between the marker line(s)
    and ``-- @endnode``, matching exactly what :func:`parse_lua` captures
    as a loaded block's ``raw_body`` (doc §2.2 step 4)."""
    lines = full_block_text.split("\n")
    start = 1
    if start < len(lines) and _PARAMS_RE.match(lines[start]):
        start += 1
    end = len(lines)
    for index in range(len(lines) - 1, -1, -1):
        if lines[index] == _ENDNODE_LINE:
            end = index
            break
    return "\n".join(lines[start:end])


def _fill_defaults(snippet: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    filled: dict[str, Any] = {}
    for param_schema in snippet.get("params", []):
        name = param_schema["name"]
        filled[name] = params[name] if name in params else param_schema.get("default")
    for key, value in params.items():
        filled.setdefault(key, value)
    return filled


def _recognize_or_demote(
    node_id: str,
    node_type: str,
    x: int,
    y: int,
    params: dict[str, Any],
    raw_body: str,
    original_block_lines: list[str],
    snippets_by_type: dict[str, dict[str, Any]],
) -> GraphBlock:
    snippet = snippets_by_type.get(node_type)
    if snippet is None:
        # type doesn't resolve (never existed, or renamed/deleted since
        # generation) — doc §2.2 step 5's "Unrecognized" outcome.
        return CustomBlock(raw_text="\n".join(original_block_lines), node_id=node_id, x=x, y=y)

    full_params = _fill_defaults(snippet, params)
    try:
        regenerated_body = extract_body(instantiate_template(snippet, node_id, x, y, full_params))
    except (KeyError, IndexError):
        return CustomBlock(raw_text="\n".join(original_block_lines), node_id=node_id, x=x, y=y)

    if _normalize_body(regenerated_body) != _normalize_body(raw_body):
        # Hand-tweaked body that no longer matches what its own declared
        # type+params would regenerate — demoted, never silently snapped
        # back to the template's version (doc §2.2's closing rationale).
        return CustomBlock(raw_text="\n".join(original_block_lines), node_id=node_id, x=x, y=y)

    return QuestNode(node_id=node_id, type=node_type, x=x, y=y, params=full_params, raw_body=raw_body)


def parse_lua(text: str, snippets_by_type: dict[str, dict[str, Any]]) -> list[GraphBlock]:
    """The Lua → node-graph pass (doc §2.2). A line-based scan, not a Lua
    AST parse — see module docstring. Never raises on a hand-mangled file:
    a malformed/partial marker (missing ``@endnode``, non-numeric ``x``/
    ``y``) degrades to "everything from here to EOF is one Custom Block"
    (doc §8's unit-test requirement), the same fail-toward-preserving-bytes
    posture as every other branch here.
    """
    lines = text.split("\n")
    n = len(lines)
    blocks: list[GraphBlock] = []
    stray: list[str] = []

    def flush_stray() -> None:
        nonlocal stray
        trimmed = _trim_blank_boundary(stray)
        if trimmed:
            blocks.append(CustomBlock(raw_text="\n".join(trimmed)))
        stray = []

    i = 0
    while i < n:
        line = lines[i]
        match = _NODE_OPEN_RE.match(line)
        if match:
            flush_stray()
            node_id, node_type, x_str, y_str = match.groups()
            x, y = int(x_str), int(y_str)
            block_start = i
            j = i + 1
            params: dict[str, Any] = {}
            if j < n:
                params_match = _PARAMS_RE.match(lines[j])
                if params_match:
                    params = _tokenize_params(params_match.group(1))
                    j += 1
            body_lines: list[str] = []
            found_end = False
            while j < n:
                if lines[j] == _ENDNODE_LINE:
                    found_end = True
                    break
                body_lines.append(lines[j])
                j += 1
            if not found_end:
                # Missing @endnode: degrade the whole remainder of the
                # file to one Custom Block rather than guessing a boundary.
                blocks.append(CustomBlock(raw_text="\n".join(lines[block_start:]), node_id=node_id, x=x, y=y))
                i = n
                break
            blocks.append(
                _recognize_or_demote(
                    node_id,
                    node_type,
                    x,
                    y,
                    params,
                    "\n".join(body_lines),
                    original_block_lines=lines[block_start : j + 1],
                    snippets_by_type=snippets_by_type,
                )
            )
            i = j + 1
            continue

        if line.startswith("-- @node"):
            # A "-- @node" line that didn't fully match the marker regex
            # (e.g. non-numeric x/y) — malformed. Degrade the rest of the
            # file to one Custom Block (doc §8).
            blocks.append(CustomBlock(raw_text="\n".join(lines[i:])))
            i = n
            break

        stray.append(line)
        i += 1

    flush_stray()
    return blocks


def _primary_call_name(text: str) -> str | None:
    match = re.search(r"engine\.\w+", text)
    return match.group(0) if match else None


def infer_edges(graph: list[GraphBlock]) -> list[tuple[str, str]]:
    """Heuristic, display-only edges (doc §2.4) — re-inferred fresh on
    every call from node bodies, never stored, **never** consumed by
    :func:`generate_lua`. A `trigger` node's body is scanned (still a
    textual, line-based check — not a parser) for another node's primary
    `engine.*` call name. A miss just means that node renders unconnected
    on the canvas — a cosmetic degradation, never a data-loss risk, since
    generation doesn't need edges at all (doc §2.4/§9)."""
    edges: list[tuple[str, str]] = []
    triggers = [b for b in graph if isinstance(b, QuestNode) and b.type.startswith("trigger:")]
    others = [b for b in graph if isinstance(b, QuestNode) and not b.type.startswith("trigger:")]
    for trigger in triggers:
        for other in others:
            call_name = _primary_call_name(other.raw_body)
            if call_name and call_name in trigger.raw_body:
                edges.append((trigger.node_id, other.node_id))
    return edges


def _manual_block_text(node: QuestNode) -> str:
    """Best-effort re-emission for a node whose ``type`` no longer
    resolves at *generation* time (the snippet was deleted mid-session,
    after the node was added/loaded) — logs once and falls back to the
    last-known body rather than raising, per CONTRACTS.md §2.7's "absence
    = zero cost" ethos applied to this authoring tool."""
    header = f'-- @node id="{node.node_id}" type="{node.type}" x={node.x} y={node.y}'
    parts = [header]
    if node.params:
        parts.append(_render_params_line(node.params))
    parts.append(node.raw_body)
    parts.append(_ENDNODE_LINE)
    return "\n".join(parts)


def _block_text(block: GraphBlock, snippets_by_type: dict[str, dict[str, Any]]) -> str:
    if isinstance(block, CustomBlock):
        return block.raw_text
    snippet = snippets_by_type.get(block.type)
    if snippet is None:
        logger.warning(
            "quest_editor: snippet %r no longer exists; re-emitting last-known body for node %r",
            block.type,
            block.node_id,
        )
        return _manual_block_text(block)
    return instantiate_template(snippet, block.node_id, block.x, block.y, block.params)


def generate_lua(graph: list[GraphBlock], snippets_by_type: dict[str, dict[str, Any]]) -> str:
    """The node-graph → file pass (doc §2.3): walk the graph in its
    **stored order** (never re-sorted by canvas position or topology, so a
    pure-layout drag doesn't reorder unrelated blocks), emit each block's
    text, concatenate with a single blank line between blocks. This is the
    entire generator — there is no additional linking step; a node's body
    is already complete, self-contained Lua (doc §2.3)."""
    block_texts = [_block_text(block, snippets_by_type) for block in graph]
    if not block_texts:
        return ""
    return "\n\n".join(block_texts) + "\n"


# -- the mode itself ----------------------------------------------------------


class QuestEditorMode(TooltipMixin, SaveAffordanceMixin):
    """Registers as a tab per `13-editor-core-authoring.md`'s mode
    interface (doc §3) — ``handle_event``/``update``/``draw``/
    ``on_activate``/``on_deactivate`` are thin, since this mode's real
    surface is the node-graph API below, exercised directly by the
    integration test (CONTRACTS.md §7.3)."""

    def __init__(self, context: EditorContext, snippets_path: Path | None = None) -> None:
        self._context = context
        self._snippets_path_override = snippets_path
        self.tooltips = dict(_TOOLTIPS)
        self.dirty = False
        self.save_button_rect: tuple[int, int, int, int] = (0, 0, 120, 24)

        self._graph: list[GraphBlock] = []
        self._loaded_path: Path | None = None
        self._undo_stack: list[list[GraphBlock]] = []
        self._next_id_counter = 1

    # -- palette (doc §7's "no code change per new snippet" DoD item) -------

    def _snippets_path(self) -> Path:
        if self._snippets_path_override is not None:
            return self._snippets_path_override
        project = self._context.project()
        if project is None:
            raise RuntimeError("quest_editor: no active project")
        return project.data_path("scripts", "snippets.json")

    def load_snippets_config(self) -> dict[str, Any]:
        """Read straight off disk on every call — no cache — so a
        snippet-file edit (a test fixture change, or a live designer
        edit) is picked up with zero code change (doc §7)."""
        path = self._snippets_path()
        if not path.exists():
            return {"schema_version": 1, "categories": [], "snippets": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def _snippets_by_id(self) -> dict[str, dict[str, Any]]:
        return {snippet["id"]: snippet for snippet in self.load_snippets_config().get("snippets", [])}

    def _snippets_by_type(self) -> dict[str, dict[str, Any]]:
        return {
            f"{snippet['category']}:{snippet['id']}": snippet
            for snippet in self.load_snippets_config().get("snippets", [])
        }

    def palette(self) -> dict[str, list[dict[str, Any]]]:
        """Category -> ordered list of snippet dicts, grouped by the fixed
        category vocabulary (doc §1.1), sourced live from
        ``data/scripts/snippets.json``."""
        config = self.load_snippets_config()
        grouped: dict[str, list[dict[str, Any]]] = {category: [] for category in config.get("categories", [])}
        for snippet in config.get("snippets", []):
            grouped.setdefault(snippet["category"], []).append(snippet)
        return grouped

    # -- graph mutation ----------------------------------------------------

    def _push_undo(self) -> None:
        self._undo_stack.append(copy.deepcopy(self._graph))

    def undo(self) -> bool:
        """Minimal undo stack (doc §6's usability budget item)."""
        if not self._undo_stack:
            return False
        self._graph = self._undo_stack.pop()
        self.mark_dirty()
        return True

    def _existing_ids(self) -> set[str]:
        return {block.node_id for block in self._graph if getattr(block, "node_id", None)}

    def _new_node_id(self) -> str:
        existing = self._existing_ids()
        while True:
            candidate = f"n{self._next_id_counter}"
            self._next_id_counter += 1
            if candidate not in existing:
                return candidate

    def add_node(self, snippet_id: str, x: int = 0, y: int = 0, node_id: str | None = None) -> str:
        """Drag-a-snippet-onto-the-canvas entry point (doc §7's DoD #2).
        Params default from the snippet's own declared defaults."""
        snippet = self._snippets_by_id().get(snippet_id)
        if snippet is None:
            raise ValueError(f"quest_editor: unknown snippet id {snippet_id!r}")
        self._push_undo()
        assigned_id = node_id or self._new_node_id()
        params = {p["name"]: p.get("default") for p in snippet.get("params", [])}
        node_type = f"{snippet['category']}:{snippet['id']}"
        raw_body = extract_body(instantiate_template(snippet, assigned_id, x, y, params))
        self._graph.append(QuestNode(node_id=assigned_id, type=node_type, x=x, y=y, params=params, raw_body=raw_body))
        self.mark_dirty()
        return assigned_id

    def _find_node(self, node_id: str) -> QuestNode:
        for block in self._graph:
            if isinstance(block, QuestNode) and block.node_id == node_id:
                return block
        raise KeyError(f"quest_editor: no such node {node_id!r}")

    def set_param(self, node_id: str, name: str, value: Any) -> None:
        """The inspector panel's entry point (doc §7's DoD #2/#6)."""
        node = self._find_node(node_id)
        self._push_undo()
        node.params[name] = value
        snippet = self._snippets_by_type().get(node.type)
        if snippet is not None:
            node.raw_body = extract_body(instantiate_template(snippet, node.node_id, node.x, node.y, node.params))
        self.mark_dirty()

    def move_node(self, node_id: str, x: int, y: int) -> None:
        """Canvas drag — layout only. Does not touch ``params``/
        ``raw_body`` (doc §2.3's "an unrelated pure-layout drag doesn't
        produce a large, noisy diff")."""
        node = self._find_node(node_id)
        self._push_undo()
        node.x, node.y = x, y
        self.mark_dirty()

    def remove_node(self, node_id: str) -> None:
        self._push_undo()
        self._graph = [b for b in self._graph if getattr(b, "node_id", None) != node_id]
        self.mark_dirty()

    # -- canvas-facing view --------------------------------------------------

    def nodes(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for block in self._graph:
            if isinstance(block, QuestNode):
                result.append(
                    {
                        "kind": "node",
                        "id": block.node_id,
                        "type": block.type,
                        "x": block.x,
                        "y": block.y,
                        "params": dict(block.params),
                    }
                )
            else:
                result.append(
                    {
                        "kind": "custom_block",
                        "id": block.node_id,
                        "x": block.x,
                        "y": block.y,
                        "raw_text": block.raw_text,
                    }
                )
        return result

    def edges(self) -> list[tuple[str, str]]:
        return infer_edges(self._graph)

    # -- generation / persistence -------------------------------------------

    def generate_lua(self) -> str:
        return generate_lua(self._graph, self._snippets_by_type())

    def view_script_text(self) -> str:
        """The "View/Edit Script" escape hatch's read side (doc §1.1)."""
        return self.generate_lua()

    def save(self, path: Path | None = None) -> Path:
        dest = path or self._loaded_path
        if dest is None:
            raise ValueError("quest_editor: no destination path — pass path= or load() a file first")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(self.generate_lua(), encoding="utf-8")
        self._loaded_path = dest
        self.mark_saved()
        return dest

    def save_hand_edited_text(self, text: str, path: Path | None = None) -> Path:
        """The "View/Edit Script" escape hatch's save side (doc §1.1): the
        text pane's raw content is written verbatim, bypassing
        :meth:`generate_lua` entirely — the next :meth:`load` re-parses it
        exactly like any other file that landed on disk by hand."""
        dest = path or self._loaded_path
        if dest is None:
            raise ValueError("quest_editor: no destination path — pass path= or load() a file first")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        self._loaded_path = dest
        self.mark_saved()
        return dest

    def load(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        self._graph = parse_lua(text, self._snippets_by_type())
        self._loaded_path = path
        self._undo_stack = []
        self.mark_saved()

    def new_quest(self) -> None:
        self._graph = []
        self._loaded_path = None
        self._undo_stack = []
        self.mark_saved()

    # -- mode protocol (13-editor-core-authoring.md §2.3) --------------------

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


def register_modes(editor: Any) -> tuple["QuestEditorMode", "DialogEditorMode"]:
    """Register both of this component's modes into an already-constructed
    ``editor.editor.Editor`` (doc §3) — through its **public**
    ``register_mode``/``project``/``navigate_to``/``sprite_cache`` surface
    only, exactly mirroring how ``Editor.__init__`` builds the
    ``EditorContext`` its own six built-in modes share
    (`13-editor-core-authoring.md` §2.2). Zero edits to ``editor.py``
    itself — this is the concrete proof the seam that doc's §2.5 describes
    actually works for an "added after the fact" component.

    F5/F6 are free: 13's own six built-ins only claim F1–F4 (Sprite
    Manager and Audio tab register with no hotkey at all), matching this
    doc's §3 suggestion exactly.

    Call this once after constructing an ``Editor`` — e.g. from a future
    application launcher/`main.py`-equivalent for the editor process, which
    is not yet part of this codebase (`13-editor-core-authoring.md` never
    added one; it's out of this component's scope to add one either, so
    this function is the documented call a future launcher makes rather
    than a launcher itself). The integration test drives this exact
    function against a real ``Editor()`` instance.
    """
    from editor.modes import EditorContext
    from editor.modes.dialog_editor import DialogEditorMode

    context = EditorContext(
        get_project=lambda: editor.project,
        navigate_to=editor.navigate_to,
        sprite_cache=editor.sprite_cache,
    )
    quest_mode = QuestEditorMode(context)
    dialog_mode = DialogEditorMode(context)
    editor.register_mode("quest_editor", quest_mode, "Quest", "F5")
    editor.register_mode("dialog_editor", dialog_mode, "Dialog", "F6")
    return quest_mode, dialog_mode
