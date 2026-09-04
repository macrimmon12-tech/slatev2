from engine.core.events import EventBus, emit, reset, subscribe, unsubscribe


def test_subscribe_and_emit_calls_handler_with_payload():
    bus = EventBus()
    received = []
    bus.subscribe("thing_happened", lambda payload: received.append(payload))
    bus.emit("thing_happened", {"x": 1})
    assert received == [{"x": 1}]


def test_emit_with_no_subscribers_is_a_noop():
    bus = EventBus()
    bus.emit("nobody_listening", {"a": 1})  # must not raise


def test_emit_with_none_payload():
    bus = EventBus()
    received = []
    bus.subscribe("event", lambda payload: received.append(payload))
    bus.emit("event")
    assert received == [None]


def test_handlers_run_in_subscription_order():
    bus = EventBus()
    order = []
    bus.subscribe("e", lambda _p: order.append("first"))
    bus.subscribe("e", lambda _p: order.append("second"))
    bus.subscribe("e", lambda _p: order.append("third"))
    bus.emit("e")
    assert order == ["first", "second", "third"]


def test_unsubscribe_removes_handler():
    bus = EventBus()
    received = []
    token = bus.subscribe("e", lambda payload: received.append(payload))
    bus.unsubscribe(token)
    bus.emit("e", {"x": 1})
    assert received == []


def test_unsubscribe_unknown_token_is_a_noop():
    bus = EventBus()
    bus.unsubscribe(99999)  # must not raise


def test_unsubscribe_only_removes_matching_token():
    bus = EventBus()
    received = []
    token_a = bus.subscribe("e", lambda _p: received.append("a"))
    bus.subscribe("e", lambda _p: received.append("b"))
    bus.unsubscribe(token_a)
    bus.emit("e")
    assert received == ["b"]


def test_snapshot_before_dispatch_excludes_handler_subscribed_mid_dispatch():
    """A handler that subscribes a new handler while an event is being
    dispatched must not have that new handler receive the *same* dispatch
    — CONTRACTS.md §3: the handler list is snapshotted before dispatch
    begins."""
    bus = EventBus()
    received = []

    def late_handler(payload):
        received.append(("late", payload))

    def subscribing_handler(payload):
        received.append(("subscriber", payload))
        bus.subscribe("e", late_handler)

    bus.subscribe("e", subscribing_handler)

    bus.emit("e", {"n": 1})
    assert received == [("subscriber", {"n": 1})]  # late_handler did NOT fire

    bus.emit("e", {"n": 2})
    assert received == [
        ("subscriber", {"n": 1}),
        ("subscriber", {"n": 2}),
        ("late", {"n": 2}),
    ]


def test_multiple_event_types_are_independent():
    bus = EventBus()
    received = []
    bus.subscribe("type_a", lambda p: received.append(("a", p)))
    bus.subscribe("type_b", lambda p: received.append(("b", p)))
    bus.emit("type_a", {"v": 1})
    assert received == [("a", {"v": 1})]


def test_module_level_default_bus_wrappers():
    reset()
    try:
        received = []
        token = subscribe("module_event", lambda payload: received.append(payload))
        emit("module_event", {"ok": True})
        assert received == [{"ok": True}]
        unsubscribe(token)
        emit("module_event", {"ok": False})
        assert received == [{"ok": True}]
    finally:
        reset()
