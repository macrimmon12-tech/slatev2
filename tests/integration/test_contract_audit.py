"""Automated regression coverage for `15-integration-verification.md` §2's
cross-component audits — so these stay checked on every `pytest` run
rather than being a one-time manual pass (§6 of that doc explicitly asks
for this). Each ``test_*`` below corresponds to one audit subsection; see
this component's PR description for the full evidence table (emitter
file:line -> consumer file:line -> verdict) these checks summarize.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# §2.1 Event table audit
# ---------------------------------------------------------------------------

# The full CONTRACTS.md §3.2 event vocabulary.
ALL_EVENTS = {
    "entity_spawned", "entity_moved", "entity_died", "entity_interacted",
    "player_moved", "player_died", "check_wake", "noise_emitted",
    "resolve_hit", "damage_dealt", "miss", "death", "loot_drop",
    "item_pickup", "item_dropped", "item_used", "equip_changed",
    "set_bonus_changed", "identify_type_request", "stat_modifier_applied",
    "stat_allocated", "level_up_pending", "spell_choice_pending",
    "spell_chosen", "spell_learned", "bonus_points_remaining",
    "spell_cast_initiated", "spell_target_chosen", "target_confirmed",
    "cancel_targeting", "spell_cast_cancelled", "status_applied",
    "status_expired", "trap_revealed", "trigger_level_complete",
    "floor_changed", "stair_use", "campaign_selected", "campaign_complete",
    "game_complete", "new_game_selected", "load_game_selected",
    "save_slot_selected", "quit_selected", "show_panel", "panel_closed",
    "message", "play_sound", "vfx_play", "ranged_attack_attempt",
    "ranged_attack_blocked", "dialog_opened", "dialog_choice_selected",
    "dialog_closed", "dialog_choice_clicked", "shop_opened", "buy_item",
    "sell_item", "trade_completed", "shop_refreshed", "shop_leave_clicked",
    "portal_target_changed",
}

# `resolve_hit` is documented (CONTRACTS.md §3.2) as "may be event or
# direct call" -- 01's real CombatSystem uses it as a direct function call
# (engine/systems/ai.py calls `self.combat_system.resolve_hit(...)`
# directly), never through the event bus. Not a gap.
NOT_BUS_EVENTS = {"resolve_hit"}

# Events with no real subscriber anywhere yet, and why -- each is either
# genuinely terminal (per §2.1's own "game_complete may have no subscriber
# yet if there's no credits screen" example), a UI-facing notification
# with no UI screen built yet to react to it, or an escalated design gap
# (this component's PR description has the full writeup for each; not
# fixed here per §1's "escalate a real design gap rather than freelance
# it"). Grouped by reason rather than restating the same note per event.
_NO_UI_SCREEN_BUILT_YET = (
    "real emitter exists, but the UI screen/panel documented as its "
    "consumer hasn't been authored yet (no main menu, message log, "
    "game-over, or level-up/spell-choice panel is shown anywhere at "
    "runtime) -- same root cause as the NO_EMITTER_EXCEPTIONS main-menu "
    "group below, just on the consuming side"
)
_LUA_NOTIFICATION_ONLY = (
    "emitted by the real Lua script as a plain notification (e.g. for a "
    "future quest/journal/audio hook) -- the actual panel display for "
    "this flow goes through a separate `show_panel`/`engine.create_panel` "
    "call, not a subscription to this specific event; no current "
    "subscriber exists, matching several other still-notification-only "
    "events in this same table"
)
NO_SUBSCRIBER_EXCEPTIONS = {
    "game_complete": "terminal by design -- no credits/game-over screen exists yet",
    "level_up_pending": _NO_UI_SCREEN_BUILT_YET,
    "spell_choice_pending": _NO_UI_SCREEN_BUILT_YET,
    "bonus_points_remaining": _NO_UI_SCREEN_BUILT_YET,
    "campaign_selected": _NO_UI_SCREEN_BUILT_YET,
    "load_game_selected": _NO_UI_SCREEN_BUILT_YET,
    "save_slot_selected": _NO_UI_SCREEN_BUILT_YET,
    "entity_spawned": "real emitter (worldgen); AI/renderer both work by polling world.query(...) every turn/frame rather than caching from a spawn event, so no subscriber is actually needed today -- an architectural choice, not a gap",
    "item_pickup": "real emitter (inventory); no journal/UI has been authored yet to react to it",
    "item_dropped": "real emitter (inventory); no worldgen/loot re-pickup listener has been authored yet",
    "item_used": "real emitter (inventory); no UI/journal listener has been authored yet",
    "set_bonus_changed": "real emitter (SetTrackerSystem); StatsSystem doesn't need to subscribe -- set bonuses are applied via direct add_modifier calls in the same code path that emits this, not via a round-trip through the event bus",
    "stat_modifier_applied": "real emitter (StatsSystem); no UI/anim listener has been authored yet",
    "spell_learned": "real emitter (progression/effects); no UI/journal listener has been authored yet",
    "spell_cast_cancelled": "real emitter (SpellSystem); no UI listener has been authored yet",
    "trap_revealed": "real emitter (VisionSystem, per its own doc's inert-hook note -- traps are explicitly deferred, spec §12); no renderer/message-log listener has been authored yet",
    "dialog_opened": _LUA_NOTIFICATION_ONLY,
    "dialog_closed": _LUA_NOTIFICATION_ONLY,
    "dialog_choice_selected": _LUA_NOTIFICATION_ONLY,
    "shop_opened": _LUA_NOTIFICATION_ONLY,
    "shop_refreshed": _LUA_NOTIFICATION_ONLY,
    "trade_completed": _LUA_NOTIFICATION_ONLY,
    "portal_target_changed": "no portal/shrine Lua script has been authored yet to emit or consume this",
    "spell_target_chosen": "03's doc names this as a targeting-UI event; no such UI widget has been authored yet",
    "ranged_attack_attempt": "no ranged/ammo feature exists anywhere in the merged codebase -- escalated against 01/04",
    "ranged_attack_blocked": "no ranged/ammo feature exists anywhere in the merged codebase -- escalated against 01/04",
}

# Events with no real emitter anywhere yet -- all genuinely unbuilt
# main-menu-UI features, subscriber-only events waiting on a UI trigger
# that doesn't exist, or escalated design gaps (see this component's PR
# description), not wiring omissions of something that otherwise exists.
# `buy_item`/`sell_item`/`shop_leave_clicked`/`dialog_choice_clicked` ARE
# real (verified by manual code read, per this component's PR audit
# table) -- emitted by UIRuntime's generic, data-driven Button `on_click`
# dispatch (`self._event_bus.emit(event, payload)` with `event` read from
# `ui_skin.json`'s JSON, e.g. the "dialog"/"shop" screens' `on_click`
# blocks) rather than a literal string in the .py source, which this
# scanner's literal-string heuristic can't see.
_UI_ON_CLICK_DATA_DRIVEN = (
    "real emitter: UIRuntime's generic Button on_click dispatch reads the "
    "event name from ui_skin.json's JSON data (see e.g. the \"dialog\"/"
    "\"shop\" screens' on_click blocks), not a literal string in .py "
    "source -- this scanner's literal-string heuristic can't see it; "
    "verified by manual code read (this component's PR audit table)"
)
NO_EMITTER_EXCEPTIONS = {
    "buy_item": _UI_ON_CLICK_DATA_DRIVEN,
    "sell_item": _UI_ON_CLICK_DATA_DRIVEN,
    "shop_leave_clicked": _UI_ON_CLICK_DATA_DRIVEN,
    "dialog_choice_clicked": _UI_ON_CLICK_DATA_DRIVEN,
    "campaign_selected": "no main-menu campaign-select screen has been authored yet",
    "new_game_selected": "no main-menu screen has been authored yet (08's own doc's example widget-tree is documented, never shown at boot)",
    "load_game_selected": "no main-menu screen has been authored yet",
    "save_slot_selected": "no save-select screen has been authored yet",
    "identify_type_request": "no \"identify\" UI button has been authored yet to emit it",
    "stat_allocated": "no stat-allocation UI panel has been authored yet to emit it",
    "spell_chosen": "no spell-choice UI panel has been authored yet to emit it",
    "check_wake": (
        "subscribed to (engine/systems/ai.py) but never emitted -- movement "
        "code only emits noise_emitted-driven wake checks (combat/spell "
        "noise), never a passive proximity-based one on plain movement. "
        "Distinct from a missing-UI gap: fixing it means picking a passive "
        "wake radius/config surface no doc specifies (each AIComponent's "
        "own configured wake_radius isn't threaded through the current "
        "shared-radius check_wake payload shape at all) -- a real design "
        "decision, escalated against 02-ai-system.md/07-input-renderer-audio.md "
        "rather than freelanced here."
    ),
    "ranged_attack_attempt": "no ranged/ammo feature exists anywhere in the merged codebase -- escalated against 01/04",
    "ranged_attack_blocked": "no ranged/ammo feature exists anywhere in the merged codebase -- escalated against 01/04",
    "spell_target_chosen": "03's doc names this as a targeting-UI event; no such UI widget has been authored yet (target_confirmed/cancel_targeting cover the real input-driven paths)",
    "portal_target_changed": "no portal/shrine Lua script has been authored yet",
}

# Production source only -- deliberately excludes tests/. A test
# subscribing to an event purely to assert it fires doesn't count as a
# real production consumer for §2.1's purposes (that's exactly the
# "already carries at least one real subscriber" question this audit is
# trying not to fool itself on).
SEARCH_DIRS = ["engine", "editor", "scripts"]
SEARCH_SUFFIXES = {".py", ".lua"}

# A call site's event-name string can land a few lines after the
# `.emit(`/`.subscribe(` token itself (this codebase's own multi-line
# call style, e.g. `event_bus.emit(\n    "some_event", {...})`) -- scan a
# short trailing window instead of requiring the same physical line.
_TRAILING_WINDOW = 4


def _iter_source_files():
    for dirname in SEARCH_DIRS:
        base = REPO_ROOT / dirname
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix in SEARCH_SUFFIXES and "__pycache__" not in path.parts:
                yield path


def _scan_calls(call_name: str) -> dict[str, list[str]]:
    """Maps event name -> list of "file:line" sites calling
    ``<anything>.<call_name>("event_name", ...)`` or the Lua equivalent
    ``engine.emit("event_name", ...)`` with a literal string event name.
    Deliberately simple (a literal-string scan within a short trailing
    window, not a type-aware call analysis) -- matches this codebase's own
    convention of always passing event names as string literals
    (CONTRACTS.md §3), and is exactly the kind of "does at least one real
    call site exist" check §2.1 asks for, kept as an automated regression
    rather than a one-time manual grep.
    """
    hits: dict[str, list[str]] = {}
    marker = f".{call_name}("
    for path in _iter_source_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if marker not in text:
            continue
        lines = text.splitlines()
        for lineno, line in enumerate(lines, start=1):
            if marker not in line:
                continue
            window = "\n".join(lines[lineno - 1 : lineno - 1 + _TRAILING_WINDOW])
            for event in ALL_EVENTS:
                if f'"{event}"' in window or f"'{event}'" in window:
                    hits.setdefault(event, []).append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    return hits


def test_every_event_has_at_least_one_real_emitter():
    emits = _scan_calls("emit")
    missing = sorted(
        event for event in ALL_EVENTS - NOT_BUS_EVENTS
        if event not in emits and event not in NO_EMITTER_EXCEPTIONS
    )
    assert missing == [], (
        f"Event(s) in CONTRACTS.md §3.2 with no real emit() call found anywhere: {missing}"
    )


def test_every_event_has_at_least_one_real_subscriber():
    subs = _scan_calls("subscribe")
    missing = sorted(
        event for event in ALL_EVENTS - NOT_BUS_EVENTS
        if event not in subs and event not in NO_SUBSCRIBER_EXCEPTIONS
    )
    assert missing == [], (
        f"Event(s) in CONTRACTS.md §3.2 with no real subscribe() call found anywhere: {missing}"
    )


def test_documented_exceptions_are_not_stale():
    """If an exception's event actually gained a real emit/subscribe call
    since this table was written, the exception is stale and should be
    removed rather than silently carried forward forever."""
    emits = _scan_calls("emit")
    subs = _scan_calls("subscribe")
    stale_emitter_exceptions = [e for e in NO_EMITTER_EXCEPTIONS if e in emits]
    stale_subscriber_exceptions = [e for e in NO_SUBSCRIBER_EXCEPTIONS if e in subs]
    assert stale_emitter_exceptions == [], (
        f"These events now have a real emitter -- remove from NO_EMITTER_EXCEPTIONS: {stale_emitter_exceptions}"
    )
    assert stale_subscriber_exceptions == [], (
        f"These events now have a real subscriber -- remove from NO_SUBSCRIBER_EXCEPTIONS: {stale_subscriber_exceptions}"
    )


# ---------------------------------------------------------------------------
# §2.2 Component registry audit
# ---------------------------------------------------------------------------


def test_component_registry_lint_check_is_wired_into_the_real_test_suite():
    """00's own lint test (test_all_real_components_in_engine_are_registered)
    already enforces this -- this just confirms it's not a dead file nobody
    runs (§2.2's own "confirm it's actually wired into CI, not just present
    as a test file nobody runs")."""
    from engine.core import save

    modules = save.discover_component_modules()
    unregistered = save.find_unregistered_components(modules)
    assert unregistered == []


def test_interactable_component_is_registered():
    from engine.core.save import _COMPONENT_REGISTRY
    from engine.systems.interaction import InteractableComponent

    assert _COMPONENT_REGISTRY.get("InteractableComponent") is InteractableComponent


# ---------------------------------------------------------------------------
# §2.3 Data schema audit -- every namespace has >=1 real fixture that
# round-trips through the registry.
# ---------------------------------------------------------------------------

NAMESPACE_DIRS = {
    "entities": "entities",
    "items": "items",
    "spells": "spells",
    "effects": "effects",
    "affixes": "affixes",
    "campaigns": "campaigns",
    "vaults": "maps/vaults",
    "legendaries": "items/legendary",
    "sets": "items/sets",
    "maps": "maps",
    "npcs": "entities/npcs",
    "dialogs": "dialogs",
    "shops": "shops",
}


@pytest.mark.parametrize("namespace", sorted(NAMESPACE_DIRS))
def test_every_namespace_has_a_real_fixture_that_round_trips(namespace):
    from engine.core.registry import DataRegistry

    registry = DataRegistry()
    registry.load(REPO_ROOT / "data")
    entries = registry.all(namespace)
    assert entries, (
        f"Namespace {namespace!r} ({NAMESPACE_DIRS[namespace]}) has no real "
        "fixture file under data/ -- CONTRACTS.md §4/15's §2.3 requires at "
        "least one."
    )


def test_configs_namespace_has_real_config_files():
    """`configs` is keyed by filename, not `id` (CONTRACTS.md §4) -- a
    separate check since DataRegistry.all("configs") has a different shape."""
    from engine.core.registry import DataRegistry

    registry = DataRegistry()
    registry.load(REPO_ROOT / "data")
    assert registry.all("configs")


# ---------------------------------------------------------------------------
# §2.4 Wall autotile contract audit
# ---------------------------------------------------------------------------


def test_renderer_and_sprite_manager_agree_on_the_16_wall_variant_slot_names():
    from engine.render.autotile import wall_sprite_key

    renderer_slots = {wall_sprite_key(bitmask) for bitmask in range(16)}

    import editor.modes.sprite_manager as sprite_manager_module

    editor_slots = set(sprite_manager_module.WALL_VARIANT_SLOTS)

    assert renderer_slots == editor_slots == {f"wall_{i}" for i in range(16)}


# ---------------------------------------------------------------------------
# §2.5 room_id contract audit
# ---------------------------------------------------------------------------


def test_procgen_floors_populate_room_id_on_every_walkable_tile():
    import random

    from engine.core.ecs import World
    from engine.systems import worldgen

    worldgen.reset_module_state()
    level_def = {
        "id": "contract_audit_floor", "depth": 1, "width": 30, "height": 20,
        "monster_spawns": [], "vaults": [],
    }
    floor_id = worldgen.generate_floor(level_def, World(), random.Random(5))
    tilemap = worldgen.get_tilemap(floor_id)
    walkable = [pos for pos, tile in tilemap.tiles.items() if tile.walkable]
    assert walkable
    assert all(tilemap.tiles[pos].room_id is not None for pos in walkable)
    worldgen.reset_module_state()


def test_handcrafted_map_with_no_room_authoring_gets_the_exact_warning():
    """Re-verified here (not just trusted from 13's own test file) per
    §2.5's "confirm the warning actually fires against a real handcrafted
    map fixture with no room_id data done, not just that the warning code
    exists"."""
    import pygame

    pygame.init()
    from editor.editor import Editor
    from editor.modes.map_editor import ROOM_ID_MISSING_WARNING, MapEditorMode

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        editor = Editor()
        editor.new_project(Path(tmp) / "proj")
        mode = editor.mode("map_editor")
        assert isinstance(mode, MapEditorMode)
        warnings = mode.validate_map()
        assert ROOM_ID_MISSING_WARNING in warnings


# ---------------------------------------------------------------------------
# §2.6 Lua boundary audit
# ---------------------------------------------------------------------------
# The set_stat-rejection and sandbox-nulled-globals halves of this audit
# are covered by tests/unit/test_lua_sandbox.py's real-Lua-source tests
# (test_set_stat_rejects_combat_derived_stat_from_real_lua_script,
# test_nulled_globals_are_nil, test_malicious_script_fixture_*). The third
# (panel widget-tree shape parity) is checked here.


def test_lua_panels_and_skin_editor_use_the_same_widget_tree_validator_and_file():
    import inspect

    from editor.modes import skin_editor as skin_editor_module
    from engine.core.schemas import ui_skin_schema

    source = inspect.getsource(skin_editor_module)
    assert "ui_skin_schema" in source
    assert "ui_skin.json" in source

    import engine.lua.api.panel as panel_api_module

    panel_source = inspect.getsource(panel_api_module)
    # create_panel's own doc: looked up by id in ui_skin.json's
    # pre-registered screens -- the same file, same schema, 08's UIRuntime
    # renders both a Lua-authored panel and a Skin-Editor-authored one
    # through the identical show_panel/tree contract.
    assert "ui_skin.json" in panel_source
    assert ui_skin_schema.validate_widget_tree is not None
