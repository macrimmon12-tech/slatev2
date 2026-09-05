"""4-bit cardinal-bitmask wall autotiling — the 16-variant contract.

See ``docs/components/07-input-renderer-audio.md`` §2.3 for the full
spec. This is the single most load-bearing contract this component
publishes: v1's exact failure (spec §11 lesson 3) was building this
renderer capability while content-authoring exposed only one generic
``"wall"`` sprite slot, so 15 of 16 variants silently never got used. This
module's job is purely computational (bitmask + key lookup + the
zero-asset polygon fallback); actual sprite *storage* is the theme JSON
`13-editor-core-authoring.md` authors and ``Renderer`` reads.

**Bitmask bit order (fixed, verbatim, do not change):**
``bit0 = North wall-neighbor present, bit1 = East, bit2 = South,
bit3 = West`` -- ``bitmask = N | (E << 1) | (S << 2) | (W << 3)``.

A tile with no ``(x, y)`` entry in the tilemap (i.e. off the generated
map) is treated as a wall neighbor, not a floor -- this makes the map
edges autotile as a fully enclosed wall instead of spuriously picking an
"open" variant at the boundary.

**Diagonal-corridor inner corners** (bitmask 3, 6, 9, 12 -- exactly two
*adjacent* cardinal neighbors present) get a hardcoded right-triangle
polygon fallback so diagonal corridors render legibly with zero sprite
assets. The two present cardinal sides name a corner of the tile; the
triangle covers the half of the tile touching both of those sides, split
along the diagonal that leaves the other two (open) sides untouched:

- bitmask  3 (N, E): triangle = (top-left, top-right, bottom-right) -- the
  NE half, split along the main diagonal (TL -> BR).
- bitmask 12 (S, W): triangle = (top-left, bottom-left, bottom-right) --
  the SW half, same main-diagonal split.
- bitmask  6 (E, S): triangle = (top-right, bottom-right, bottom-left) --
  the SE half, split along the anti-diagonal (TR -> BL).
- bitmask  9 (N, W): triangle = (top-left, top-right, bottom-left) -- the
  NW half, same anti-diagonal split.
"""

from __future__ import annotations

from typing import Protocol

import pygame

Position = tuple[int, int]

_NORTH, _EAST, _SOUTH, _WEST = (0, -1), (1, 0), (0, 1), (-1, 0)

# The four true "inner corner" bitmasks: exactly two adjacent (not
# opposite) cardinal wall-neighbors present.
DIAGONAL_CORNER_BITMASKS = frozenset({3, 6, 9, 12})


class _TileLike(Protocol):
    walkable: bool


class _TileMapLike(Protocol):
    """Minimal duck-typed shape this module needs from a tilemap --
    matches `06-worldgen-campaign.md` §2.1's ``TileMap``
    (``tiles: dict[(x, y), Tile]`` where ``Tile.walkable: bool``) without
    importing that (unmerged, Wave-1-sibling-owned) module, per
    CONTRACTS.md §8 stubbing guidance."""

    tiles: dict[Position, _TileLike]


def _is_wall(tilemap: _TileMapLike, pos: Position) -> bool:
    """A position is a "wall" for bitmask purposes if it's off the map
    entirely, or its tile exists and isn't walkable. See module docstring
    for why off-map counts as a wall."""
    tile = tilemap.tiles.get(pos)
    if tile is None:
        return True
    return not tile.walkable


def compute_wall_bitmask(tilemap: _TileMapLike, x: int, y: int) -> int:
    """Compute the 4-bit cardinal wall-neighbor bitmask for tile ``(x,
    y)``. See module docstring for the fixed bit order (0-15)."""
    nx, ny = x + _NORTH[0], y + _NORTH[1]
    ex, ey = x + _EAST[0], y + _EAST[1]
    sx, sy = x + _SOUTH[0], y + _SOUTH[1]
    wx, wy = x + _WEST[0], y + _WEST[1]

    bitmask = 0
    if _is_wall(tilemap, (nx, ny)):
        bitmask |= 1
    if _is_wall(tilemap, (ex, ey)):
        bitmask |= 1 << 1
    if _is_wall(tilemap, (sx, sy)):
        bitmask |= 1 << 2
    if _is_wall(tilemap, (wx, wy)):
        bitmask |= 1 << 3
    return bitmask


def wall_sprite_key(bitmask: int) -> str:
    """The theme JSON lookup key for a given bitmask -- always
    ``wall_0``..``wall_15`` (§2.3, the 16-variant contract)."""
    if not 0 <= bitmask <= 15:
        raise ValueError(f"bitmask must be 0-15, got {bitmask}")
    return f"wall_{bitmask}"


def is_diagonal_corner(bitmask: int) -> bool:
    """True for the four inner-corner bitmasks (3, 6, 9, 12) that get the
    hardcoded polygon fallback regardless of theme content."""
    return bitmask in DIAGONAL_CORNER_BITMASKS


def draw_diagonal_corner_fallback(
    surface: pygame.Surface,
    rect: pygame.Rect,
    bitmask: int,
    color: tuple[int, int, int],
) -> None:
    """Draw the hardcoded right-triangle fallback for one of the four
    diagonal-corner bitmasks (3, 6, 9, 12) into ``rect`` on ``surface``,
    filled with ``color``. A no-op (nothing drawn) for any other bitmask --
    every other variant is either a plain wall (no diagonal needed) or an
    open floor tile, neither of which this function draws."""
    tl = rect.topleft
    tr = rect.topright
    bl = rect.bottomleft
    br = rect.bottomright

    if bitmask == 3:  # N, E -- NE half, main diagonal
        points = (tl, tr, br)
    elif bitmask == 12:  # S, W -- SW half, main diagonal
        points = (tl, bl, br)
    elif bitmask == 6:  # E, S -- SE half, anti-diagonal
        points = (tr, br, bl)
    elif bitmask == 9:  # N, W -- NW half, anti-diagonal
        points = (tl, tr, bl)
    else:
        return

    pygame.draw.polygon(surface, color, points)
