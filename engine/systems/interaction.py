"""``InteractableComponent`` — Python's *entire* NPC/dialog/shop footprint
(component doc ``11-npc-dialog-shop-content.md`` §1/§2.1).

This is deliberately not a "system" in the gameplay sense: no update loop,
no subscriptions of its own beyond what §1.1 describes (and those live in
``engine/input/input_handler.py``, not here). Everything downstream of
"an entity carries this component" is Lua (``scripts/npc_interaction.lua``,
``scripts/dialog_walker.lua``, ``scripts/shop.lua``) — spec §5's flagship
"Python knows nothing" pattern. See component doc §6 for why any *future*
interactable kind (a lever, a readable book, a crafting station, ...)
should extend this same pattern rather than adding a new Python component
or system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.core.ecs import component


@component
@dataclass
class InteractableComponent:
    """An opaque data blob. Python copies ``data`` verbatim from content
    (e.g. an NPC definition's ``interactable`` block, component doc §5.3)
    at spawn time and never inspects, validates, or branches on its
    contents — only Lua (``npc_interaction.lua``) reads it, keyed by the
    content-only convention ``data["kind"]``. This file must never contain
    a conditional keyed on that field or any other key of ``data``
    (component doc §8's Definition of Done; enforced by
    ``tests/unit/test_interaction_component.py``'s lint check) — a future
    interactable kind can put whatever shape it wants in here without this
    module ever needing to change.
    """

    data: dict[str, Any] = field(default_factory=dict)
