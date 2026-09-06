"""Unit tests for engine.render.autotile -- see
docs/components/07-input-renderer-audio.md §2.3/§8 for the Definition of
Done bullets these correspond to.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import pytest

from engine.render.autotile import (
    DIAGONAL_CORNER_BITMASKS,
    compute_wall_bitmask,
    draw_diagonal_corner_fallback,
    is_diagonal_corner,
    wall_sprite_key,
)


@dataclass
class FakeTile:
    walkable: bool
    sprite_ref: str | None = None


@dataclass
class FakeTileMap:
    tiles: dict[tuple[int, int], FakeTile] = field(default_factory=dict)


def make_tilemap(wall_positions: set[tuple[int, int]], size: int = 5) -> FakeTileMap:
    """A size x size all-floor map with `wall_positions` marked non-walkable."""
    tiles = {}
    for x in range(size):
        for y in range(size):
            tiles[(x, y)] = FakeTile(walkable=(x, y) not in wall_positions)
    return FakeTileMap(tiles=tiles)


# -- compute_wall_bitmask ---------------------------------------------------


def test_bitmask_zero_when_fully_surrounded_by_floor():
    tilemap = make_tilemap({(2, 2)})
    assert compute_wall_bitmask(tilemap, 2, 2) == 0


def test_bitmask_all_16_neighbor_configurations():
    # Enumerate all 16 N/E/S/W wall-neighbor combinations directly against
    # the documented bit order (bit0=N, bit1=E, bit2=S, bit3=W).
    for bitmask in range(16):
        n = bool(bitmask & 1)
        e = bool(bitmask & 2)
        s = bool(bitmask & 4)
        w = bool(bitmask & 8)

        wall_positions = {(2, 2)}
        if n:
            wall_positions.add((2, 1))
        if e:
            wall_positions.add((3, 2))
        if s:
            wall_positions.add((2, 3))
        if w:
            wall_positions.add((1, 2))

        tilemap = make_tilemap(wall_positions)
        assert compute_wall_bitmask(tilemap, 2, 2) == bitmask, f"failed for bitmask {bitmask}"


def test_off_map_neighbor_counts_as_a_wall():
    tilemap = make_tilemap(set(), size=1)  # single floor tile at (0, 0)
    # Every neighbor of (0, 0) is off-map -> full bitmask.
    assert compute_wall_bitmask(tilemap, 0, 0) == 0b1111


# -- wall_sprite_key ---------------------------------------------------------


def test_wall_sprite_key_all_16_variants():
    for bitmask in range(16):
        assert wall_sprite_key(bitmask) == f"wall_{bitmask}"


def test_wall_sprite_key_rejects_out_of_range():
    with pytest.raises(ValueError):
        wall_sprite_key(16)
    with pytest.raises(ValueError):
        wall_sprite_key(-1)


# -- is_diagonal_corner -------------------------------------------------------


def test_is_diagonal_corner_exactly_the_four_documented_bitmasks():
    assert DIAGONAL_CORNER_BITMASKS == {3, 6, 9, 12}
    for bitmask in range(16):
        assert is_diagonal_corner(bitmask) == (bitmask in {3, 6, 9, 12})


# -- draw_diagonal_corner_fallback -------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _pygame_headless():
    import os

    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    pygame.init()
    yield
    pygame.quit()


WALL_COLOR = (90, 90, 100)
BACKGROUND = (0, 0, 0)


def _render(bitmask: int) -> pygame.Surface:
    surface = pygame.Surface((10, 10))
    surface.fill(BACKGROUND)
    rect = pygame.Rect(0, 0, 10, 10)
    draw_diagonal_corner_fallback(surface, rect, bitmask, WALL_COLOR)
    return surface


@pytest.mark.parametrize("bitmask", sorted(DIAGONAL_CORNER_BITMASKS))
def test_diagonal_corner_fallback_paints_non_background_pixels(bitmask):
    surface = _render(bitmask)
    non_background = [
        (x, y)
        for x in range(10)
        for y in range(10)
        if surface.get_at((x, y))[:3] != BACKGROUND
    ]
    assert non_background, f"bitmask {bitmask} drew nothing"


@pytest.mark.parametrize(
    "bitmask,near_corner,far_corner",
    [
        (3, (9, 0), (0, 9)),  # N,E -> NE corner painted, SW corner untouched
        (12, (0, 9), (9, 0)),  # S,W -> SW corner painted, NE corner untouched
        (6, (9, 9), (0, 0)),  # E,S -> SE corner painted, NW corner untouched
        (9, (0, 0), (9, 9)),  # N,W -> NW corner painted, SE corner untouched
    ],
)
def test_diagonal_corner_fallback_orientation(bitmask, near_corner, far_corner):
    surface = _render(bitmask)
    assert surface.get_at(near_corner)[:3] == WALL_COLOR
    assert surface.get_at(far_corner)[:3] == BACKGROUND


def test_non_corner_bitmask_draws_nothing():
    surface = _render(0)
    for x in range(10):
        for y in range(10):
            assert surface.get_at((x, y))[:3] == BACKGROUND
