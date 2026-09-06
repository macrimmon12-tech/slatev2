"""Sprite Manager (``docs/components/13-editor-core-authoring.md`` §2.6) —
a needed-sprite/has-sprite catalogue across every content type, with
import-and-link-back, plus the tile/autotile-variant view.

**The 16-slot wall-variant requirement is the direct fix for spec §11
lesson 3** (doc §2.6/§7): this module exposes exactly the 16 named slots
``wall_0``..``wall_15``, individually assignable, matching
``07-input-renderer-audio.md``'s ``wall_variants`` lookup contract (that
doc's §2.3/§5.1) key-for-key. This module never imports
``engine.render.autotile`` (editor code doesn't import engine gameplay/
render modules, per doc §1) — the 16 slot *names* are simply reproduced
here verbatim as the shared, documented contract string, not derived from
that module's code.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from editor.modes import EditorContext, SaveAffordanceMixin, TooltipMixin

logger = logging.getLogger(__name__)

#: The exact 16 named wall-variant slots (``07-input-renderer-audio.md``
#: §2.3/§5.1) — bitmask 0..15, keyed as ``wall_<bitmask>``. Do not collapse
#: this to a single generic "wall" slot under any circumstance (doc §2.6).
WALL_VARIANT_SLOTS = [f"wall_{i}" for i in range(16)]

_TOOLTIPS = {
    "gap_list": "Content records whose sprite_ref points at a file that doesn't exist yet",
    "import_button": "Import a sprite file and link it back to the selected record automatically",
    "wall_variant_grid": "All 16 wall_0..wall_15 slots — assign each independently, no single generic wall slot",
}


def _iter_sprite_refs(obj: Any, path: str = "") -> list[tuple[str, str]]:
    """Recursively find every ``sprite_ref`` field in ``obj`` (a parsed
    content JSON dict). Returns ``(dotted_path, value)`` pairs."""
    results: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            sub_path = f"{path}.{key}" if path else key
            if key == "sprite_ref" and isinstance(value, str) and value:
                results.append((sub_path, value))
            else:
                results.extend(_iter_sprite_refs(value, sub_path))
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            results.extend(_iter_sprite_refs(item, f"{path}[{index}]"))
    return results


def _set_dotted(record: dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    current = record
    for part in parts[:-1]:
        current = current[part]
    current[parts[-1]] = value


class SpriteGap:
    """One content record whose ``sprite_ref`` points at a file that
    doesn't exist under the project's ``assets/``."""

    def __init__(self, json_path: Path, field_path: str, sprite_ref: str) -> None:
        self.json_path = json_path
        self.field_path = field_path
        self.sprite_ref = sprite_ref

    def __repr__(self) -> str:  # pragma: no cover — debugging convenience
        return f"SpriteGap({self.json_path}, {self.field_path!r}, {self.sprite_ref!r})"


class SpriteManagerMode(TooltipMixin, SaveAffordanceMixin):
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

    # -- needed-sprite / has-sprite catalogue -----------------------------------

    def scan_gaps(self) -> list[SpriteGap]:
        """Scan every content JSON under the project's ``data/`` for
        ``sprite_ref`` fields, cross-reference against ``assets/``, and
        return the ones that don't resolve to a real file (doc §2.6)."""
        project = self._require_project()
        gaps: list[SpriteGap] = []
        data_dir = project.root / "data"
        if not data_dir.is_dir():
            return gaps

        for json_path in sorted(data_dir.rglob("*.json")):
            try:
                payload = json.loads(json_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            for field_path, sprite_ref in _iter_sprite_refs(payload):
                if not (project.assets_path(sprite_ref)).exists():
                    gaps.append(SpriteGap(json_path, field_path, sprite_ref))
        return gaps

    def import_and_link(
        self,
        source_file: Path,
        dest_relative: str,
        json_path: Path,
        field_path: str,
    ) -> Path:
        """Import ``source_file`` into the project's ``assets/`` at
        ``dest_relative`` and write the resulting ``sprite_ref`` onto
        ``json_path``'s ``field_path`` automatically — no manual "go edit
        the JSON" step (doc §2.6)."""
        project = self._require_project()
        dest = project.assets_path(dest_relative)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, dest)

        payload = json.loads(json_path.read_text(encoding="utf-8"))
        _set_dotted(payload, field_path, dest_relative)
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        self._context.sprite_cache.invalidate(dest)
        return dest

    # -- wall-variant (16-slot) view --------------------------------------------

    def load_theme(self, theme_relative_path: str) -> dict[str, Any]:
        project = self._require_project()
        path = project.assets_path(theme_relative_path)
        if not path.exists():
            return {"wall_variants": {}}
        return json.loads(path.read_text(encoding="utf-8"))

    def get_wall_variant(self, theme: dict[str, Any], slot_index: int) -> str | None:
        slot = self._slot_name(slot_index)
        return theme.get("wall_variants", {}).get(slot)

    def set_wall_variant(self, theme: dict[str, Any], slot_index: int, sprite_path: str) -> None:
        """Assign one of the 16 slots independently — doc §2.6/§8 requires
        assigning one to have zero effect on any other slot, which holds
        here structurally since each slot is its own dict key."""
        slot = self._slot_name(slot_index)
        theme.setdefault("wall_variants", {})[slot] = sprite_path
        self.mark_dirty()

    def _slot_name(self, slot_index: int) -> str:
        if not 0 <= slot_index <= 15:
            raise ValueError(f"wall variant slot index must be 0-15, got {slot_index}")
        return WALL_VARIANT_SLOTS[slot_index]

    def save_theme(self, theme: dict[str, Any], theme_relative_path: str) -> Path:
        project = self._require_project()
        path = project.assets_path(theme_relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(theme, indent=2), encoding="utf-8")
        self.mark_saved()
        return path

    # -- helpers ------------------------------------------------------------

    def _require_project(self):
        project = self._context.project()
        if project is None:
            raise RuntimeError("Sprite Manager has no active project")
        return project
