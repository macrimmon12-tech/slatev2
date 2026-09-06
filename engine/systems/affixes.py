"""`AffixGenerator` — rarity-budgeted affix selection for rolled item
instances (component doc `04-inventory-items-loot.md` §2.2/§5.2/§5.3).

Budget-by-rarity table (CONTRACTS.md §10 fixes the two endpoints,
`common=0`/`legendary=14`; the intermediate values are this component's
committed default per the doc's §5.3):
"""

from __future__ import annotations

import logging
import random
from typing import Any

from engine.core.registry import DataRegistry

logger = logging.getLogger(__name__)

RARITY_BUDGETS: dict[str, int] = {
    "common": 0,
    "uncommon": 4,
    "rare": 8,
    "epic": 12,
    "legendary": 14,  # bypassed entirely — legendaries never call roll_affixes.
}

_logged_no_affixes_namespace = False


def roll_affixes(
    item_base_id: str,
    rarity: str,
    depth: int,
    registry: DataRegistry,
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    """Select a weighted-random affix set for a newly-spawned item instance.

    ``registry`` is an explicit parameter (not shown in the doc's code
    block) because ``DataRegistry`` has no global singleton instance in the
    merged foundation layer — ``engine/main.py``'s ``Application`` holds
    one explicitly and passes it around, so this module follows the same
    dependency-injection convention already used everywhere else
    (``World``/``EventBus`` are passed explicitly too). Noted in the PR as
    a deliberate deviation from the doc's literal signature.

    Weighted `1/cost` (cheap affixes common, expensive ones rare),
    `conflict_tags` excluding incompatible pairs, depth-gated via each
    affix's `min_depth`/`max_depth`. Sums `cost` until the rarity's budget
    is met or reached/exceeded, or no eligible affixes remain — running out
    of eligible affixes before the budget is met is a valid, silent
    outcome, never an error (CONTRACTS.md §2 rule 7).

    Legendary items never call this function — see component doc §5.5.
    ``item_base_id`` is accepted (per the documented signature) for future
    per-item affix filtering/telemetry; nothing in the current schema keys
    an affix's eligibility off it.
    """
    del item_base_id  # not used by current affix eligibility rules; see docstring.

    rng = rng if rng is not None else random.Random()
    budget = RARITY_BUDGETS.get(rarity, 0)
    if budget <= 0:
        return []

    global _logged_no_affixes_namespace
    try:
        all_affixes = registry.all("affixes")
    except Exception:  # pragma: no cover - defensive, registry always has this namespace
        all_affixes = {}
    if not all_affixes and not _logged_no_affixes_namespace:
        logger.info("roll_affixes: 'affixes' namespace is empty; rolling zero affixes.")
        _logged_no_affixes_namespace = True

    pool = [
        dict(affix)
        for affix in all_affixes.values()
        if affix.get("min_depth", 1) <= depth <= affix.get("max_depth", 10**9)
    ]

    picked: list[dict[str, Any]] = []
    picked_ids: set[str] = set()
    conflict_tags: set[str] = set()
    spent = 0

    while spent < budget:
        candidates = [
            affix
            for affix in pool
            if affix.get("id") not in picked_ids
            and not (set(affix.get("conflict_tags", [])) & conflict_tags)
        ]
        if not candidates:
            break

        weights = [1.0 / max(affix.get("cost", 1), 1) for affix in candidates]
        chosen = rng.choices(candidates, weights=weights, k=1)[0]

        picked.append(chosen)
        picked_ids.add(chosen.get("id"))
        conflict_tags.update(chosen.get("conflict_tags", []))
        spent += max(chosen.get("cost", 1), 0)

    return picked
