"""Audio tab (``docs/components/13-editor-core-authoring.md`` §2.6) — plain
file-manager import/organize for ``sfx/``/``music/`` under the project's
``assets/``, mirroring Sprite Manager's import pattern.

Open Question resolution (doc §10): ``audio_config.json``'s ``event_sounds``
link-back is included (:meth:`AudioTabMode.link_event`) since it's a small
addition on top of the required import/organize baseline, but it remains
optional to use — importing a file alone (:meth:`import_file`) never
requires a link-back call.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from editor.modes import EditorContext, SaveAffordanceMixin, TooltipMixin

logger = logging.getLogger(__name__)

_VALID_KINDS = frozenset({"sfx", "music"})

_TOOLTIPS = {
    "import_button": "Import an audio file into this project's assets/sfx or assets/music",
    "link_back_button": "Bind the selected sound to an event in audio_config.json (optional)",
}


class AudioTabMode(TooltipMixin, SaveAffordanceMixin):
    def __init__(self, context: EditorContext) -> None:
        self._context = context
        self.tooltips = dict(_TOOLTIPS)
        self.dirty = False
        self.save_button_rect: tuple[int, int, int, int] = (0, 0, 80, 24)

    # -- EditorMode protocol --------------------------------------------------

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

    # -- import / organize ----------------------------------------------------

    def list_files(self, kind: str) -> list[Path]:
        self._require_kind(kind)
        project = self._require_project()
        directory = project.assets_path(kind)
        if not directory.is_dir():
            return []
        return sorted(directory.rglob("*"))

    def import_file(self, source_file: Path, kind: str, dest_name: str | None = None) -> Path:
        """Import ``source_file`` into the project's ``assets/sfx`` or
        ``assets/music`` (doc §2.6). Returns the destination path."""
        self._require_kind(kind)
        project = self._require_project()
        source_file = Path(source_file)
        dest_name = dest_name or source_file.name
        dest = project.assets_path(kind, dest_name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, dest)
        self.mark_dirty()
        return dest

    def organize(self, kind: str, sub_folder: str) -> Path:
        """Create (and return) a named sub-folder under ``assets/<kind>/``
        for the designer to file sounds into."""
        self._require_kind(kind)
        project = self._require_project()
        directory = project.assets_path(kind, sub_folder)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    # -- optional audio_config.json link-back -----------------------------------

    def link_event(self, event_name: str, sound_relative_path: str) -> Path:
        """Bind ``event_name`` -> ``sound_relative_path`` in the project's
        ``audio_config.json`` (``event_sounds.<event_name>.default`` per
        ``07-input-renderer-audio.md`` §5.2's schema). Optional per doc
        §2.6/§10 — plain import/organize never requires calling this."""
        project = self._require_project()
        path = project.data_path("config", "audio_config.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            config = json.loads(path.read_text(encoding="utf-8"))
        else:
            config = {"schema_version": 1, "event_sounds": {}, "music": {}}
        config.setdefault("event_sounds", {}).setdefault(event_name, {})["default"] = sound_relative_path
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        self.mark_saved()
        return path

    # -- helpers ------------------------------------------------------------

    def _require_kind(self, kind: str) -> None:
        if kind not in _VALID_KINDS:
            raise ValueError(f"invalid audio kind {kind!r}; expected one of {sorted(_VALID_KINDS)}")

    def _require_project(self):
        project = self._context.project()
        if project is None:
            raise RuntimeError("Audio tab has no active project")
        return project
