"""``engine.lua.api.combat`` unit tests (component doc §7/§8): both
functions route through 01's real ``EffectResolver.apply_effect_list``.
"""

from __future__ import annotations

from engine.lua.api import combat as combat_api
from engine.systems.stats import StatsComponent

from tests.unit._lua_api_helpers import make_ctx


def _make_target(ctx, hp=20.0, max_hp=100.0):
    target_id = ctx.world.create_entity()
    ctx.world.add_component(target_id, StatsComponent(base={"hp": hp, "max_hp": max_hp}, modifiers={}))
    return target_id


def test_deal_damage_reduces_hp_and_emits_damage_dealt():
    ctx = make_ctx()
    api = combat_api.build(ctx)
    source_id = ctx.world.create_entity()
    target_id = _make_target(ctx)
    events = []
    ctx.event_bus.subscribe("damage_dealt", lambda payload: events.append(payload))

    api["deal_damage"](source_id, target_id, 6, "fire")

    stats = ctx.world.get_component(target_id, StatsComponent)
    assert stats.base["hp"] == 14.0
    assert len(events) == 1
    assert events[0]["target_id"] == target_id
    assert events[0]["amount"] == 6.0
    assert events[0]["damage_type"] == "fire"
    assert events[0]["source_id"] == source_id


def test_deal_damage_accepts_dice_formula_amount():
    ctx = make_ctx()
    api = combat_api.build(ctx)
    source_id = ctx.world.create_entity()
    target_id = _make_target(ctx, hp=100.0)

    api["deal_damage"](source_id, target_id, "1d1", "physical")

    stats = ctx.world.get_component(target_id, StatsComponent)
    assert stats.base["hp"] == 99.0


def test_apply_effect_accepts_single_effect_table():
    ctx = make_ctx()
    api = combat_api.build(ctx)
    source_id = ctx.world.create_entity()
    target_id = _make_target(ctx, hp=10.0)

    api["apply_effect"]({"type": "restore_hp", "amount": 5}, source_id, target_id)

    stats = ctx.world.get_component(target_id, StatsComponent)
    assert stats.base["hp"] == 15.0


def test_apply_effect_accepts_array_of_effect_tables():
    ctx = make_ctx()
    api = combat_api.build(ctx)
    source_id = ctx.world.create_entity()
    target_id = _make_target(ctx, hp=10.0)

    api["apply_effect"](
        [
            {"type": "damage", "amount": 3, "damage_type": "physical"},
            {"type": "restore_hp", "amount": 1},
        ],
        source_id,
        target_id,
    )

    stats = ctx.world.get_component(target_id, StatsComponent)
    assert stats.base["hp"] == 8.0


def test_apply_effect_via_real_lua_table():
    """Exercises the actual Lua-table -> Python-list normalization path
    (a real lupa table, not a Python dict pretending to be one)."""
    ctx = make_ctx()
    api = combat_api.build(ctx)
    source_id = ctx.world.create_entity()
    target_id = _make_target(ctx, hp=10.0)

    lua_effect = ctx.lua_runtime.table_from({"type": "restore_hp", "amount": 5})
    api["apply_effect"](lua_effect, source_id, target_id)

    stats = ctx.world.get_component(target_id, StatsComponent)
    assert stats.base["hp"] == 15.0
