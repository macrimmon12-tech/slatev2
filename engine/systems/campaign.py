"""CampaignSystem / FloorManager — ordered campaign level lists, depth
resolution independent of list position, hub floors, and floor transition
with snapshot/restore + auto-save. See
``docs/components/06-worldgen-campaign.md`` §2.3 for the full spec.

Two genuine gaps in §2.3's documented signatures this module had to
resolve on its own (flagged here and in the PR description rather than
silently guessed):

1. **Kicking off the very first floor.** ``load_campaign`` explicitly does
   *not* generate any floor ("generation happens lazily on first visit via
   FloorManager") but no documented method actually triggers that first
   visit — ``_on_stair_use`` only reacts to an already-in-progress run.
   This module adds :meth:`CampaignSystem.enter_level` (not in §2.3's
   table) as the one documented entry point for both bootstrapping the
   first floor and any explicit floor jump (a portal, a hub's stairs) —
   ``_on_stair_use`` is now a thin wrapper that resolves a target level id
   and calls it.
2. **"Active save slot" writer.** §2.3 says ``transition_to`` writes
   ``serialize_world``'s result "to the active save slot", but no function
   anywhere in CONTRACTS.md or ``00-foundation-core.md`` actually performs
   file I/O for a save slot — ``engine/core/save.py`` only ever returns a
   dict. ``FloorManager`` therefore calls ``serialize_world`` for real
   after every transition (exposed via ``last_save_data`` for
   inspection/testing) and forwards it to an optional injectable
   ``save_sink`` callable when one is wired, logging once (never raising)
   when it isn't — the same "absence = zero cost" shape as every other
   optional-dependency extension already established in this codebase
   (e.g. ``01``'s ``combat.set_data_registry``).
"""

from __future__ import annotations

import logging
import random
from typing import Any, Callable

from engine.core import save
from engine.core.ecs import World
from engine.core.events import EventBus
from engine.systems import worldgen
from engine.systems.ai import PlayerTagComponent

logger = logging.getLogger(__name__)

__all__ = ["CampaignSystem", "FloorManager", "set_data_registry", "validate_campaign_data"]

_data_registry: Any | None = None


def set_data_registry(registry: Any | None) -> None:
    """Registers the loaded ``DataRegistry`` so ``load_campaign`` resolves
    real campaign data. Pass ``None`` to clear (test teardown)."""
    global _data_registry
    _data_registry = registry


def validate_campaign_data(campaign: dict) -> list[str]:
    """Lightweight schema check for a campaign JSON document (§5.1).
    Lives here rather than ``engine/core/schemas/`` — see
    ``worldgen.validate_worldgen_config``'s docstring for the flagged
    CONTRACTS.md/00 ownership conflict this mirrors."""
    errors: list[str] = []
    levels = campaign.get("levels")
    if not isinstance(levels, list) or not levels:
        errors.append("levels must be a non-empty list")
        return errors
    seen_ids: set[str] = set()
    for i, level in enumerate(levels):
        for field_name in ("id", "depth", "width", "height"):
            if field_name not in level:
                errors.append(f"levels[{i}] missing required field {field_name!r}")
        level_id = level.get("id")
        if level_id in seen_ids:
            errors.append(f"duplicate level id {level_id!r}")
        elif level_id is not None:
            seen_ids.add(level_id)
        for ref in level.get("vaults", []):
            if ref.get("guaranteed") and "weight" in ref:
                errors.append(f"levels[{i}] vault {ref.get('vault_id')!r} has both 'guaranteed' and 'weight'")
    return errors


