"""Synchronous, in-process event bus. See CONTRACTS.md §3 for the contract
this implements exactly — do not deviate from it, every other component
codes directly against this module's ``subscribe``/``emit``/``unsubscribe``.

Naming convention (binding for every new event, CONTRACTS.md §3.1):
``noun_verbedpast`` (e.g. ``item_picked_up``, ``floor_changed``,
``status_applied``), with ``_pending`` reserved for the one sanctioned
"waiting on UI" exception (``level_up_pending``, ``spell_choice_pending``).
This module doesn't enforce that convention in code (event names are
free-form strings by design, so any system can introduce a new one) — it's
enforced by review against CONTRACTS.md §3.2's canonical event table.
"""

from __future__ import annotations

from typing import Callable

Handler = Callable[[dict | None], None]


class EventBus:
    """A single pub/sub channel keyed by event-type string.

    Handlers run synchronously, in subscription order. A snapshot of the
    handler list for an event type is taken *before* dispatch begins, so a
    handler that subscribes a new handler for the same event type mid-
    dispatch does not have that new handler invoked for the event
    currently being dispatched — only for future ``emit`` calls.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[tuple[int, Handler]]] = {}
        self._next_token: int = 1

    def subscribe(self, event_type: str, handler: Handler) -> int:
        """Register ``handler`` for ``event_type``. Returns a token usable
        with :meth:`unsubscribe`."""
        token = self._next_token
        self._next_token += 1
        self._handlers.setdefault(event_type, []).append((token, handler))
        return token

    def emit(self, event_type: str, payload: dict | None = None) -> None:
        """Dispatch ``payload`` to every handler currently subscribed to
        ``event_type``, in subscription order.

        Payloads are ``dict`` or ``None`` by convention (CONTRACTS.md §3) —
        never a bespoke object.
        """
        handlers = list(self._handlers.get(event_type, ()))  # snapshot
        for _token, handler in handlers:
            handler(payload)

    def unsubscribe(self, token: int) -> None:
        """Remove a previously subscribed handler by its token. A no-op if
        the token doesn't exist (already unsubscribed, or never valid) —
        callers doing teardown shouldn't need to guard this."""
        for handlers in self._handlers.values():
            for index, (existing_token, _handler) in enumerate(handlers):
                if existing_token == token:
                    del handlers[index]
                    return


# The event bus is one of the few sanctioned pieces of global mutable state
# (CONTRACTS.md §9) — every system imports the module-level functions below
# rather than constructing its own EventBus, so there is exactly one bus per
# process. Tests that need isolation should construct their own EventBus
# instance directly instead of using these module-level wrappers.
_default_bus = EventBus()


def subscribe(event_type: str, handler: Handler) -> int:
    return _default_bus.subscribe(event_type, handler)


def emit(event_type: str, payload: dict | None = None) -> None:
    _default_bus.emit(event_type, payload)


def unsubscribe(token: int) -> None:
    _default_bus.unsubscribe(token)


def reset() -> None:
    """Clear all subscriptions on the default bus. Test-only convenience —
    gameplay code should never need to reset the bus mid-run."""
    _default_bus._handlers.clear()
    _default_bus._next_token = 1
