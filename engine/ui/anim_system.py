"""AnimSystem -- render-layer-only terminal event subscriber
(docs/components/09-animation-vfx.md).

``AnimSystem`` listens to five combat/status events plus ``entity_moved``
(used purely to feed a local position cache) and turns them into transient
visuals -- projectile, swoosh, impact-flash, aura-pulse, area-burst,
tile-fade. It never mutates game state, never emits a gameplay-relevant
event, and never requires a new field on any payload documented in
CONTRACTS.md §3.2.

Removability (docs/components/09-animation-vfx.md §1.1): deleting this
module and un-registering its subscriptions leaves the rest of the engine
and every JSON file completely untouched. No event this module emits is
consumed anywhere else (it emits nothing at all -- see module bottom). No
combat/status system checks whether this component exists. See
``tests/integration/test_anim_system_pipeline.py`` for the executable test
proving this.

Heuristic tables (damage-type -> color, damage-amount -> scale, effect_id
-> color, vfx_id -> style) are deliberately Python data living in this
module, not JSON (component doc §1/§7) -- presentation-layer tuning that
nothing outside this module ever needs to read or override.

Payload-sufficiency audit (component doc §5): all six visual types resolve
using only the documented ``damage_dealt``/``miss``/``status_applied``/
``status_expired``/``vfx_play`` payload fields plus this module's own
``entity_moved``-fed local position cache -- no new payload field was
needed. This confirms 01-stats-combat.md's payload design is sufficient as
specified; no CONTRACTS.md amendment is required.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from engine.core.spatial_hash import chebyshev_distance

logger = logging.getLogger(__name__)

Position = tuple[int, int]
Color = tuple[int, int, int]

__all__ = [
    "AnimSystem",
    "Transient",
    "DAMAGE_TYPE_COLOR",
    "DEFAULT_DAMAGE_COLOR",
    "scale_for_amount",
    "STATUS_EFFECT_COLOR",
    "DEFAULT_STATUS_COLOR",
    "VFX_STYLE",
    "DEFAULT_VFX_STYLE",
    "VISUAL_KINDS",
]

# ---------------------------------------------------------------------------
# Heuristic tables -- Python configuration, not JSON (component doc §1/§7).
# ---------------------------------------------------------------------------

# All 8 canonical damage types (CONTRACTS.md §10) get an explicit entry; an
# unmapped/future damage type falls back to DEFAULT_DAMAGE_COLOR rather than
# a KeyError -- "absence = zero cost" applies to this module's own internal
# lookups too.
DAMAGE_TYPE_COLOR: dict[str, Color] = {
    "physical": (200, 200, 200),
    "fire": (240, 90, 40),
    "cold": (120, 200, 240),
    "lightning": (240, 240, 120),
    "poison": (120, 200, 80),
    "holy": (250, 240, 200),
    "arcane": (170, 110, 240),
    "necrotic": (110, 40, 110),
}
DEFAULT_DAMAGE_COLOR: Color = (255, 255, 255)


def scale_for_amount(amount: float) -> float:
    """Damage amount -> visual scale bucket. A missing/non-numeric amount
    is treated as 0 (smallest bucket) rather than raising."""
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 3:
        return 0.6
    if amount <= 8:
        return 1.0
    if amount <= 15:
        return 1.4
    return 1.8


# Small effect_id -> color heuristic table. Unmapped ids (any status effect
# id 03-spells-status.md defines that isn't listed here) fall back to
# DEFAULT_STATUS_COLOR -- never an error.
STATUS_EFFECT_COLOR: dict[str, Color] = {
    "poison": (90, 200, 90),
    "poisoned": (90, 200, 90),
    "burning": (240, 120, 40),
    "burn": (240, 120, 40),
    "freeze": (140, 210, 250),
    "frozen": (140, 210, 250),
    "stun": (240, 220, 100),
    "stunned": (240, 220, 100),
    "bleed": (200, 40, 40),
    "bleeding": (200, 40, 40),
    "blessed": (250, 240, 200),
    "cursed": (140, 60, 160),
}
DEFAULT_STATUS_COLOR: Color = (200, 200, 200)


def _desaturate(color: Color, factor: float = 0.5) -> Color:
    """Blend ``color`` toward neutral gray by ``factor`` (0 = unchanged, 1 =
    fully gray) -- used for the tile-fade variant of the status color table
    (component doc §2.1: "same color table as status_applied, faded/
    desaturated")."""
    gray = 160
    return tuple(int(c + (gray - c) * factor) for c in color)  # type: ignore[return-value]


# Small vfx_id -> (color, scale) heuristic table, analogous to the
# damage-type one. An unmapped vfx_id falls back to a generic burst, never
# an error.
VFX_STYLE: dict[str, tuple[Color, float]] = {
    "fireball": ((240, 110, 40), 1.6),
    "frost_nova": ((130, 210, 250), 1.4),
    "heal": ((120, 240, 150), 1.0),
    "arcane_blast": ((170, 110, 240), 1.4),
    "lightning_strike": ((240, 240, 120), 1.5),
}
DEFAULT_VFX_STYLE: tuple[Color, float] = ((220, 220, 255), 1.0)

NEUTRAL_MISS_COLOR: Color = (190, 190, 190)
MISS_SCALE: float = 0.5

# Default lifetime (seconds) per visual kind.
DEFAULT_DURATIONS: dict[str, float] = {
    "projectile": 0.25,
    "swoosh": 0.2,
    "impact-flash": 0.25,
    "aura-pulse": 0.5,
    "area-burst": 0.45,
    "tile-fade": 0.5,
}

VISUAL_KINDS: tuple[str, ...] = (
    "projectile",
    "swoosh",
    "impact-flash",
    "aura-pulse",
    "area-burst",
    "tile-fade",
)

# Melee-vs-ranged heuristic threshold (component doc §11 Open Questions): a
# reasonable default absent a `combat.attack_kind` field in the payload. If
# 01-stats-combat.md's payload ever grows an explicit ranged/melee flag,
# prefer reading that directly over this distance heuristic.
PROJECTILE_DISTANCE_THRESHOLD = 1


# ---------------------------------------------------------------------------
# Transient record
# ---------------------------------------------------------------------------


@dataclass
class Transient:
    """A local, ephemeral render-thread record -- not a ``@component``, not
    attached to any entity, not saved. World positions are stored (never
    screen pixels) and resolved through ``world_to_screen`` at draw time,
    since the window can resize between the source event and the frame that
    draws it."""

    kind: str
    duration: float
    color: Color
    scale: float = 1.0
    src: Optional[Position] = None
    dst: Optional[Position] = None
    data: dict[str, Any] = field(default_factory=dict)
    elapsed: float = 0.0

    @property
    def progress(self) -> float:
        """0.0 (just created) .. 1.0 (fully expired)."""
        if self.duration <= 0:
            return 1.0
        return min(1.0, self.elapsed / self.duration)

    @property
    def expired(self) -> bool:
        return self.elapsed >= self.duration


# ---------------------------------------------------------------------------
# AnimSystem
# ---------------------------------------------------------------------------


class AnimSystem:
    """Subscribes to combat/status events and maintains a local list of
    :class:`Transient` visuals. Purely additive: constructing it subscribes
    handlers; never constructing it (or simply never calling
    ``tick()``/``draw()``) is entirely safe -- no other system reads any
    state this class owns.

    Standalone by design (component doc §2/§6/§9): this module imports
    neither ``engine.ui.ui_runtime`` nor ``engine.render.renderer``.
    ``draw(surface, world_to_screen)`` is called directly by whatever owns
    the frame loop, independent of ``Renderer`` internals.
    """

    def __init__(
        self,
        event_bus: Any,
        screen_rect_provider: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._event_bus = event_bus
        self._screen_rect_provider = screen_rect_provider
        self._transients: list[Transient] = []
        # entity_id -> last known world position, fed only by entity_moved.
        # Deliberately not SpatialHash (component doc §3/§9): this is a
        # local render-thread convenience, not the authoritative store.
        self._positions: dict[int, Position] = {}

        self._tokens = [
            event_bus.subscribe("entity_moved", self._on_entity_moved),
            event_bus.subscribe("damage_dealt", self._on_damage_dealt),
            event_bus.subscribe("miss", self._on_miss),
            event_bus.subscribe("status_applied", self._on_status_applied),
            event_bus.subscribe("status_expired", self._on_status_expired),
            event_bus.subscribe("vfx_play", self._on_vfx_play),
        ]

    def close(self) -> None:
        """Unsubscribe every handler this instance registered. Not required
        for correctness (the removability test never calls this -- it
        proves removal is safe even without explicit teardown), but useful
        for tests/tools that construct many short-lived instances on a
        shared bus."""
        for token in self._tokens:
            self._event_bus.unsubscribe(token)
        self._tokens = []

    # -- test-only accessors --------------------------------------------

    def transients(self) -> list[Transient]:
        """A snapshot of the currently live transient list. Test-only
        accessor -- gameplay code never reads this."""
        return list(self._transients)

    def position_of(self, entity_id: int) -> Optional[Position]:
        """Test-only accessor into the local position cache."""
        return self._positions.get(entity_id)

    # -- event handlers ---------------------------------------------------

    def _on_entity_moved(self, payload: dict | None) -> None:
        if not payload:
            return
        entity_id = payload.get("entity_id")
        to = payload.get("to")
        if entity_id is None or to is None:
            return
        self._positions[entity_id] = (int(to[0]), int(to[1]))

    def _resolve_position(self, entity_id: Any) -> Optional[Position]:
        if entity_id is None:
            return None
        return self._positions.get(entity_id)

    def _on_damage_dealt(self, payload: dict | None) -> None:
        if not payload:
            return
        position = payload.get("position")
        if position is None:
            return
        position = (int(position[0]), int(position[1]))

        damage_type = payload.get("damage_type")
        color = DAMAGE_TYPE_COLOR.get(damage_type, DEFAULT_DAMAGE_COLOR)
        scale = scale_for_amount(payload.get("amount"))

        self._add(
            Transient(
                kind="impact-flash",
                duration=DEFAULT_DURATIONS["impact-flash"],
                color=color,
                scale=scale,
                dst=position,
            )
        )

        source_pos = self._resolve_position(payload.get("source_id"))
        if source_pos is None:
            # No known source position (never moved, or environmental
            # damage with no source_id) -- absence = zero cost: still show
            # the impact-flash above, just skip the attack-vector visual
            # rather than guessing a position.
            return

        if chebyshev_distance(source_pos, position) > PROJECTILE_DISTANCE_THRESHOLD:
            kind = "projectile"
        else:
            kind = "swoosh"
        self._add(
            Transient(
                kind=kind,
                duration=DEFAULT_DURATIONS[kind],
                color=color,
                scale=scale,
                src=source_pos,
                dst=position,
            )
        )

    def _on_miss(self, payload: dict | None) -> None:
        if not payload:
            return
        attacker_pos = self._resolve_position(payload.get("attacker_id"))
        defender_pos = self._resolve_position(payload.get("defender_id"))
        if attacker_pos is None or defender_pos is None:
            # Neither position is documented on the miss payload itself
            # (CONTRACTS.md §3.2) -- both must come from the entity_moved
            # cache. Absence = zero cost: skip rather than guess.
            return
        self._add(
            Transient(
                kind="swoosh",
                duration=DEFAULT_DURATIONS["swoosh"],
                color=NEUTRAL_MISS_COLOR,
                scale=MISS_SCALE,
                src=attacker_pos,
                dst=defender_pos,
            )
        )

    def _on_status_applied(self, payload: dict | None) -> None:
        if not payload:
            return
        position = self._resolve_position(payload.get("entity_id"))
        if position is None:
            return
        color = STATUS_EFFECT_COLOR.get(payload.get("effect_id"), DEFAULT_STATUS_COLOR)
        self._add(
            Transient(
                kind="aura-pulse",
                duration=DEFAULT_DURATIONS["aura-pulse"],
                color=color,
                dst=position,
            )
        )

    def _on_status_expired(self, payload: dict | None) -> None:
        if not payload:
            return
        position = self._resolve_position(payload.get("entity_id"))
        if position is None:
            return
        base_color = STATUS_EFFECT_COLOR.get(payload.get("effect_id"), DEFAULT_STATUS_COLOR)
        self._add(
            Transient(
                kind="tile-fade",
                duration=DEFAULT_DURATIONS["tile-fade"],
                color=_desaturate(base_color),
                dst=position,
            )
        )

    def _on_vfx_play(self, payload: dict | None) -> None:
        if not payload:
            return
        position = payload.get("position")
        if position is None:
            return
        position = (int(position[0]), int(position[1]))
        color, scale = VFX_STYLE.get(payload.get("vfx_id"), DEFAULT_VFX_STYLE)
        # `data` is read permissively per component doc §2.1/§5 -- any
        # positional/radius hint it might carry is not formally documented,
        # so it's stashed here for a future drawer to consult defensively,
        # never relied on or validated.
        self._add(
            Transient(
                kind="area-burst",
                duration=DEFAULT_DURATIONS["area-burst"],
                color=color,
                scale=scale,
                dst=position,
                data=dict(payload.get("data") or {}),
            )
        )

    def _add(self, transient: Transient) -> None:
        self._transients.append(transient)

    # -- frame lifecycle ----------------------------------------------------

    def tick(self, dt: float) -> None:
        """Ages every live transient by ``dt`` seconds and prunes expired
        ones."""
        if not self._transients:
            return
        for transient in self._transients:
            transient.elapsed += dt
        self._transients = [t for t in self._transients if not t.expired]

    def draw(self, surface: Any, world_to_screen: Callable[[Position], tuple[int, int]]) -> None:
        """Renders the live transient set onto ``surface``. Screen positions
        are resolved from each transient's stored world position via
        ``world_to_screen`` on every call -- never cached."""
        for transient in self._transients:
            drawer = _DRAWERS.get(transient.kind)
            if drawer is None:
                continue
            drawer(surface, transient, world_to_screen)


# ---------------------------------------------------------------------------
# Drawing routines -- a handful of pygame primitives per visual type, no
# sprite assets required (component doc §2.1: "consistent with 'absence =
# zero cost' extended to the animation layer itself").
# ---------------------------------------------------------------------------


def _blit_alpha_circle(surface: Any, center: tuple[int, int], radius: int, color: Color, alpha: int, width: int = 0) -> None:
    import pygame

    radius = max(1, radius)
    alpha = max(0, min(255, alpha))
    size = radius * 2 + 2
    temp = pygame.Surface((size, size), pygame.SRCALPHA)
    pygame.draw.circle(temp, (*color, alpha), (size // 2, size // 2), radius, width)
    surface.blit(temp, (center[0] - size // 2, center[1] - size // 2))


def _blit_alpha_line(surface: Any, p1: tuple[int, int], p2: tuple[int, int], color: Color, width: int, alpha: int) -> None:
    import pygame

    alpha = max(0, min(255, alpha))
    width = max(1, width)
    pad = width + 2
    min_x = min(p1[0], p2[0]) - pad
    min_y = min(p1[1], p2[1]) - pad
    w = abs(p1[0] - p2[0]) + pad * 2
    h = abs(p1[1] - p2[1]) + pad * 2
    temp = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.line(
        temp, (*color, alpha), (p1[0] - min_x, p1[1] - min_y), (p2[0] - min_x, p2[1] - min_y), width
    )
    surface.blit(temp, (min_x, min_y))


def _draw_impact_flash(surface: Any, t: Transient, world_to_screen: Callable[[Position], tuple[int, int]]) -> None:
    if t.dst is None:
        return
    center = world_to_screen(t.dst)
    radius = max(2, int(7 * t.scale))
    alpha = int(255 * (1.0 - t.progress))
    _blit_alpha_circle(surface, center, radius, t.color, alpha)


def _draw_projectile(surface: Any, t: Transient, world_to_screen: Callable[[Position], tuple[int, int]]) -> None:
    if t.src is None or t.dst is None:
        return
    sx, sy = world_to_screen(t.src)
    dx, dy = world_to_screen(t.dst)
    progress = t.progress
    x = int(sx + (dx - sx) * progress)
    y = int(sy + (dy - sy) * progress)
    alpha = int(255 * (1.0 - 0.5 * progress))
    _blit_alpha_circle(surface, (x, y), max(2, int(4 * t.scale)), t.color, alpha)


def _draw_swoosh(surface: Any, t: Transient, world_to_screen: Callable[[Position], tuple[int, int]]) -> None:
    if t.src is None or t.dst is None:
        return
    p1 = world_to_screen(t.src)
    p2 = world_to_screen(t.dst)
    alpha = int(255 * (1.0 - t.progress))
    _blit_alpha_line(surface, p1, p2, t.color, max(1, int(3 * t.scale)), alpha)


def _draw_aura_pulse(surface: Any, t: Transient, world_to_screen: Callable[[Position], tuple[int, int]]) -> None:
    if t.dst is None:
        return
    center = world_to_screen(t.dst)
    radius = int(8 + 10 * t.progress)
    alpha = int(220 * (1.0 - t.progress))
    _blit_alpha_circle(surface, center, radius, t.color, alpha, width=2)


def _draw_area_burst(surface: Any, t: Transient, world_to_screen: Callable[[Position], tuple[int, int]]) -> None:
    if t.dst is None:
        return
    center = world_to_screen(t.dst)
    radius = int(6 + 26 * t.scale * t.progress)
    alpha = int(220 * (1.0 - t.progress))
    _blit_alpha_circle(surface, center, radius, t.color, alpha, width=2)


def _draw_tile_fade(surface: Any, t: Transient, world_to_screen: Callable[[Position], tuple[int, int]]) -> None:
    if t.dst is None:
        return
    center = world_to_screen(t.dst)
    radius = int(9 * (1.0 - 0.3 * t.progress))
    alpha = int(180 * (1.0 - t.progress))
    _blit_alpha_circle(surface, center, radius, t.color, alpha, width=2)


_DRAWERS: dict[str, Callable[[Any, Transient, Callable[[Position], tuple[int, int]]], None]] = {
    "impact-flash": _draw_impact_flash,
    "projectile": _draw_projectile,
    "swoosh": _draw_swoosh,
    "aura-pulse": _draw_aura_pulse,
    "area-burst": _draw_area_burst,
    "tile-fade": _draw_tile_fade,
}
