"""Covers this component's Definition of Done item: ``engine/main.py``
boots to an empty running loop with zero content loaded and doesn't
crash — "absence = zero cost" holding at the foundation level.

15-integration-verification.md addition: ``Application.boot()`` now
constructs and cross-wires the real Wave 1 gameplay systems (see
``engine/main.py``'s own module docstring) instead of leaving that
assembly for later — the tests below prove that wiring is real, not just
present as unused attributes."""

from engine.main import Application
from engine.systems.inventory import InventoryComponent
from engine.systems.stats import StatsComponent


def test_boot_with_empty_data_root_does_not_crash(tmp_path):
    app = Application(data_root=tmp_path)
    app.boot()  # tmp_path has no content at all under it
    assert app.registry.all("entities") == {}


def test_run_with_max_ticks_terminates():
    app = Application()
    app.boot()
    ticks_seen = []
    app.tick = lambda: ticks_seen.append(1)  # type: ignore[method-assign]
    app.run(max_ticks=5)
    assert len(ticks_seen) == 5


def test_stop_ends_the_loop_early():
    app = Application()
    app.boot()

    call_count = 0

    def tick_then_stop():
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            app.stop()

    app.tick = tick_then_stop  # type: ignore[method-assign]
    app.run(max_ticks=1000)
    assert call_count == 3


def test_boot_wires_real_stats_system_not_a_placeholder(tmp_path):
    app = Application(data_root=tmp_path)
    app.boot()

    entity_id = app.world.create_entity()
    app.world.add_component(
        entity_id, StatsComponent(base={"armor": 10.0}, modifiers={})
    )
    app.stats_system.add_modifier(entity_id, "test_tag", "armor", "add", 5.0)

    assert app.stats_system.get_stat(entity_id, "armor", app.world) == 15.0


def test_boot_wires_inventory_and_stats_together(tmp_path):
    """Proves InventorySystem/SetTrackerSystem were constructed against the
    *same* real StatsSystem instance (equip/unequip actually mutates
    derived stats), not left as disconnected objects."""
    (tmp_path / "items").mkdir()
    (tmp_path / "items" / "test_ring.json").write_text(
        '{"id": "test_ring", "display_name": "Test Ring", "item_type": "ring", '
        '"equippable": true, "slot": "ring", "min_depth": 1, "max_depth": 20, '
        '"set_id": null, "stat_modifiers": [{"stat": "armor", "operation": "add", "value": 7}]}'
    )
    app = Application(data_root=tmp_path)
    app.boot()

    from engine.systems.loot import spawn_item_instance

    actor = app.world.create_entity()
    app.world.add_component(actor, InventoryComponent())
    app.world.add_component(actor, StatsComponent(base={"armor": 0.0}, modifiers={}))
    inv = app.world.get_component(actor, InventoryComponent)

    instance_id = spawn_item_instance(
        "test_ring", depth=1, position=(0, 0), world=app.world, registry=app.registry
    )
    inv.item_instance_ids.append(instance_id)
    assert app.inventory_system.equip(actor, instance_id, "ring") is True

    assert app.stats_system.get_stat(actor, "armor", app.world) == 7.0


def test_boot_wires_campaign_complete_to_game_complete(tmp_path):
    """06's own doc left `game_complete` "for the entry point" to decide
    (engine/systems/campaign.py's module docstring) -- proves this entry
    point actually makes that decision, through the real event bus."""
    app = Application(data_root=tmp_path)
    app.boot()

    game_complete_events = []
    app.event_bus.subscribe("game_complete", lambda payload: game_complete_events.append(payload))

    app.event_bus.emit("campaign_complete", {"campaign_id": "test_campaign"})

    assert len(game_complete_events) == 1
