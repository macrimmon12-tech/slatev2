"""Unit tests for `engine.systems.affixes.roll_affixes` (component doc
`04-inventory-items-loot.md` §2.2/§5.3, Definition of Done)."""

import random
from pathlib import Path

import pytest

from engine.core.registry import DataRegistry
from engine.systems.affixes import RARITY_BUDGETS, roll_affixes

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def registry() -> DataRegistry:
    reg = DataRegistry()
    reg.load(FIXTURES_ROOT)
    return reg


def test_common_rarity_yields_zero_affixes(registry):
    affixes = roll_affixes("dagger", "common", depth=1, registry=registry)
    assert affixes == []


def test_budget_by_rarity_endpoints_match_contracts():
    assert RARITY_BUDGETS["common"] == 0
    assert RARITY_BUDGETS["legendary"] == 14


def test_uncommon_budget_is_met_or_exceeded(registry):
    rng = random.Random(1234)
    affixes = roll_affixes("dagger", "uncommon", depth=10, registry=registry, rng=rng)
    total_cost = sum(a["cost"] for a in affixes)
    assert total_cost >= RARITY_BUDGETS["uncommon"]


def test_conflict_tags_never_both_picked(registry):
    # affix_conflict_a and affix_conflict_b share `shared_conflict` — across
    # many rolls big enough to want both, they should never co-occur.
    rng = random.Random(42)
    for _ in range(200):
        affixes = roll_affixes("dagger", "rare", depth=10, registry=registry, rng=rng)
        picked_ids = {a["id"] for a in affixes}
        assert not {"affix_conflict_a", "affix_conflict_b"}.issubset(picked_ids)


def test_depth_gating_excludes_ineligible_affixes(registry):
    # affix_deep_only has min_depth 15 — must never appear at depth 1.
    rng = random.Random(7)
    for _ in range(200):
        affixes = roll_affixes("dagger", "epic", depth=1, registry=registry, rng=rng)
        picked_ids = {a["id"] for a in affixes}
        assert "affix_deep_only" not in picked_ids

    # ...but is eligible (may appear) at depth 15+.
    rng = random.Random(7)
    seen_deep_only = False
    for _ in range(200):
        affixes = roll_affixes("dagger", "epic", depth=15, registry=registry, rng=rng)
        if any(a["id"] == "affix_deep_only" for a in affixes):
            seen_deep_only = True
            break
    assert seen_deep_only


def test_weighting_skews_toward_cheap_affixes():
    # A pool with five equally-cheap (cost 1) affixes and one expensive
    # (cost 10) one, budget 4 — there's always a cheap alternative available
    # until the pool is exhausted, so 1/cost weighting should make the
    # expensive affix show up far less often than any individual cheap one.
    reg = DataRegistry()
    reg.load(FIXTURES_ROOT / "affixes_weighting")
    rng = random.Random(99)
    cheap_a_hits = 0
    expensive_hits = 0
    for _ in range(500):
        affixes = roll_affixes("dagger", "uncommon", depth=10, registry=reg, rng=rng)
        ids = {a["id"] for a in affixes}
        if "affix_cheap_a" in ids:
            cheap_a_hits += 1
        if "affix_pricey" in ids:
            expensive_hits += 1
    assert cheap_a_hits > expensive_hits


def test_fewer_affixes_than_budget_is_valid_not_an_error():
    # A registry with a single depth-gated affix pool too small to ever
    # meet the legendary-adjacent epic budget — must return early, not error.
    reg = DataRegistry()
    reg.load(FIXTURES_ROOT / "affixes_scarce")
    affixes = roll_affixes("dagger", "epic", depth=1, registry=reg, rng=random.Random(1))
    assert isinstance(affixes, list)
    assert sum(a["cost"] for a in affixes) < RARITY_BUDGETS["epic"]
