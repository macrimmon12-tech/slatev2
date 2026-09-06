"""Data Editor (``docs/components/13-editor-core-authoring.md`` §2.4) —
schema-aware forms **per content type**, explicitly not a raw JSON editor.

One form layout per namespace/content-type, driven entirely by a small
data-driven form-schema file (§5.2, this component's own format) so adding
a new content type's form is a data addition (drop a new JSON file under
``editor/form_schemas/``), not a new Python module — see
:meth:`DataEditorMode.list_content_types`.

A single reusable "effect array" sub-editor (:meth:`DataEditorMode.
effect_array_*`) backs every ``effects: [...]``-shaped field across every
content type (doc §2.4) — it is driven by the field schema's own
``columns`` list, never re-implemented per content type.

Open Question resolution (doc §10): the live JSON preview pane is
read-only for this component's initial scope (:meth:`json_preview`
returns a string; there is no ``load_from_json_text`` escape hatch here).
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

from editor.modes import EditorContext, SaveAffordanceMixin, TooltipMixin, table_column_headers

logger = logging.getLogger(__name__)

_FORM_SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "form_schemas"

#: Generic widget types this mode's form renderer understands (doc §5.2).
#: Adding a new content type whose fields only use these needs zero new
#: Python code — only a new form-schema JSON file.
KNOWN_WIDGETS = frozenset(
    {
        "text",
        "int",
        "float",
        "bool",
        "dropdown",
        "sprite_picker",
        "id_picker_or_typed",
        "effect_array",
    }
)

_TOOLTIPS = {
    "save_button": "Save this record to the project's data folder (Ctrl+S)",
    "content_type_selector": "Choose which content type's form to author",
    "new_record_button": "Start a new, blank record of the selected content type",
    "json_preview_pane": "Read-only preview of the exact JSON that will be written on Save",
    "effect_array_add_row": "Add a new entry to this list",
    "effect_array_remove_row": "Remove this entry from the list",
    "id_field_typed_input": "Type an ID directly, even one that doesn't exist yet",
}


def _get_dotted(record: dict[str, Any], dotted_key: str) -> Any:
    current: Any = record
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _set_dotted(record: dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    current = record
    for part in parts[:-1]:
        nxt = current.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            current[part] = nxt
        current = nxt
    current[parts[-1]] = value


def _default_for_widget(field_schema: dict[str, Any]) -> Any:
    if "default" in field_schema:
        return field_schema["default"]
    widget = field_schema.get("widget")
    if widget == "int":
        return 0
    if widget == "float":
        return 0.0
    if widget == "bool":
        return False
    if widget == "effect_array":
        return []
    if widget == "dropdown":
        options = field_schema.get("options")
        if isinstance(options, list) and options:
            return options[0]
        return None
    return ""


class DataEditorMode(TooltipMixin, SaveAffordanceMixin):
    """Schema-aware, form-schema-driven content editor. See module
    docstring and doc §2.4/§5.2."""

    def __init__(
        self,
        context: EditorContext,
        form_schemas_dir: Path | None = None,
    ) -> None:
        self._context = context
        self._schemas_dir = form_schemas_dir or _FORM_SCHEMAS_DIR
        self.tooltips = dict(_TOOLTIPS)

        self._schema_cache: dict[str, dict[str, Any]] = {}
        self.current_content_type: str | None = None
        self.current_record: dict[str, Any] = {}
        self.dirty = False
        self.save_button_rect: tuple[int, int, int, int] = (0, 0, 80, 24)
        self.last_saved_path: Path | None = None

    # -- EditorMode protocol --------------------------------------------------

    def on_activate(self) -> None:
        if self.current_content_type is None:
            content_types = self.list_content_types()
            if content_types:
                self.select_content_type(content_types[0])

    def on_deactivate(self) -> None:
        pass

    def handle_event(self, event: Any) -> None:
        pass

    def update(self, dt: float) -> None:
        pass

    def draw(self, surface: Any) -> None:
        pygame = _get_pygame()
        if pygame is None:
            return
        try:
            pygame.draw.rect(surface, (24, 24, 30), pygame.Rect(0, 0, 240, 24))
            pygame.draw.rect(surface, (60, 120, 60), pygame.Rect(*self.save_button_rect))
        except Exception:
            logger.warning("Data Editor draw failed", exc_info=True)

    # -- form-schema registry (data-driven, §5.2) ------------------------------

    def list_content_types(self) -> list[str]:
        if not self._schemas_dir.is_dir():
            return []
        return sorted(p.stem for p in self._schemas_dir.glob("*.json"))

    def load_schema(self, content_type: str) -> dict[str, Any]:
        cached = self._schema_cache.get(content_type)
        if cached is not None:
            return cached
        path = self._schemas_dir / f"{content_type}.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        self._schema_cache[content_type] = schema
        return schema

    def select_content_type(self, content_type: str) -> None:
        self.current_content_type = content_type
        self.new_record_for_editing(content_type)

    # -- record editing -------------------------------------------------------

    def new_record(self, content_type: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        schema = self.load_schema(content_type)
        record: dict[str, Any] = {}
        for field_schema in schema["fields"]:
            _set_dotted(record, field_schema["key"], _default_for_widget(field_schema))
        if overrides:
            for key, value in overrides.items():
                _set_dotted(record, key, value)
        return record

    def new_record_for_editing(
        self, content_type: str, overrides: dict[str, Any] | None = None
    ) -> None:
        self.current_content_type = content_type
        self.current_record = self.new_record(content_type, overrides)
        self.mark_dirty()

    def load_record_for_editing(self, content_type: str, record: dict[str, Any]) -> None:
        self.current_content_type = content_type
        self.current_record = copy.deepcopy(record)
        self.mark_saved()

    def get_field_value(self, key: str) -> Any:
        return _get_dotted(self.current_record, key)

    def set_field_value(self, key: str, value: Any) -> None:
        _set_dotted(self.current_record, key, value)
        self.mark_dirty()

    def json_preview(self) -> str:
        """Read-only live JSON preview (doc §2.4/§10) — the literal file
        that would be written on Save."""
        return json.dumps(self.current_record, indent=2, sort_keys=False)

    # -- effect-array sub-editor (reused across every effects-shaped field) --

    def effect_array_column_headers(self, field_schema: dict[str, Any]) -> list[str]:
        return table_column_headers(field_schema.get("columns", []))

    def effect_array_add_row(self, key: str) -> None:
        rows = _get_dotted(self.current_record, key)
        if not isinstance(rows, list):
            rows = []
        rows.append({})
        _set_dotted(self.current_record, key, rows)
        self.mark_dirty()

    def effect_array_remove_row(self, key: str, index: int) -> None:
        rows = _get_dotted(self.current_record, key)
        if isinstance(rows, list) and 0 <= index < len(rows):
            rows.pop(index)
            self.mark_dirty()

    def effect_array_set_cell(self, key: str, index: int, column_key: str, value: Any) -> None:
        rows = _get_dotted(self.current_record, key)
        if not isinstance(rows, list):
            rows = []
            _set_dotted(self.current_record, key, rows)
        while len(rows) <= index:
            rows.append({})
        rows[index][column_key] = value
        self.mark_dirty()

    # -- option resolution (dropdown / id_picker_or_typed) ---------------------

    def resolve_options(self, field_schema: dict[str, Any]) -> list[str]:
        """Literal ``options`` list, or live IDs from a registry namespace
        (``options_from_namespace``) — this is what lets a brand-new
        namespace populate a dropdown with zero changes to this mode's
        code (doc §5.2)."""
        namespace = field_schema.get("options_from_namespace")
        if namespace:
            registry = self._project_registry()
            if registry is not None:
                try:
                    return sorted(registry.all(namespace).keys())
                except KeyError:
                    return []
            return []
        options = field_schema.get("options")
        return list(options) if isinstance(options, list) else []

    def _project_registry(self):
        project = self._context.project()
        if project is None:
            return None
        from engine.core.registry import DataRegistry

        registry = DataRegistry()
        registry.load(project.data_path())
        return registry

    # -- save -------------------------------------------------------------

    def save_current_record(self) -> Path:
        """Write :attr:`current_record` to the project's data layout as a
        loose file (doc §2.4 — never into an archive)."""
        project = self._context.project()
        if project is None:
            raise RuntimeError("Data Editor has no active project to save into")
        if self.current_content_type is None:
            raise RuntimeError("Data Editor has no content type selected")

        schema = self.load_schema(self.current_content_type)
        namespace = schema["namespace"]
        record_id = self.get_field_value("id")
        if not record_id:
            raise ValueError("Record must have a non-empty 'id' before saving")

        namespace_dir = project.data_path(*_namespace_relative_dir(namespace))
        namespace_dir.mkdir(parents=True, exist_ok=True)
        dest = namespace_dir / f"{record_id}.json"
        dest.write_text(json.dumps(self.current_record, indent=2), encoding="utf-8")

        self.last_saved_path = dest
        self.mark_saved()
        return dest


def _namespace_relative_dir(namespace: str) -> tuple[str, ...]:
    """Directory (relative to ``data/``) a given namespace's records live
    under, per CONTRACTS.md §4. Only the namespaces this component's own
    form schemas actually target are needed here; anything else falls back
    to a same-named top-level directory, additive and harmless."""
    from engine.core.registry import NAMESPACE_TABLE

    spec = NAMESPACE_TABLE.get(namespace)
    if spec is not None:
        return tuple(spec.directory.split("/"))
    return (namespace,)


_pygame_module: Any = False


def _get_pygame() -> Any:
    global _pygame_module
    if _pygame_module is False:
        try:
            import pygame

            _pygame_module = pygame
        except ImportError:
            _pygame_module = None
    return _pygame_module
