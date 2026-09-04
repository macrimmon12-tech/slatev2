"""Process entry point.

Wave 0 provides just enough here for the game to "boot": construct the
``World``, wire up the event bus, and load the ``DataRegistry`` from
``data/`` — then run an empty loop that does nothing and crashes on
nothing, proving "absence = zero cost" (CONTRACTS.md §2 rule 7) holds even
with zero real content loaded.

Out of scope for this component (00-foundation-core.md §1): real system
wiring, rendering, input, audio. Each Wave 1+ component attaches its own
systems/event subscriptions here as it lands — this file stays
deliberately thin; it is not the place to grow gameplay logic.
"""

from __future__ import annotations

import logging
from pathlib import Path

from engine.core.ecs import World
from engine.core.registry import DataRegistry

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data"


class Application:
    """Holds the process-wide singletons every system wires against.

    Wave 1+ components attach their own systems/subscriptions to an
    ``Application`` instance (or to the module-level ``engine.core.events``
    bus directly) rather than this class growing new responsibilities of
    its own — see CONTRACTS.md §2 rule 4 (systems never call each other
    directly; only the event bus or shared components).
    """

    def __init__(self, data_root: Path = DEFAULT_DATA_ROOT) -> None:
        self.world = World()
        self.registry = DataRegistry()
        self.data_root = data_root
        self._running = False

    def boot(self) -> None:
        """Load content and get ready to run.

        Safe to call against a ``data_root`` with little or no content —
        an empty/near-empty data tree loads zero entries per namespace and
        still boots cleanly (CONTRACTS.md §2 rule 7).
        """
        self.registry.load(self.data_root)
        logger.info("SLATE v2 booted (data_root=%s)", self.data_root)

    def tick(self) -> None:
        """One iteration of the game loop.

        Wave 0 has no gameplay systems to run, so this is currently a
        no-op placeholder — later components extend it (or hook in purely
        via event subscriptions, needing no change here at all).
        """

    def run(self, max_ticks: int | None = None) -> None:
        """Run the loop.

        No input/renderer is wired yet (that lands in
        ``07-input-renderer-audio.md``), so there is no real "quit" signal
        available to a bare, unbounded ``run()``. Pass ``max_ticks`` (used
        by this module's own smoke run below, and by tests) to make the
        loop terminate on its own instead of relying on a quit path that
        doesn't exist yet.
        """
        self._running = True
        ticks = 0
        while self._running:
            self.tick()
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                self.stop()

    def stop(self) -> None:
        self._running = False


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    app = Application()
    app.boot()
    # Run a bounded number of empty ticks so `python -m engine.main`
    # demonstrates a clean boot-and-exit with zero content loaded, rather
    # than hanging forever waiting for a quit signal nothing can send yet.
    app.run(max_ticks=1)
    logger.info("SLATE v2 exiting cleanly, zero content loaded.")


if __name__ == "__main__":
    main()
