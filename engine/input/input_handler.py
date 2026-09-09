"""InputHandler: raw pygame key events -> logical, context-scoped actions
-> action-level events on the shared event bus.

See ``docs/components/07-input-renderer-audio.md`` §2.1 for the full spec.
This module never decides game *rules* — hit chance, spell legality,
inventory contents — it only translates a physical key press into a named
action (via ``data/config/controls.json``) and emits the corresponding
request-level event; the owning system (spells/inventory/combat) decides
legality and no-ops or refuses silently (CONTRACTS.md §2 rule 7).

Genuine spec gaps this module had to resolve on its own (flagged here and
in the PR description, per CONTRACTS.md §8/the component doc's own Open
Questions §10, rather than silently guessed):

1. **Constructor signature.** The doc's §2.1 signature is
   ``__init__(self, event_bus, controls_config, context="game")`` only, but
   §2.1's own action table requires resolving "the player entity",
   adjacency lookups through ``SpatialHash``, a passability check for
   movement, and (for cast/use-item slot actions) a slot->id lookup no
   merged component yet owns. Following the precedent set by
   ``engine.systems.ai.AISystem``/``engine.systems.vision.VisionSystem``
   (both of which extended their own documented constructors with
   optional, backward-compatible keyword-only parameters when the doc's
   signature was incomplete), this class accepts additional optional
   keyword-only dependencies — ``world``, ``spatial_hash``, ``is_passable``,
   ``resolve_spell_slot``, ``resolve_item_slot``, ``get_current_target`` —
   all defaulting to ``None``/a fresh empty ``World`` so the documented
   positional call still works, just producing no-ops (never crashes) for
   the features that need one of these.
2. **``PositionComponent``/``PlayerTagComponent``.** No component doc
   claims ownership of these (component doc §10 references "foundation's
   player-id convention", which `00-foundation-core.md` never actually
   defines). ``engine.systems.ai`` defined provisional versions and
   explicitly invites reuse; ``engine.systems.vision`` already took that
   invitation. This module does the same rather than inventing a third,
   incompatible definition.
3. **Player-movement call boundary (Open Questions §10, bullet 1).** No
   merged component documents a ``request_move`` event or a movement entry
   point function. Per the doc's own stated default ("if no owning
   component documents a request_move event, InputHandler calls a
   documented movement entry point directly for the player entity... since
   player input is inherently a direct-call boundary"), this module *is*
   that entry point: it updates ``PositionComponent`` (and ``SpatialHash``,
   if one was injected) directly for the player entity and then emits
   ``entity_moved``/``player_moved`` itself. Monster/AI movement is
   untouched — that stays ``AISystem``'s own internal concern.
4. **Cast-key intake event name (Open Questions §10, bullet 2).** No
   `03-spells-status.md` code is merged, so this module takes the doc's
   stated default: emit ``spell_cast_initiated`` directly with
   ``targeting_mode: None`` for the owning system to fill in. Slot->spell_id
   resolution is not this component's data to own (hotbar/loadout state
   belongs to spellbook/inventory content), so it is delegated to an
   optional injected ``resolve_spell_slot`` callable; with none wired, a
   cast-slot key press is a logged-once no-op rather than a crash or a
   guessed spell id (CONTRACTS.md §2 rule 7).
5. **``use_item_<slot>``.** CONTRACTS.md §3.2's canonical event table
   assigns ``item_used``'s emission to "inventory", not input — and this
   component doc's own §4 "Emits" table (the authoritative list of what
   *this* component emits) does not list ``item_used`` at all, even though
   §2.1's action table mentions it. Treating §4 as authoritative for scope
   and §2.1's mention as aspirational/underspecified, this module does not
   unilaterally become a second emitter of an event CONTRACTS.md assigns to
   another owner: ``use_item_<slot>`` follows the same optional-resolver,
   absence-is-a-no-op pattern as cast-spell (see bullet 4) purely so a
   keybind exists and never crashes; wiring a real ``resolve_item_slot`` is
   left to whoever integrates inventory.
6. **``use_stairs`` direction.** The doc's own §5.1 sample config binds
   *both* ``Greater`` and ``Less`` to the single ``use_stairs`` action, yet
   the emitted ``stair_use`` event needs a ``direction``. Resolved via a
   small, explicit key->direction hint table (``Greater`` -> ``"down"``,
   ``Less`` -> ``"up"``, unrecognized key -> ``"down"``) consulted only for
   this one action — the only place this module inspects *which* physical
   key fired rather than just the action name.
8. **Bump-into-interactable hook (11-npc-dialog-shop-content.md §1.1).**
   That component doc's §1.1 requires the movement-collision check to emit
   ``entity_interacted`` when the player bumps into an entity carrying
   ``InteractableComponent`` — this module's merged code had no such check
   (only the explicit ``interact`` key emitted it, via ``_handle_interact``
   below). Since no other component has a reason to add this first, 11's
   own PR adds it here: ``_handle_move`` now checks, via the injected
   ``spatial_hash`` (absent = zero cost, exactly like every other
   ``spatial_hash``-dependent feature in this module), whether the
   destination tile holds an entity carrying ``InteractableComponent``
   before applying the ``is_passable`` check; if so, it emits
   ``entity_interacted`` with the documented ``{actor_id, target_id}``
   shape and does not move onto that tile (bumping into an interactable
   interacts with it rather than walking through it). A destination tile
   occupied by a *non*-interactable entity (a monster, say) is untouched by
   this check and falls through to the pre-existing ``is_passable``-driven
   behavior — 11's PR does not add anything else to this file.
7. **Key-name resolution.** The doc says key names are
   "pygame key-name strings (``pygame.key.key_code``-compatible)", but
   ``pygame.key.key_code`` does not actually accept the doc's own sample
   names ``"KP8"``, ``"Greater"``, ``"Less"`` (pygame's *own*
   ``pygame.key.name()`` would render those as ``"[8]"``, ``">"``, ``"<"``).
   ``_resolve_pygame_key`` therefore also falls back to a
   ``getattr(pygame, f"K_{name.upper()}")`` lookup (i.e. treating the
   config's key names as pygame constant-name suffixes), which does accept
   every name in the doc's sample file verbatim. An unresolvable name is
   logged once and that single binding is skipped — never a crash, and
   never silently disabling the whole action if another bound key for it is
   still resolvable.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Callable

import pygame

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.spatial_hash import SpatialHash

# See module docstring, gap 2.
from engine.systems.ai import PlayerTagComponent, PositionComponent

# Flagged cross-component addition (see module docstring, gap 8, and
# 11-npc-dialog-shop-content.md §1.1): 11's own PR added this import and
# the bump-into-interactable check in _handle_move below, since this
# module's merged code only emitted entity_interacted for the explicit
# interact key, not the movement-collision ("bump") case §1.1 documents.
from engine.systems.interaction import InteractableComponent

# Live-play wiring addition (no component doc covers "bump into a
# monster" at all -- §2.1's action table only ever produced
# entity_moved/player_moved or the interactable-bump case above; a real
# playable build needs *something* to turn "walked into a hostile" into
# combat). StatsComponent is used purely as an existence check ("this
# entity has combat stats, so it's attackable"), never read from -- this
# module still never decides hit chance or damage (module docstring, top
# paragraph): it only recognizes the bump and emits a request-level event
# for whoever resolves combat (the live game loop) to act on, exactly the
# same shape as the interactable-bump case just above.
from engine.systems.stats import StatsComponent

logger = logging.getLogger(__name__)

Position = tuple[int, int]

VALID_CONTEXTS = ("game", "targeting", "ui")

# Built-in fallback bindings, baked as a constant per the component doc's
# instruction to "fall back to a built-in default dict baked as a Python
# constant at the top of the module, not scattered through the handler
# logic" — this is a verbatim copy of the doc's §5.1 sample file.
DEFAULT_CONTROLS: dict[str, dict[str, list[str]]] = {
    "game": {
        "move_north": ["Up", "w", "KP8"],
        "move_south": ["Down", "s", "KP2"],
        "move_east": ["Right", "d", "KP6"],
        "move_west": ["Left", "a", "KP4"],
        "move_ne": ["KP9"],
        "move_nw": ["KP7"],
        "move_se": ["KP3"],
        "move_sw": ["KP1"],
        "wait": ["KP5", "z"],
        "interact": ["e", "Return"],
        "use_stairs": ["Greater", "Less"],
        "cast_spell_1": ["1"],
        "cast_spell_2": ["2"],
        "use_item_1": ["q"],
        "inventory": ["i"],
        "spellbook": ["p"],
        "journal": ["j"],
        "minimap": ["m"],
        "menu": ["Escape"],
    },
    "targeting": {
        "move_cursor_north": ["Up", "w"],
        "move_cursor_south": ["Down", "s"],
        "move_cursor_east": ["Right", "d"],
        "move_cursor_west": ["Left", "a"],
        "confirm_target": ["Return", "e"],
        "cancel_targeting": ["Escape"],
    },
    "ui": {
        "confirm": ["Return"],
        "cancel": ["Escape"],
        "nav_up": ["Up", "w"],
        "nav_down": ["Down", "s"],
        "quit_to_menu": ["Escape"],
    },
}

# 8-way movement deltas (x, y); +y is south, matching the tile-grid
# convention used throughout the codebase (e.g. TileMap row-major grids).
_MOVE_DELTAS: dict[str, Position] = {
    "move_north": (0, -1),
    "move_south": (0, 1),
    "move_east": (1, 0),
    "move_west": (-1, 0),
    "move_ne": (1, -1),
    "move_nw": (-1, -1),
    "move_se": (1, 1),
    "move_sw": (-1, 1),
}

# Renderer-local cursor actions: §2.1 documents these as "no event", so
# they're recognized (never fall through to the "unknown action" warning)
# but deliberately do nothing here.
_CURSOR_ACTIONS = frozenset(
    {"move_cursor_north", "move_cursor_south", "move_cursor_east", "move_cursor_west"}
)

_PANEL_ACTIONS: dict[str, str] = {
    "menu": "menu",
    "inventory": "inventory",
    "spellbook": "spellbook",
    "journal": "journal",
    "minimap": "minimap",
}

# See module docstring, gap 6.
_STAIR_DIRECTION_BY_KEY: dict[str, str] = {"Greater": "down", "Less": "up"}

_warned: set[str] = set()


def _warn_once(key: str, message: str, *args: Any) -> None:
    if key not in _warned:
        logger.warning(message, *args)
        _warned.add(key)


def _resolve_pygame_key(name: str) -> int | None:
    """Resolve a controls.json key name to a pygame keycode. See module
    docstring, gap 7, for why this isn't a single ``pygame.key.key_code``
    call."""
    try:
        return pygame.key.key_code(name)
    except Exception:  # pragma: no cover - pygame raises a bespoke error type
        pass
    candidate = getattr(pygame, f"K_{name.upper()}", None)
    if isinstance(candidate, int):
        return candidate
    _warn_once(
        f"unknown_key:{name}",
        "controls.json key name %r is not a recognized pygame key; skipping this binding.",
        name,
    )
    return None


def _load_controls_config(config: dict | None) -> dict[str, dict[str, list[str]]]:
    """Normalize a raw ``controls.json`` payload into
    ``{context: {action: [key_name, ...]}}``, falling back to
    :data:`DEFAULT_CONTROLS` (logged once) on anything missing or
    malformed — controls are gameplay-critical but never worth crashing
    over (CONTRACTS.md §2 rule 7)."""
    if not config:
        _warn_once(
            "controls_missing",
            "controls.json missing/empty; using built-in default key bindings.",
        )
        return copy.deepcopy(DEFAULT_CONTROLS)

    contexts = config.get("contexts")
    if not isinstance(contexts, dict) or not contexts:
        _warn_once(
            "controls_malformed",
            "controls.json has no valid 'contexts' object; using built-in default key bindings.",
        )
        return copy.deepcopy(DEFAULT_CONTROLS)

    normalized: dict[str, dict[str, list[str]]] = {}
    for context_name, actions in contexts.items():
        if not isinstance(actions, dict):
            continue
        normalized_actions: dict[str, list[str]] = {}
        for action, keys in actions.items():
            # Schema requires a list (§5.1) even for one key, but don't
            # crash on a hand-edited file that used a bare string.
            if isinstance(keys, str):
                keys = [keys]
            if not isinstance(keys, list) or not keys:
                continue
            normalized_actions[action] = [str(k) for k in keys]
        normalized[context_name] = normalized_actions
    return normalized


def _build_key_map(bindings: dict[str, list[str]]) -> dict[int, tuple[str, str]]:
    """``{action: [key_name, ...]}`` -> ``{pygame_keycode: (action, key_name)}``
    for one context. Multiple key names per action collapse naturally into
    multiple entries pointing at the same action — "arrows + WASD + numpad
    all bound to move_north simultaneously" needs no special-casing here."""
    key_map: dict[int, tuple[str, str]] = {}
    for action, keys in bindings.items():
        for key_name in keys:
            keycode = _resolve_pygame_key(key_name)
            if keycode is not None:
                key_map[keycode] = (action, key_name)
    return key_map


