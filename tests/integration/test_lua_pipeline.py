"""End-to-end proof the Lua scripting layer is wired, not just built
(CONTRACTS.md §7.3 / component doc §8): a real ``LuaHost`` boots against a
real ``World``/``EventBus``, loads the real ``scripts/examples/*.lua``
content, and an emitted event drives a real Lua handler which calls a real
``engine.*`` wrapper into 01's real ``EffectResolver`` — not a mock of any
of it. A second case proves floor-scoped Lua state round-trips through
foundation's real ``serialize_floor_snapshot``/``restore_floor_snapshot``
with zero NPC/dialog/shop-specific save code (component doc §2.4).
"""

from __future__ import annotations

from pathlib import Path

from engine.core import save
from engine.core.ecs import World
from engine.core.events import EventBus
from engine.lua.lua_host import LuaCampaignStateComponent, LuaHost
from engine.systems.ai import PlayerTagComponent
from engine.systems.stats import StatsComponent

REPO_ROOT = Path(__file__).parent.parent.parent


def test_damage_trap_example_script_drives_real_effect_resolver():
    """scripts/examples/damage_trap.lua subscribes to entity_interacted and
    calls engine.deal_damage -> 01's real apply_effect_list -> damage_dealt.
    scripts/examples/heal_shrine.lua also reacts to the same event (it
    restores the actor's HP after the trap fires, alphabetically loading
    and therefore dispatching second) -- this test captures state at the
    moment damage_dealt fires (mid-dispatch, before heal_shrine's handler
    runs) rather than asserting on final post-heal HP, so it isn't
    order-fragile against that second script's own, independently correct
    behavior."""
    world = World()
    event_bus = EventBus()

    trap_id = world.create_entity()
    monster_id = world.create_entity()
    world.add_component(monster_id, StatsComponent(base={"hp": 12.0, "max_hp": 12.0}, modifiers={}))

    host = LuaHost(world, event_bus, None)
    host.boot()  # loads the real scripts/ tree (examples/*.lua only, today)

    damage_events: list[dict] = []
    hp_at_damage_time: list[float] = []

    def on_damage_dealt(payload):
        damage_events.append(payload)
        hp_at_damage_time.append(world.get_component(monster_id, StatsComponent).base["hp"])

    event_bus.subscribe("damage_dealt", on_damage_dealt)

    event_bus.emit("entity_interacted", {"actor_id": monster_id, "target_id": trap_id})

    assert len(damage_events) == 1
    assert damage_events[0]["target_id"] == monster_id
    assert damage_events[0]["source_id"] == trap_id
    assert damage_events[0]["amount"] == 5.0
    assert damage_events[0]["damage_type"] == "physical"
    # The real EffectResolver actually reduced HP by the time damage_dealt
    # fired: 12 - 5 = 7 (whatever heal_shrine.lua does afterward is its own,
    # separately-tested concern).
    assert hp_at_damage_time == [7.0]


def test_heal_shrine_example_script_restores_hp_in_isolation(tmp_path, monkeypatch):
    """Loads only scripts/examples/heal_shrine.lua (copied into an
    isolated tmp scripts dir, so damage_trap.lua's independent reaction to
    the same event can't interact with this assertion) and proves its
    observable effect: restore_hp through the real EffectResolver."""
    monkeypatch.chdir(tmp_path)
    scripts_dir = tmp_path / "scripts" / "examples"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "heal_shrine.lua").write_text(
        (REPO_ROOT / "scripts" / "examples" / "heal_shrine.lua").read_text(encoding="utf-8")
    )

    world = World()
    event_bus = EventBus()
    actor_id = world.create_entity()
    world.add_component(actor_id, StatsComponent(base={"hp": 1.0, "max_hp": 50.0}, modifiers={}))

    host = LuaHost(world, event_bus, None)
    host.boot()

    event_bus.emit("entity_interacted", {"actor_id": actor_id, "target_id": actor_id})

    assert world.get_component(actor_id, StatsComponent).base["hp"] == 50.0


def test_campaign_scoped_state_round_trips_through_world_serialize_deserialize(tmp_path, monkeypatch):
    """LuaCampaignStateComponent round-trips through 00's full
    serialize_world/deserialize_world (as opposed to the per-floor
    snapshot functions the floor-state case above uses) -- component doc
    §2.4's other half of "zero NPC/dialog/shop-specific save code"."""
    monkeypatch.chdir(tmp_path)

    world = World()
    event_bus = EventBus()
    player_id = world.create_entity()
    world.add_component(player_id, PlayerTagComponent())

    host = LuaHost(world, event_bus, None)
    host.boot()
    host.set_context(None, player_id)

    engine = host.lua_runtime.globals().engine
    engine.set_campaign_state("gold", 42)

    saved = save.serialize_world(world)

    world_2 = World()
    save.deserialize_world(saved, world_2)

    # The player entity (and its LuaCampaignStateComponent) round-tripped
    # with the same id, with zero code here aware of "campaign state" --
    # this is exactly the registration-is-the-whole-mechanism guarantee
    # component doc §2.4 describes.
    assert world_2.get_component(player_id, LuaCampaignStateComponent).data["gold"] == 42

    host_2 = LuaHost(world_2, EventBus(), None)
    host_2.boot()
    host_2.set_context(None, player_id)

    engine_2 = host_2.lua_runtime.globals().engine
    assert engine_2.get_campaign_state("gold", 0) == 42


def test_floor_scoped_state_round_trips_through_save_and_restore(tmp_path, monkeypatch):
    """No scripts/ content is needed for this case (component doc §8 Test
    Plan explicitly allows driving get/set_floor_state directly through
    the API) -- chdir into an empty tmp dir so boot() doesn't load the
    unrelated example scripts."""
    monkeypatch.chdir(tmp_path)

    world = World()
    event_bus = EventBus()
    host = LuaHost(world, event_bus, None)
    host.boot()
    host.set_context(None, None)

    engine = host.lua_runtime.globals().engine
    engine.set_floor_state("shrine_used", True)
    engine.set_floor_state("visits", 3)

    snapshot = save.serialize_floor_snapshot(world, floor_id="floor_1")

    # Tear down and rebuild the world entirely -- a fresh World, fresh
    # EventBus, fresh LuaHost, exactly as a real floor reload would do.
    world_2 = World()
    save.restore_floor_snapshot(world_2, snapshot)

    host_2 = LuaHost(world_2, EventBus(), None)
    host_2.boot()
    host_2.set_context(None, None)

    engine_2 = host_2.lua_runtime.globals().engine
    assert engine_2.get_floor_state("shrine_used", False) is True
    assert engine_2.get_floor_state("visits", 0) == 3
    # A key that was never set still falls back to its default -- proves
    # this isn't just "always true" on a de-serialized dict.
    assert engine_2.get_floor_state("never_set", "fallback") == "fallback"