class FloorManager:
    """Owns floor transitions: snapshot the outgoing floor, generate-fresh
    or restore-from-snapshot the incoming one, emit ``floor_changed``, and
    auto-save unconditionally after every transition (§2.3)."""

    def __init__(
        self,
        world: World,
        event_bus: EventBus,
        *,
        rng: random.Random | None = None,
        save_sink: Callable[[dict], None] | None = None,
    ) -> None:
        self.world = world
        self.event_bus = event_bus
        self._rng = rng if rng is not None else random.Random()
        self._save_sink = save_sink
        self.spawned_uniques: set[str] = set()
        self.last_save_data: dict | None = None
        self._floors: dict[str, dict] = {}  # floor_id -> {"level_def", "snapshot"}
        self._current_floor_id: str | None = None
        self._warned_no_save_sink = False

    @property
    def current_floor_id(self) -> str | None:
        return self._current_floor_id

    def restore_current_floor_id(self, floor_id: str | None) -> None:
        """Load-game support (live game loop) — a loaded save's world
        state doesn't include this bookkeeping (it lives on
        ``FloorManager``, not in anything ``save.py`` serializes), so a
        loader restores it explicitly rather than reaching into
        ``_current_floor_id`` directly."""
        self._current_floor_id = floor_id

    def set_save_sink(self, save_sink: Callable[[dict], None] | None) -> None:
        """Wire (or clear) the save-sink after construction — needed
        because ``CampaignSystem`` builds its own default ``FloorManager``
        with no way to pass one in from outside at the same time (see
        this module's docstring, gap 2); a real save-to-disk caller
        (the live game loop) constructs everything else first, then wires
        this in via ``campaign_system.floor_manager.set_save_sink(...)``."""
        self._save_sink = save_sink
        self._warned_no_save_sink = False

    def transition_to(self, level_def: dict) -> None:
        floor_id = level_def["id"]

        from_depth = self._snapshot_current_floor()

        record = self._floors.get(floor_id)
        if record is None:
            worldgen.generate_floor(level_def, self.world, self._rng, event_bus=self.event_bus)
            self._floors[floor_id] = {"level_def": level_def, "snapshot": None}
        else:
            record["level_def"] = level_def
            snapshot = record.get("snapshot")
            if snapshot is not None:
                save.restore_floor_snapshot(self.world, snapshot)
                tilemap_data = snapshot.get("tilemap")
                if tilemap_data is not None:
                    worldgen.register_tilemap(floor_id, worldgen.tilemap_from_dict(tilemap_data))
                record["snapshot"] = None

        to_depth = level_def["depth"]
        worldgen.set_current_depth(self.world, to_depth)
        self._current_floor_id = floor_id

        self.event_bus.emit("floor_changed", {"from_depth": from_depth, "to_depth": to_depth})

        self._auto_save()

    def _snapshot_current_floor(self) -> int | None:
        """Snapshots the outgoing floor (non-player entities + tilemap) and
        clears them from ``world`` so the incoming floor starts from a
        clean slate. Returns the outgoing floor's depth (for
        ``floor_changed``'s ``from_depth``), or ``None`` on the very first
        transition."""
        if self._current_floor_id is None:
            return None

        outgoing_id = self._current_floor_id
        outgoing_record = self._floors.get(outgoing_id)
        outgoing_depth = outgoing_record["level_def"]["depth"] if outgoing_record else None

        player_ids = {row[0] for row in self.world.query(PlayerTagComponent)}
        entity_ids = [eid for eid in self.world.entities() if eid not in player_ids]

        tilemap = worldgen.get_tilemap(outgoing_id)
        tilemap_data = worldgen.tilemap_to_dict(tilemap) if tilemap is not None else None
        snapshot = save.serialize_floor_snapshot(self.world, outgoing_id, entity_ids, tilemap=tilemap_data)

        if outgoing_record is not None:
            outgoing_record["snapshot"] = snapshot

        for eid in entity_ids:
            self.world.destroy_entity(eid)

        return outgoing_depth

    def _auto_save(self) -> None:
        """Unconditional after every transition (§2.3/§6) — see module
        docstring's gap-2 note for why this calls ``serialize_world`` for
        real but only *forwards* it to disk when a ``save_sink`` is wired."""
        save_data = save.serialize_world(self.world, spawned_uniques=self.spawned_uniques)
        self.last_save_data = save_data
        if self._save_sink is not None:
            self._save_sink(save_data)
        elif not self._warned_no_save_sink:
            logger.info(
                "FloorManager has no save_sink wired -- serialize_world() is still "
                "called for real after every transition (available via "
                "FloorManager.last_save_data), but nothing persists it to disk until "
                "a caller wires one (see this module's docstring)."
            )
            self._warned_no_save_sink = True


