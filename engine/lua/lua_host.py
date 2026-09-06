"""``LuaHost`` — the sandboxed ``lupa`` runtime, the merged ``engine`` Lua
table, and the ``scripts/`` loading mechanism. See
``docs/components/10-lua-scripting-layer.md`` §2 for the full spec this
implements.

This module is a *host*, not a gameplay system (component doc §1): every
``engine.*`` function it exposes is a thin, documented wrapper around
another component's public entry point or the shared event bus, built by
the ``engine/lua/api/*.py`` modules. A Lua script calling
``engine.deal_damage(...)``, which internally calls ``01``'s
``EffectResolver.apply_effect_list``, is content calling through a defined
engine boundary, not a Python system-to-system call — CONTRACTS.md rule 4
governs the latter, not this (component doc §1 states this explicitly; see
each ``api/*.py`` module's wrapper call sites for the same note repeated at
the point of call).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


# `lupa` bundles several embedded Lua versions; its top-level
# `lupa.LuaRuntime` in the installed release defaults to a newer one (5.5)
# where `unpack` is no longer a bare global (only `table.unpack`).
# Component doc §2.1's kept-globals list explicitly includes bare
# `unpack`, matching classic Lua 5.1 semantics -- so this module pins to
# `lupa.lua51` specifically, rather than shimming a global `unpack` alias
# onto a newer Lua version. `lua.LuaError`/`lua.lua_type` (used by
# convert.py) must come from this *same* submodule -- each bundled Lua
# version's runtime, error type, and `lua_type()` helper are a matched
# set; mixing e.g. `lupa.LuaError` (the top-level/5.5 alias) with a
# `lupa.lua51.LuaRuntime` instance would silently fail to catch its errors.
import lupa.lua51 as lua

from engine.core.ecs import World, component
from engine.core.events import EventBus
from engine.core.registry import DataRegistry

logger = logging.getLogger(__name__)

_warned_once: set[str] = set()


def warn_once(key: str, message: str, *args: Any) -> None:
    """Shared "log once" helper (CONTRACTS.md §9) used by ``LuaHost`` and
    every ``engine/lua/api/*.py`` module for a soft/absent dependency."""
    if key not in _warned_once:
        logger.warning(message, *args)
        _warned_once.add(key)


def reset_warned_once() -> None:
    """Test-only convenience: clears the "already logged this once" set so
    a test asserting a warning fires doesn't get silently skipped because
    an earlier test in the same process already tripped it."""
    _warned_once.clear()


class LuaApiError(Exception):
    """Raised by an ``engine.*`` wrapper to reject a call cleanly (e.g.
    ``set_stat``'s combat-derived-stat blocklist, or a missing entity/
    component). Caught by :meth:`LuaHost.run_protected` exactly like a
    Lua-side error — it never propagates past a script call boundary."""


# ---------------------------------------------------------------------------
# Floor-/campaign-scoped persistent state components (component doc §2.4)
# ---------------------------------------------------------------------------


@component
@dataclass
class LuaFloorStateComponent:
    """Arbitrary JSON-safe key/value store, scoped to one floor. Attached
    to an ordinary entity that lives and dies with that floor's entity set
    — this registration is the *entire* save/load mechanism for this data,
    per component doc §2.4; no NPC/dialog/shop-specific save code exists
    anywhere."""

    data: dict[str, Any] = field(default_factory=dict)


@component
@dataclass
class LuaCampaignStateComponent:
    """Arbitrary JSON-safe key/value store, scoped to the whole campaign/
    run. Attached to the player entity, which persists across floors
    outside any per-floor snapshot (component doc §2.4)."""

    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ApiContext — the shared, mutable context every api/*.py module closes over
# ---------------------------------------------------------------------------


@dataclass
class ApiContext:
    """Carries ``world``, ``event_bus``, ``registry`` (component doc §2.2),
    plus the mutable current-floor/current-campaign state ``set_context``
    rebinds, plus a handful of *optional* hooks for dependencies that are
    either genuinely hard-but-not-globally-discoverable (``spatial_hash``)
    or soft/not-yet-merged (``get_tile_fn``, ``grant_xp_fn``,
    ``inventory_fns``) per CONTRACTS.md §8 — each defaults to ``None`` and
    the corresponding ``engine.*`` wrapper degrades to a logged-once no-op
    when its hook is absent (CONTRACTS.md §2 rule 7, "absence = zero
    cost"). Every ``engine/lua/api/*.py`` module's ``build(ctx)`` closes
    over the *same* instance, so rebinding one field (e.g. ``set_context``
    updating ``floor_state_entity``) is immediately visible to every
    already-built wrapper — there is no per-module copy to go stale.
    """

    world: World
    event_bus: EventBus
    registry: DataRegistry | None
    lua_runtime: Any = None
    run_protected: Callable[..., Any] | None = None
    clear_floor_subscriptions: Callable[[], None] | None = None

    floor_state_entity: int | None = None
    campaign_state_entity: int | None = None
    floor_scoped_tokens: set[int] = field(default_factory=set)

    # Named, ad-hoc-callable script handlers, registered via
    # `engine.register_script_handler(script_ref, fn)` (api/event.py) --
    # this is what backs the module-level `run_script()` below, itself
    # 01's own documented call boundary into this component (see that
    # function's docstring).
    script_handlers: dict[str, Any] = field(default_factory=dict)

    # Hard dependency (foundation's SpatialHash) with no shared-global
    # accessor to reach it from a bare (world, event_bus, registry) triple
    # (unlike the event bus's `_default_bus`) — the constructing caller
    # (main.py, or a test) injects the live instance; absent, radius
    # queries return an empty list rather than raising.
    spatial_hash: Any = None

    # Soft, 06 — worldgen has no per-(x, y) tile accessor as of this
    # component's implementation (only `get_tilemap(floor_id) -> TileMap`,
    # and no "current floor_id for this world" accessor either); per
    # component doc §2.2's own documented fallback, this stays a no-op
    # returning nil until a caller injects a real lookup.
    get_tile_fn: Callable[[int, int], dict | None] | None = None

    # Soft, 05 — ProgressionSystem exposes no public "grant arbitrary XP"
    # entry point (XP is awarded internally off the `death` event only);
    # absent, `engine.grant_xp` is a logged-once no-op rather than
    # reaching into ProgressionSystem's private level-up bookkeeping.
    grant_xp_fn: Callable[[int, float], None] | None = None

    # Soft, 04 — inventory/items/loot doesn't exist yet at all. Each key
    # ("grant_item", "remove_item", "has_item") is an optional callable a
    # caller may inject once 04 merges; absent, each wrapper is a
    # logged-once no-op with a safe default return value.
    inventory_fns: dict[str, Callable] | None = None


# ---------------------------------------------------------------------------
# Sandboxing (component doc §2.1)
# ---------------------------------------------------------------------------

#: Nulled at init, before `engine` exists. Security-critical, not a style
#: choice (component doc §2.1) — a script executing `os.execute(...)` or
#: `io.open(...)` must be structurally impossible.
NULLED_GLOBALS: tuple[str, ...] = (
    "io", "os", "package", "require", "load", "loadfile", "dofile",
    "debug", "rawget", "rawset",
)

#: Verified present and callable after nulling.
REQUIRED_GLOBALS: tuple[str, ...] = (
    "math", "string", "table", "ipairs", "pairs", "type", "tostring",
    "tonumber", "pcall", "error", "assert", "select", "unpack",
)


def _sandbox(lua_runtime: Any) -> None:
    """Nulls :data:`NULLED_GLOBALS` and asserts :data:`REQUIRED_GLOBALS`
    are still present, in that order, on ``lua_runtime.globals()``. The
    one and only sandboxing code path — every ``LuaRuntime`` this process
    ever creates goes through this exact function (component doc §2.1)."""
    globals_table = lua_runtime.globals()
    for name in NULLED_GLOBALS:
        globals_table[name] = None

    missing = [name for name in REQUIRED_GLOBALS if globals_table[name] is None]
    assert not missing, (
        f"sandboxing broke required Lua globals: {missing} — this is a "
        "bug in _sandbox(), not content; kept globals must survive nulling "
        "the dangerous surface."
    )


# ---------------------------------------------------------------------------
# LuaHost
# ---------------------------------------------------------------------------


class LuaHost:
    """Owns exactly one ``lupa.LuaRuntime`` for the process lifetime,
    constructed once (like a gameplay system), never per-script or
    per-floor (component doc §2.1)."""

    def __init__(
        self,
        world: World,
        event_bus: EventBus,
        registry: DataRegistry | None,
        *,
        spatial_hash: Any = None,
        get_tile_fn: Callable[[int, int], dict | None] | None = None,
        grant_xp_fn: Callable[[int, float], None] | None = None,
        inventory_fns: dict[str, Callable] | None = None,
    ) -> None:
        self.world = world
        self.event_bus = event_bus
        self.registry = registry
        self.lua_runtime: Any = None
        self.ctx = ApiContext(
            world=world,
            event_bus=event_bus,
            registry=registry,
            spatial_hash=spatial_hash,
            get_tile_fn=get_tile_fn,
            grant_xp_fn=grant_xp_fn,
            inventory_fns=inventory_fns,
        )
        self._floor_changed_token: int | None = None
        self._last_call_failed = False

    # -- boot ---------------------------------------------------------------

    def boot(self) -> None:
        """Ordered sandbox construction (component doc §2.1):
        1. Create the one ``lupa.LuaRuntime``.
        2-3. Null the dangerous surface, verify the safe surface survived.
        4. Build and assign the merged ``engine`` table — the ONLY new
           global.
        5. Load ``scripts/``.
        """
        self.lua_runtime = lua.LuaRuntime(unpack_returned_tuples=True)
        _sandbox(self.lua_runtime)

        self.ctx.lua_runtime = self.lua_runtime
        self.ctx.run_protected = self.run_protected
        self.ctx.clear_floor_subscriptions = self.clear_floor_subscriptions

        engine_table = self._build_engine_table()
        globals_table = self.lua_runtime.globals()
        globals_table["engine"] = engine_table

        self._floor_changed_token = self.event_bus.subscribe("floor_changed", self._on_floor_changed)

        self.load_scripts_dir()

    def _build_engine_table(self) -> Any:
        # Imported lazily (inside boot, not at module import time) so a
        # not-yet-written api module can't break importing lua_host.py
        # itself during early development.
        from engine.lua.api import (
            ai as ai_api,
            combat as combat_api,
            dialog_shop as dialog_shop_api,
            entity as entity_api,
            event as event_api,
            inventory as inventory_api,
            lifecycle as lifecycle_api,
            narrative as narrative_api,
            panel as panel_api,
            state as state_api,
            world as world_api,
        )

        merged: dict[str, Callable] = {}
        for module in (
            entity_api, lifecycle_api, combat_api, ai_api, inventory_api,
            world_api, state_api, narrative_api, panel_api, dialog_shop_api,
            event_api,
        ):
            merged.update(module.build(self.ctx))
        return self.lua_runtime.table_from(merged)

    # -- run_protected --------------------------------------------------------

    def run_protected(self, lua_callable: Any, *args: Any) -> Any | None:
        """THE single call path used everywhere a Lua callable is invoked
        from Python (component doc §2.1). Catches ``lupa.LuaError`` and any
        Python exception raised by an ``engine.*`` wrapper (e.g.
        :class:`LuaApiError`), logs at ERROR, swallows it, and returns
        ``None`` — a broken script never crashes the host, only itself.
        """
        self._last_call_failed = False
        try:
            return lua_callable(*args)
        except lua.LuaError as exc:
            logger.error("Lua error while running a script callback: %s", exc)
            self._last_call_failed = True
            return None
        except LuaApiError as exc:
            logger.error("engine.* API call rejected: %s", exc)
            self._last_call_failed = True
            return None
        except Exception:
            logger.exception("Unexpected Python error while running a Lua callback")
            self._last_call_failed = True
            return None

    # -- floor/campaign context (component doc §2.4) -------------------------

    def set_context(self, floor_entity_hint: int | None, player_entity_id: int | None) -> None:
        """Rebinds :attr:`ApiContext.floor_state_entity`/
        ``campaign_state_entity``. Called internally by the ``floor_changed``
        handler (never exposed to Lua) — see component doc §2.4.

        ``floor_entity_hint``, if it names an entity that already carries
        :class:`LuaFloorStateComponent`, is used directly; otherwise this
        scans the live world for one (there should be at most one per
        floor — the destination floor's own singleton, if this floor has
        been visited before, since ``restore_floor_snapshot`` already ran
        before ``floor_changed`` fires). If none exists yet, one is
        created — from that point on it's an ordinary entity in this
        floor's entity set, so the *next* ``serialize_floor_snapshot`` call
        captures it with zero code written specifically for this.
        """
        entity_id: int | None = None
        if floor_entity_hint is not None and self.world.get_component(
            floor_entity_hint, LuaFloorStateComponent
        ) is not None:
            entity_id = floor_entity_hint
        else:
            for row in self.world.query(LuaFloorStateComponent):
                entity_id = row[0]
                break

        if entity_id is None:
            entity_id = self.world.create_entity()
            self.world.add_component(entity_id, LuaFloorStateComponent(data={}))

        self.ctx.floor_state_entity = entity_id

        if player_entity_id is None:
            self.ctx.campaign_state_entity = None
            return

        if self.world.get_component(player_entity_id, LuaCampaignStateComponent) is None:
            self.world.add_component(player_entity_id, LuaCampaignStateComponent(data={}))
        self.ctx.campaign_state_entity = player_entity_id

    def clear_floor_subscriptions(self) -> None:
        """Unsubscribes every event-bus token registered via
        ``engine.subscribe`` with ``opts.tag_floor_scoped = true``. Called
        automatically by the ``floor_changed`` handler, before rebinding
        context; also exposed to Lua directly (component doc §2.1/§2.2)."""
        for token in list(self.ctx.floor_scoped_tokens):
            self.event_bus.unsubscribe(token)
        self.ctx.floor_scoped_tokens.clear()

    def _on_floor_changed(self, _payload: dict | None) -> None:
        self.clear_floor_subscriptions()
        self.set_context(None, self._find_player_entity())

    def _find_player_entity(self) -> int | None:
        """Best-effort "who is the player" lookup for rebinding campaign-
        scoped state on ``floor_changed`` (whose own payload carries only
        depths, not entity ids). Uses 02's ``PlayerTagComponent`` — the
        documented "foundation player-id convention" stand-in (component
        doc §2.4/§9) — degrading to ``None`` (campaign state simply isn't
        rebound this transition) if it isn't importable or no entity
        carries it, rather than raising."""
        try:
            from engine.systems.ai import PlayerTagComponent
        except ImportError:
            warn_once(
                "lua_host_no_player_tag_component",
                "LuaHost: engine.systems.ai.PlayerTagComponent not importable; "
                "campaign-scoped Lua state will not be rebound on floor_changed.",
            )
            return None

        rows = self.world.query(PlayerTagComponent)
        if not rows:
            return None
        return rows[0][0]

    # -- script directory loading (component doc §2.3) -----------------------

    def load_scripts_dir(self, scripts_dir: Path | str = Path("scripts")) -> dict[str, bool]:
        """Discovers ``scripts_dir/**/*.lua`` (covering both top-level
        scripts and ``scripts_dir/examples/*.lua``), sorted alphabetically
        for determinism, and executes each file's top-level Lua code
        exactly once. Returns ``{filename: loaded_ok}``.

        A script that fails to parse or throws during its top-level
        execution is caught, logged once, and skipped — every other
        script still loads (component doc §2.3). A missing or empty
        ``scripts_dir`` is not an error (absence = zero cost).
        """
        scripts_dir = Path(scripts_dir)
        results: dict[str, bool] = {}
        if not scripts_dir.is_dir():
            return results

        for lua_path in sorted(scripts_dir.rglob("*.lua")):
            key = str(lua_path.relative_to(scripts_dir))
            try:
                source = lua_path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.error("Could not read Lua script %s: %s", lua_path, exc)
                results[key] = False
                continue

            self.run_protected(lambda src=source: self.lua_runtime.execute(src))
            ok = not self._last_call_failed
            if not ok:
                logger.error("Failed to load Lua script %s (see error above); skipping.", lua_path)
            results[key] = ok

        return results


# ---------------------------------------------------------------------------
# run_script — 01's own documented call boundary into this component
# ---------------------------------------------------------------------------
#
# engine/systems/effects.py's "script" effect type (_handle_script) already
# ships with a documented, exact call it makes once this component merges:
# `engine.lua.lua_host.run_script(script_ref, source_id, target_id, world,
# event_bus) -> None`. This wasn't part of this component doc's own §2.2
# API surface (01 introduced the call boundary independently on its own
# side, the same "documented assumption ahead of the dependency merging"
# shape CONTRACTS.md §8 describes, just pointed *at* this component rather
# than *from* it) -- flagged in this component's PR. Since neither doc
# specifies what "running a script by ref" means beyond the call signature,
# this extends the existing "a script registers itself at load time" self-
# registration convention (component doc §2.3) with one more, narrower
# form: `engine.register_script_handler(script_ref, fn)` lets any loaded
# script opt in to being invoked this way, keyed by an arbitrary string
# name (an item/spell JSON's `script_ref` field), called with
# `(source_id, target_id)` through the same run_protected guarantee every
# other Lua entry point gets.
_active_host: "LuaHost | None" = None


def set_active_host(host: "LuaHost | None") -> None:
    """Registers the process's live ``LuaHost`` so :func:`run_script` can
    reach it. Mirrors the existing ``set_rng``/``set_position_lookup``/
    ``set_monster_data_lookup`` "current instance" convention already used
    elsewhere in this codebase (CONTRACTS.md §8) rather than inventing a
    new pattern. Not called automatically by :meth:`LuaHost.boot` — the
    caller that owns the process's one live ``LuaHost`` (main.py, or a
    test) registers it explicitly, the same way those other ``set_*``
    hooks work. Pass ``None`` to clear (test teardown)."""
    global _active_host
    _active_host = host


def run_script(
    script_ref: str, source_id: int, target_id: int, world: World, event_bus: EventBus
) -> None:
    """01's entry point for the ``"script"`` effect type. Looks up a
    handler registered for ``script_ref`` via
    ``engine.register_script_handler`` on the currently active
    ``LuaHost`` and invokes it (through ``run_protected``) with
    ``(source_id, target_id)``. Absence = zero cost at every stage: no
    active host, or no handler registered for this particular
    ``script_ref``, both degrade to a logged-once no-op rather than
    raising — a content-authored ``script_ref`` typo must never crash an
    effect list."""
    if _active_host is None:
        warn_once(
            "lua_host_run_script_no_active_host",
            "engine.lua.lua_host.run_script(%r): no active LuaHost registered "
            "(see set_active_host); no-op.",
            script_ref,
        )
        return

    handler = _active_host.ctx.script_handlers.get(script_ref)
    if handler is None:
        warn_once(
            f"lua_host_run_script_unregistered_{script_ref}",
            "engine.lua.lua_host.run_script(%r): no script handler "
            "registered for this ref (via engine.register_script_handler); "
            "no-op.",
            script_ref,
        )
        return

    _active_host.run_protected(handler, source_id, target_id)
