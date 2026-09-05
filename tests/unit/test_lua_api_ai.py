"""``engine.lua.api.ai`` unit tests (component doc §7/§8/§9): ``02``'s real
``AISystem`` exposes no override/enable entry points, so
``override_ai_behavior``/``set_ai_enabled`` are documented no-ops;
``get_ai_behavior`` reads real ``AIComponent`` state.
"""

from __future__ import annotations

from engine.lua.api import ai as ai_api
from engine.systems.ai import AIComponent

from tests.unit._lua_api_helpers import make_ctx


def test_get_ai_behavior_none_without_component():
    ctx = make_ctx()
    api = ai_api.build(ctx)
    entity_id = ctx.world.create_entity()

    assert api["get_ai_behavior"](entity_id) is None


def test_get_ai_behavior_reads_real_component():
    ctx = make_ctx()
    api = ai_api.build(ctx)
    entity_id = ctx.world.create_entity()
    ctx.world.add_component(entity_id, AIComponent(behavior="coward", state="awake"))

    result = api["get_ai_behavior"](entity_id)

    assert result.behavior == "coward"
    assert result.state == "awake"


def test_override_ai_behavior_is_a_safe_no_op():
    ctx = make_ctx()
    api = ai_api.build(ctx)
    entity_id = ctx.world.create_entity()
    ctx.world.add_component(entity_id, AIComponent(behavior="chaser"))

    api["override_ai_behavior"](entity_id, "coward", None)

    # No exception, and (since no real entry point exists yet) the
    # component is untouched.
    assert ctx.world.get_component(entity_id, AIComponent).behavior == "chaser"


def test_set_ai_enabled_is_a_safe_no_op():
    ctx = make_ctx()
    api = ai_api.build(ctx)
    entity_id = ctx.world.create_entity()

    api["set_ai_enabled"](entity_id, False)  # must not raise
