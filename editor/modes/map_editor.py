"""Map Editor (``docs/components/13-editor-core-authoring.md`` §2.4) —
paint tiles, place entities/NPCs/items/transitions, author vaults and
campaigns, and surface non-blocking validation warnings.

Map JSON shape (this component's own authoring format — the editor never
imports ``engine.systems.worldgen``, per doc §1's "editor never imports
engine gameplay systems" boundary) mirrors ``tilemap_to_dict``'s field
names exactly (``x``/``y``/``walkable``/``room_id``/``sprite_ref``/
``is_lit_room`` per tile, plus ``rooms``/``stairs_down``/``stairs_up``) so
a handcrafted map is structurally interchangeable with a procedurally
generated one at the JSON level, without a Python import dependency
between the two components. This module adds its own authoring-only
fields (``id``, ``display_name``, ``entities``, ``transitions``) on top.

Vaults are authored on the identical paint/place code path (doc §2.4) —
:meth:`MapEditorMode.save_as_vault` is the only difference from
:meth:`save_map`: same in-memory map, different destination directory and
namespace.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from editor.modes import EditorContext, SaveAffordanceMixin, TooltipMixin

logger = logging.getLogger(__name__)

#: The exact-text warning doc §2.4/§7 requires, verbatim, so it's
#: greppable/testable and matches what `06-worldgen-campaign.md`'s
#: noise-attenuation code actually falls back to. Do not paraphrase.
ROOM_ID_MISSING_WARNING = (
    "this map has no room_id data — noise attenuation will use radius only"
)

#: namespace -> Data Editor content-type, for the "place an entity with no
#: JSON definition yet -> jump to Data Editor pre-filled" context link
#: (doc §2.7). Only the namespaces Map Editor actually places content from
#: need an entry here.
_NAMESPACE_TO_CONTENT_TYPE = {
    "entities": "monster",
    "npcs": "monster",
    "items": "item",
    "spells": "spell",
}

_TOOLTIPS = {
    "save_button": "Save this map to the project's data folder (Ctrl+S)",
    "save_as_vault_button": "Save this map as a vault under data/maps/vaults/",
    "palette": "Right-click a tile here to edit its sprite in the Sprite Editor",
    "entity_palette": "Pick an entity/NPC/item to place; unknown IDs open Data Editor",
    "warnings_panel": "Non-blocking authoring warnings — nothing here stops you from saving",
    "campaign_level_list": "Drag to reorder levels; toggle Hub to mark a level as a hub floor",
}


def _new_tiles(width: int, height: int) -> list[dict[str, Any]]:
    return [
        {
            "x": x,
            "y": y,
            "walkable": True,
            "room_id": None,
            "sprite_ref": None,
            "is_lit_room": False,
        }
        for y in range(height)
        for x in range(width)
    ]


class MapEditorMode(TooltipMixin, SaveAffordanceMixin):
    def __init__(self, context: EditorContext) -> None:
        self._context = context
        self.tooltips = dict(_TOOLTIPS)

        self.current_map: dict[str, Any] = self._blank_map("untitled")
        self._tiles_by_pos: dict[tuple[int, int], dict[str, Any]] = {}
        self._reindex_tiles()

        self.current_campaign: dict[str, Any] | None = None
        self.dirty = False
        self.save_button_rect: tuple[int, int, int, int] = (0, 0, 80, 24)
        self.save_as_vault_button_rect: tuple[int, int, int, int] = (90, 0, 120, 24)

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
        pygame = _get_pygame()
        if pygame is None:
            return
        try:
            pygame.draw.rect(surface, (60, 120, 60), pygame.Rect(*self.save_button_rect))
            pygame.draw.rect(surface, (60, 90, 120), pygame.Rect(*self.save_as_vault_button_rect))
        except Exception:
            logger.warning("Map Editor draw failed", exc_info=True)

    # -- map construction / loading -------------------------------------------

    def _blank_map(self, map_id: str, width: int = 40, height: int = 25) -> dict[str, Any]:
        return {
            "id": map_id,
            "display_name": map_id,
            "width": width,
            "height": height,
            "tiles": _new_tiles(width, height),
            "rooms": {},
            "stairs_down": None,
            "stairs_up": None,
            "entities": [],
            "transitions": [],
        }

    def new_blank_map(self, map_id: str, width: int = 40, height: int = 25) -> None:
        self.current_map = self._blank_map(map_id, width, height)
        self._reindex_tiles()
        self.mark_dirty()

    def _reindex_tiles(self) -> None:
        self._tiles_by_pos = {(t["x"], t["y"]): t for t in self.current_map["tiles"]}

    def load_map(self, map_id: str) -> None:
        project = self._require_project()
        path = project.data_path("maps", f"{map_id}.json")
        self.current_map = json.loads(path.read_text(encoding="utf-8"))
        self._reindex_tiles()
        self.mark_saved()

    def load_vault(self, vault_id: str) -> None:
        project = self._require_project()
        path = project.data_path("maps", "vaults", f"{vault_id}.json")
        self.current_map = json.loads(path.read_text(encoding="utf-8"))
        self._reindex_tiles()
        self.mark_saved()

    # -- painting -----------------------------------------------------------

    def paint_tile(
        self,
        x: int,
        y: int,
        walkable: bool,
        sprite_ref: str | None = None,
        room_id: str | None = None,
    ) -> None:
        tile = self._tiles_by_pos.get((x, y))
        if tile is None:
            tile = {"x": x, "y": y, "walkable": walkable, "room_id": room_id,
                     "sprite_ref": sprite_ref, "is_lit_room": False}
            self.current_map["tiles"].append(tile)
            self._tiles_by_pos[(x, y)] = tile
        else:
            tile["walkable"] = walkable
            tile["sprite_ref"] = sprite_ref
            tile["room_id"] = room_id
        self.mark_dirty()

    def get_tile(self, x: int, y: int) -> dict[str, Any] | None:
        return self._tiles_by_pos.get((x, y))

    # -- entity/NPC/item placement -------------------------------------------

    def place_entity(
        self,
        x: int,
        y: int,
        namespace: str,
        content_id: str,
        instance_id: str | None = None,
    ) -> str:
        """Place an entity referencing ``namespace``/``content_id`` at
        ``(x, y)``. If ``content_id`` doesn't exist yet in the project's
        registry, this is the §2.7 context link: jump to Data Editor with a
        new-record form pre-filled with what Map Editor already knows
        (content type, id guess) instead of silently placing a dangling
        reference."""
        instance_id = instance_id or f"{namespace}_{content_id}_{x}_{y}"
        entry = {
            "instance_id": instance_id,
            "namespace": namespace,
            "content_id": content_id,
            "position": [x, y],
        }
        self.current_map.setdefault("entities", []).append(entry)
        self.mark_dirty()

        if not self._content_id_exists(namespace, content_id):
            content_type = _NAMESPACE_TO_CONTENT_TYPE.get(namespace, namespace)
            self._context.navigate_to(
                "data_editor",
                {
                    "content_type": content_type,
                    "new_record": {"id": content_id},
                },
            )
        return instance_id

    def remove_entity(self, instance_id: str) -> None:
        entities = self.current_map.get("entities", [])
        self.current_map["entities"] = [e for e in entities if e.get("instance_id") != instance_id]
        self.mark_dirty()

    def _content_id_exists(self, namespace: str, content_id: str) -> bool:
        project = self._context.project()
        if project is None:
            return True  # nothing to check against — don't false-positive a redirect
        try:
            from engine.core.registry import DataRegistry

            registry = DataRegistry()
            registry.load(project.data_path())
            return registry.get(namespace, content_id) is not None
        except Exception:
            logger.debug("Content-id existence check failed", exc_info=True)
            return True

    # -- context link: palette right-click -----------------------------------

    def on_palette_right_click(self, sprite_path: str) -> None:
        """Right-click a palette tile -> jump to Sprite Editor with that
        PNG already loaded (doc §2.7)."""
        self._context.navigate_to("sprite_editor", {"asset_path": sprite_path})

    # -- validation (doc §2.4, non-blocking) -----------------------------------

    def validate_map(self, map_data: dict[str, Any] | None = None) -> list[str]:
        """Non-blocking authoring warnings for ``map_data`` (defaults to
        :attr:`current_map`). Never raises, never blocks saving."""
        data = map_data if map_data is not None else self.current_map
        warnings: list[str] = []

        if not data.get("transitions") and not data.get("stairs_down"):
            warnings.append("No stairs found on this map.")

        if not data.get("completion_trigger"):
            warnings.append("No completion trigger found on this map.")

        seen_ids: set[str] = set()
        for entity in data.get("entities", []):
            instance_id = entity.get("instance_id")
            if instance_id in seen_ids:
                warnings.append(f"Duplicate entity instance_id: {instance_id!r}")
            seen_ids.add(instance_id)

        tiles = data.get("tiles", [])
        has_room_id = any(tile.get("room_id") for tile in tiles)
        if tiles and not has_room_id:
            warnings.append(ROOM_ID_MISSING_WARNING)

        return warnings

    # -- saving (map / vault) ------------------------------------------------

    def save_map(self) -> Path:
        project = self._require_project()
        dest_dir = project.data_path("maps")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{self.current_map['id']}.json"
        dest.write_text(json.dumps(self.current_map, indent=2), encoding="utf-8")
        self.mark_saved()
        return dest

    def save_as_vault(self, vault_id: str | None = None) -> Path:
        """Author a vault on the identical toolset (doc §2.4) — same
        in-memory map, saved under ``data/maps/vaults/`` instead of
        ``data/maps/``."""
        project = self._require_project()
        vault_id = vault_id or self.current_map["id"]
        vault_data = dict(self.current_map)
        vault_data["id"] = vault_id
        dest_dir = project.data_path("maps", "vaults")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{vault_id}.json"
        dest.write_text(json.dumps(vault_data, indent=2), encoding="utf-8")
        self.mark_saved()
        return dest

    # -- campaign authoring ---------------------------------------------------

    def new_campaign(self, campaign_id: str, display_name: str | None = None) -> None:
        self.current_campaign = {
            "id": campaign_id,
            "display_name": display_name or campaign_id,
            "levels": [],
        }

    def load_campaign(self, campaign_id: str) -> None:
        project = self._require_project()
        path = project.data_path("campaigns", f"{campaign_id}.json")
        self.current_campaign = json.loads(path.read_text(encoding="utf-8"))

    def campaign_add_level(self, level: dict[str, Any]) -> None:
        campaign = self._require_campaign()
        level = dict(level)
        level.setdefault("is_hub", False)
        campaign["levels"].append(level)

    def campaign_move_level(self, index: int, new_index: int) -> None:
        """Drag-reorder a level in the ordered level list (doc §2.4)."""
        campaign = self._require_campaign()
        levels = campaign["levels"]
        level = levels.pop(index)
        levels.insert(new_index, level)

    def campaign_set_hub(self, index: int, is_hub: bool) -> None:
        campaign = self._require_campaign()
        campaign["levels"][index]["is_hub"] = is_hub

    def save_campaign(self) -> Path:
        project = self._require_project()
        campaign = self._require_campaign()
        dest_dir = project.data_path("campaigns")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{campaign['id']}.json"
        dest.write_text(json.dumps(campaign, indent=2), encoding="utf-8")
        return dest

    def _require_campaign(self) -> dict[str, Any]:
        if self.current_campaign is None:
            raise RuntimeError("No campaign is currently loaded/being authored")
        return self.current_campaign

    def _require_project(self):
        project = self._context.project()
        if project is None:
            raise RuntimeError("Map Editor has no active project")
        return project


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
