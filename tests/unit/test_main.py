"""Covers this component's Definition of Done item: ``engine/main.py``
boots to an empty running loop with zero content loaded and doesn't
crash — "absence = zero cost" holding at the foundation level."""

from engine.main import Application


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