class CampaignSystem:
    """Loads an ordered campaign level list and resolves stair-based
    sequencing against it, independent of a level's list position (§1/§9 —
    only ``level_def["depth"]`` ever drives depth-aware content, never list
    index)."""

    def __init__(
        self,
        world: World,
        event_bus: EventBus,
        *,
        rng: random.Random | None = None,
        floor_manager: FloorManager | None = None,
    ) -> None:
        self.world = world
        self.event_bus = event_bus
        self.floor_manager = floor_manager if floor_manager is not None else FloorManager(world, event_bus, rng=rng)

        self._campaign_id: str | None = None
        self._levels: list[dict] = []
        self._linear_level_ids: list[str] = []  # non-hub level ids, list order (§1: hubs excluded from linear advance)
        self._current_level_id: str | None = None
        self._warned_no_campaign_data = False

        event_bus.subscribe("stair_use", self._on_stair_use)
        event_bus.subscribe("trigger_level_complete", self._on_trigger_level_complete)

    @property
    def current_level_id(self) -> str | None:
        return self._current_level_id

    @property
    def campaign_id(self) -> str | None:
        return self._campaign_id

    def load_campaign(self, campaign_id: str) -> None:
        """Loads ``campaign_id`` from the ``campaigns`` registry namespace
        (§5.1). Does NOT generate any floor — call :meth:`enter_level` (or
        wait for a real ``stair_use``) to actually produce the first
        floor."""
        campaign = _data_registry.get("campaigns", campaign_id) if _data_registry is not None else None
        if campaign is None:
            if not self._warned_no_campaign_data:
                logger.warning(
                    "CampaignSystem.load_campaign(%r): no campaign data resolvable "
                    "(no DataRegistry registered via campaign.set_data_registry, or "
                    "unknown campaign id) -- campaign not loaded.",
                    campaign_id,
                )
                self._warned_no_campaign_data = True
            return

        self._campaign_id = campaign_id
        self._levels = list(campaign.get("levels", []))
        self._linear_level_ids = [lvl["id"] for lvl in self._levels if not lvl.get("is_hub")]
        self._current_level_id = None

    def enter_level(self, level_id: str) -> None:
        """Transitions directly to ``level_id`` — the entry point for
        bootstrapping the very first floor of a loaded campaign, and for
        any explicit jump (a portal, a hub's own stairs) that isn't a plain
        sequential stair_use. See module docstring, gap 1."""
        level_def = self._level_by_id(level_id)
        if level_def is None:
            logger.warning("CampaignSystem.enter_level(%r): unknown level id for campaign %r.", level_id, self._campaign_id)
            return

        if not level_def.get("is_hub") and self._linear_level_ids and self._linear_level_ids[0] == level_id:
            level_def = dict(level_def, is_first_floor=True)

        self.floor_manager.transition_to(level_def)
        self._current_level_id = level_id

    def _level_by_id(self, level_id: str) -> dict | None:
        for level in self._levels:
            if level["id"] == level_id:
                return level
        return None

    def _on_stair_use(self, payload: dict | None) -> None:
        if not payload:
            return
        target_id = self._resolve_stair_target(payload.get("direction"))
        if target_id is not None:
            self.enter_level(target_id)

    def _resolve_stair_target(self, direction: str | None) -> str | None:
        """Sequencing among non-hub levels only, in list order (§1: a hub
        is "a place players return to, not a rung on a ladder"). From a hub
        (or before any floor has been entered), "down" enters the sequence
        at its first level; "up" has nowhere documented to go from there,
        so it's a no-op rather than a guess."""
        if not self._linear_level_ids:
            return None

        current = self._current_level_id
        if current not in self._linear_level_ids:
            return self._linear_level_ids[0] if direction == "down" else None

        idx = self._linear_level_ids.index(current)
        if direction == "down":
            next_idx = idx + 1
            return self._linear_level_ids[next_idx] if next_idx < len(self._linear_level_ids) else None
        if direction == "up":
            prev_idx = idx - 1
            return self._linear_level_ids[prev_idx] if prev_idx >= 0 else None
        return None

    def _on_trigger_level_complete(self, payload: dict | None) -> None:
        current = self._current_level_id
        if current is None:
            return
        level_def = self._level_by_id(current)
        if level_def is None or level_def.get("is_hub"):
            return  # hubs never complete a campaign (§1)
        if current not in self._linear_level_ids:
            return

        idx = self._linear_level_ids.index(current)
        if idx == len(self._linear_level_ids) - 1:
            self.event_bus.emit("campaign_complete", {"campaign_id": self._campaign_id})
