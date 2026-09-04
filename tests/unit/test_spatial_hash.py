import math

import pytest

from engine.core.spatial_hash import SpatialHash, chebyshev_distance


def test_chebyshev_distance_basic():
    assert chebyshev_distance((0, 0), (3, 4)) == 4
    assert chebyshev_distance((0, 0), (0, 0)) == 0
    assert chebyshev_distance((-2, -2), (2, 2)) == 4


def test_chebyshev_distance_differs_from_euclidean():
    """A diagonal step must cost the same as a cardinal step under
    Chebyshev — this is the case that would give a *different* answer
    under Euclidean distance, proving Chebyshev is actually in effect
    (CONTRACTS.md §2 rule 5, this component's Definition of Done)."""
    a = (0, 0)
    diagonal = (3, 3)
    cardinal = (3, 0)

    assert chebyshev_distance(a, diagonal) == chebyshev_distance(a, cardinal) == 3

    euclidean_diagonal = math.dist(a, diagonal)
    euclidean_cardinal = math.dist(a, cardinal)
    assert euclidean_diagonal != euclidean_cardinal  # they'd differ under Euclidean


def test_insert_and_query_radius():
    grid = SpatialHash(cell_size=8)
    grid.insert(1, (0, 0))
    grid.insert(2, (2, 2))
    grid.insert(3, (10, 10))

    nearby = grid.query_radius((0, 0), radius=2)
    assert set(nearby) == {1, 2}

    far = grid.query_radius((0, 0), radius=1)
    assert set(far) == {1}


def test_query_radius_uses_chebyshev_not_euclidean():
    """A point at (3, 0) is Chebyshev-distance 3 from the origin — same as
    a point at (3, 3) — but Euclidean distance would rank them very
    differently. Query with radius 3 must include both."""
    grid = SpatialHash(cell_size=4)
    grid.insert(1, (3, 0))
    grid.insert(2, (3, 3))
    grid.insert(3, (4, 4))  # Chebyshev distance 4 — must be excluded

    found = set(grid.query_radius((0, 0), radius=3))
    assert found == {1, 2}


def test_query_radius_zero_matches_only_exact_position():
    grid = SpatialHash(cell_size=8)
    grid.insert(1, (5, 5))
    grid.insert(2, (5, 6))
    assert grid.query_radius((5, 5), radius=0) == [1]


def test_query_radius_negative_returns_empty():
    grid = SpatialHash(cell_size=8)
    grid.insert(1, (0, 0))
    assert grid.query_radius((0, 0), radius=-1) == []


def test_move_updates_position_and_cells():
    grid = SpatialHash(cell_size=4)
    grid.insert(1, (0, 0))
    grid.move(1, (0, 0), (20, 20))

    assert grid.query_radius((0, 0), radius=2) == []
    assert grid.query_radius((20, 20), radius=0) == [1]
    assert grid.position_of(1) == (20, 20)


def test_move_with_stale_old_pos_raises():
    grid = SpatialHash(cell_size=4)
    grid.insert(1, (0, 0))
    with pytest.raises(ValueError):
        grid.move(1, (5, 5), (6, 6))  # (5, 5) isn't where entity 1 actually is


def test_move_untracked_entity_behaves_like_insert():
    grid = SpatialHash(cell_size=4)
    grid.move(1, (0, 0), (9, 9))
    assert grid.position_of(1) == (9, 9)


def test_remove_untracks_entity():
    grid = SpatialHash(cell_size=4)
    grid.insert(1, (1, 1))
    grid.remove(1)
    assert grid.position_of(1) is None
    assert grid.query_radius((1, 1), radius=5) == []


def test_remove_missing_entity_is_noop():
    grid = SpatialHash(cell_size=4)
    grid.remove(999)  # must not raise


def test_reinsert_moves_entity():
    grid = SpatialHash(cell_size=4)
    grid.insert(1, (0, 0))
    grid.insert(1, (50, 50))  # re-insert instead of move()
    assert grid.position_of(1) == (50, 50)
    assert grid.query_radius((0, 0), radius=1) == []


def test_query_rect_inclusive_bounds():
    grid = SpatialHash(cell_size=8)
    grid.insert(1, (0, 0))
    grid.insert(2, (5, 5))
    grid.insert(3, (10, 0))

    found = set(grid.query_rect((0, 0), (5, 5)))
    assert found == {1, 2}


def test_query_rect_accepts_unsorted_corners():
    grid = SpatialHash(cell_size=8)
    grid.insert(1, (2, 2))
    found = grid.query_rect((5, 5), (0, 0))  # bottom_right passed as top_left
    assert found == [1]


def test_query_across_many_cells():
    grid = SpatialHash(cell_size=4)
    for i in range(20):
        grid.insert(i, (i, 0))

    found = set(grid.query_radius((10, 0), radius=2))
    assert found == {8, 9, 10, 11, 12}