def _slot_number(action: str, prefix: str) -> int | None:
    suffix = action[len(prefix):]
    try:
        return int(suffix)
    except ValueError:
        return None


class InputHandler:
    """Translates raw pygame key events into action-level events, scoped by
    a single active context (``game`` | ``targeting`` | ``ui``).

    See module docstring for the constructor's extra, optional dependencies
    beyond the doc's documented three positional/keyword args.
    """

    def __init__(
        self,
        event_bus: EventBus,
        controls_config: dict | None,
        context: str = "game",
        *,
        world: World | None = None,
        spatial_hash: SpatialHash | None = None,
        is_passable: Callable[[Position], bool] | None = None,
        resolve_spell_slot: Callable[[int, int], str | None] | None = None,
        resolve_item_slot: Callable[[int, int], str | None] | None = None,
        get_current_target: Callable[[], Any] | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._world = world if world is not None else World()
        self._spatial_hash = spatial_hash
        self._is_passable = is_passable
        self._resolve_spell_slot = resolve_spell_slot
        self._resolve_item_slot = resolve_item_slot
        self._get_current_target = get_current_target

        self._contexts = _load_controls_config(controls_config)
        self._key_maps = {
            context_name: _build_key_map(bindings)
            for context_name, bindings in self._contexts.items()
        }
        self._context = context

    # -- public API (component doc §2.1) -----------------------------------

    def set_spatial_hash(self, spatial_hash: SpatialHash | None) -> None:
        """Swap in a new ``SpatialHash`` — needed because
        ``engine.systems.worldgen`` builds a fresh, floor-scoped
        ``SpatialHash`` per floor (``register_spatial_hash``/
        ``get_spatial_hash``) rather than reusing one fixed instance for
        the whole run; whoever drives floor transitions (the live game
        loop) calls this on every ``floor_changed`` so adjacency/bump
        lookups here keep querying the *current* floor's entities instead
        of a stale or permanently-empty one."""
        self._spatial_hash = spatial_hash

    def set_context(self, context: str) -> None:
        """Swap the single active context. Exactly one context is active at
        a time (§2.1); an unrecognized context is accepted (so a later
        :meth:`rebind` can populate it) but every key press against it will
        simply find no binding until then."""
        self._context = context

    def rebind(self, action: str, context: str, new_key: str) -> None:
        """Rebind ``action`` in ``context`` to exactly ``new_key``,
        replacing any previously bound keys for that action in that
        context."""
        bindings = self._contexts.setdefault(context, {})
        bindings[action] = [new_key]
        self._key_maps[context] = _build_key_map(bindings)

    def resolve_action(self, pygame_event: Any) -> str | None:
        """Read-only counterpart to :meth:`handle_pygame_event`: returns
        the action name a key-down would resolve to in the current
        context, without dispatching it. Exists because the ``ui``
        context's ``confirm``/``cancel``/``nav_up``/``nav_down`` actions
        are, by this module's own design (module docstring note 3;
        ``_dispatch_action``'s closing branch), UI runtime's to route, not
        this module's to act on — a caller wiring a real frame loop needs
        the action name itself to hand to ``UIRuntime.handle_ui_input``."""
        if pygame_event.type != pygame.KEYDOWN:
            return None
        key_map = self._key_maps.get(self._context)
        if not key_map:
            return None
        match = key_map.get(pygame_event.key)
        if match is None:
            return None
        action, _key_name = match
        return action

    def handle_pygame_event(self, pygame_event: Any) -> None:
        """Translate one raw pygame event, if it's a recognized key-down in
        the current context, into an action dispatch. Anything else
        (key-up, mouse, non-key events, or a key with no binding in the
        current context) is silently ignored."""
        if pygame_event.type != pygame.KEYDOWN:
            return
        key_map = self._key_maps.get(self._context)
        if not key_map:
            return
        match = key_map.get(pygame_event.key)
        if match is None:
            return
        action, key_name = match
        self._dispatch_action(action, key_name)

    # -- action dispatch -----------------------------------------------------

    def _dispatch_action(self, action: str, key_name: str) -> None:
        if action in _MOVE_DELTAS:
            self._handle_move(action)
        elif action == "wait":
            # No owning component documents a turn-pass event/entry point
            # yet (component doc §2.1: "(movement system's turn-pass
            # path)") -- absence = zero cost, this is a deliberate no-op,
            # not a missed case.
            pass
        elif action == "interact":
            self._handle_interact()
        elif action == "use_stairs":
            self._handle_use_stairs(key_name)
        elif action.startswith("cast_spell_"):
            slot = _slot_number(action, "cast_spell_")
            if slot is not None:
                self._handle_cast_spell(slot)
        elif action == "open_spellbook":
            self._handle_show_panel("spellbook")
        elif action.startswith("use_item_"):
            slot = _slot_number(action, "use_item_")
            if slot is not None:
                self._handle_use_item(slot)
        elif action == "confirm_target":
            self._handle_confirm_target()
        elif action == "cancel_targeting":
            self._handle_cancel_targeting()
        elif action in _CURSOR_ACTIONS:
            pass  # renderer-local cursor state; no event (§2.1)
        elif action in _PANEL_ACTIONS:
            self._handle_show_panel(_PANEL_ACTIONS[action])
        elif action == "quit_to_menu":
            self._event_bus.emit("quit_selected", None)
        # "confirm"/"cancel"/"nav_up"/"nav_down" (ui context) have no
        # documented event mapping in §2.1/§4 -- UI runtime routes raw keys
        # itself in that context per §2.1's closing paragraph, so this is
        # not an "unknown action" warning case, just nothing to dispatch.
        elif action not in ("confirm", "cancel", "nav_up", "nav_down"):
            _warn_once(
                f"unknown_action:{action}",
                "controls.json action %r has no handler wired in InputHandler; ignoring.",
                action,
            )

    # -- action handlers -----------------------------------------------------

    def _resolve_player(self) -> tuple[int, PositionComponent] | None:
        rows = self._world.query(PlayerTagComponent, PositionComponent)
        if not rows:
            _warn_once(
                "no_player_entity",
                "No PlayerTagComponent+PositionComponent entity found; "
                "movement/interact/targeting input is a no-op until one exists.",
            )
            return None
        entity_id, _tag, position = rows[0]
        return entity_id, position

    def _find_interactable_at(self, position: Position, exclude: int) -> int | None:
        """Entity id at ``position`` carrying ``InteractableComponent``, if
        any (excluding ``exclude``, the mover itself). ``None`` if no
        ``spatial_hash`` was injected — absence = zero cost, this check
        simply doesn't run, matching every other ``spatial_hash``-gated
        feature in this module (see module docstring, gap 8)."""
        if self._spatial_hash is None:
            return None
        for candidate_id in sorted(self._spatial_hash.query_radius(position, 0)):
            if candidate_id == exclude:
                continue
            if self._world.get_component(candidate_id, InteractableComponent) is not None:
                return candidate_id
        return None

    def _find_attackable_at(self, position: Position, exclude: int) -> int | None:
        """Entity id at ``position`` carrying ``StatsComponent`` (used
        purely as an existence check — "this has combat stats, so it's
        attackable"), other than ``exclude``. ``None`` if no
        ``spatial_hash`` was injected — mirrors
        :meth:`_find_interactable_at`'s absence-= zero-cost gating."""
        if self._spatial_hash is None:
            return None
        for candidate_id in sorted(self._spatial_hash.query_radius(position, 0)):
            if candidate_id == exclude:
                continue
            if self._world.get_component(candidate_id, StatsComponent) is not None:
                return candidate_id
        return None

    def _handle_move(self, action: str) -> None:
        resolved = self._resolve_player()
        if resolved is None:
            return
        entity_id, position = resolved
        dx, dy = _MOVE_DELTAS[action]
        from_pos = (position.x, position.y)
        to_pos = (from_pos[0] + dx, from_pos[1] + dy)

        # See module docstring, gap 8 (11-npc-dialog-shop-content.md §1.1):
        # bumping into an interactable entity interacts with it instead of
        # moving onto its tile.
        interactable_target = self._find_interactable_at(to_pos, exclude=entity_id)
        if interactable_target is not None:
            self._event_bus.emit(
                "entity_interacted", {"actor_id": entity_id, "target_id": interactable_target}
            )
            return

        attack_target = self._find_attackable_at(to_pos, exclude=entity_id)
        if attack_target is not None:
            # Request-level only -- combat.resolve_hit (hit chance, damage
            # roll) is not this module's call (module docstring, top).
            self._event_bus.emit(
                "melee_attack_attempt", {"attacker_id": entity_id, "target_id": attack_target}
            )
            return

        if self._is_passable is not None and not self._is_passable(to_pos):
            return  # blocked -- silent no-op, not this component's call to log

        position.x, position.y = to_pos
        if self._spatial_hash is not None:
            if self._spatial_hash.position_of(entity_id) is None:
                self._spatial_hash.insert(entity_id, to_pos)
            else:
                self._spatial_hash.move(entity_id, from_pos, to_pos)

        payload = {"entity_id": entity_id, "from": from_pos, "to": to_pos}
        self._event_bus.emit("entity_moved", payload)
        self._event_bus.emit("player_moved", dict(payload))

    def _handle_interact(self) -> None:
        resolved = self._resolve_player()
        if resolved is None:
            return
        entity_id, position = resolved
        if self._spatial_hash is None:
            _warn_once(
                "interact_no_spatial_hash",
                "No SpatialHash wired into InputHandler; 'interact' cannot "
                "resolve an adjacent entity and is a no-op.",
            )
            return

        own_pos = (position.x, position.y)
        target_id: int | None = None
        for candidate_id in sorted(self._spatial_hash.query_radius(own_pos, 1)):
            if candidate_id == entity_id:
                continue
            target_id = candidate_id
            break
        if target_id is None:
            return
        self._event_bus.emit(
            "entity_interacted", {"actor_id": entity_id, "target_id": target_id}
        )

    def _handle_use_stairs(self, key_name: str) -> None:
        resolved = self._resolve_player()
        if resolved is None:
            return
        entity_id, _position = resolved
        direction = _STAIR_DIRECTION_BY_KEY.get(key_name, "down")
        self._event_bus.emit(
            "stair_use", {"entity_id": entity_id, "direction": direction}
        )

    def _handle_cast_spell(self, slot: int) -> None:
        resolved = self._resolve_player()
        if resolved is None:
            return
        entity_id, _position = resolved
        if self._resolve_spell_slot is None:
            _warn_once(
                "cast_spell_no_resolver",
                "No resolve_spell_slot wired into InputHandler; cast-spell "
                "slot keys are a no-op until spellbook/hotbar state exists.",
            )
            return
        spell_id = self._resolve_spell_slot(entity_id, slot)
        if spell_id is None:
            return  # nothing hotbarred in that slot -- not an error
        self._event_bus.emit(
            "spell_cast_initiated",
            {"entity_id": entity_id, "spell_id": spell_id, "targeting_mode": None},
        )

    def _handle_use_item(self, slot: int) -> None:
        resolved = self._resolve_player()
        if resolved is None:
            return
        entity_id, _position = resolved
        if self._resolve_item_slot is None:
            _warn_once(
                "use_item_no_resolver",
                "No resolve_item_slot wired into InputHandler; use-item "
                "slot keys are a no-op until inventory hotbar state exists.",
            )
            return
        item_instance_id = self._resolve_item_slot(entity_id, slot)
        if item_instance_id is None:
            return  # nothing in that slot -- not an error
        self._event_bus.emit(
            "item_used", {"entity_id": entity_id, "item_instance_id": item_instance_id}
        )

    def _handle_confirm_target(self) -> None:
        target = self._get_current_target() if self._get_current_target is not None else None
        self._event_bus.emit("target_confirmed", {"target": target})

    def _handle_cancel_targeting(self) -> None:
        resolved = self._resolve_player()
        entity_id = resolved[0] if resolved is not None else None
        self._event_bus.emit("cancel_targeting", {"entity_id": entity_id})

    def _handle_show_panel(self, panel_id: str) -> None:
        self._event_bus.emit("show_panel", {"panel_id": panel_id, "data": None})


__all__ = ["InputHandler", "DEFAULT_CONTROLS", "VALID_CONTEXTS"]
