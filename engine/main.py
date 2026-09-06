"""Process entry point.

Wave 0 provided just enough here for the game to "boot": construct the
``World``, wire up the event bus, and load the ``DataRegistry`` from
``data/`` — then run an empty loop that does nothing and crashes on
nothing, proving "absence = zero cost" (CONTRACTS.md §2 rule 7) holds even
with zero real content loaded. Every Wave 1+ component's own doc said
"Each Wave 1+ component attaches its own systems/event subscriptions
here as it lands" (00-foundation-core.md §1) — in practice none of them
did (each built and tested its system in isolation, per its own doc's
Test Plan, and left the actual cross-system assembly for later). That is
exactly the "built but not wired" failure §11/CONTRACTS.md §7 exists to
catch, generalized to the process entry point itself, so
15-integration-verification.md's pass wires the real gameplay systems
together here.

``Application.boot()`` now constructs and cross-wires the real Wave 1
gameplay systems (stats, combat, spells, status effects, inventory, sets,
loot placement, progression, vision, campaign/floor generation, AI turn
processing) against one real ``World``/``EventBus``, using the same
module-level "set the process-wide registry once at boot" convention
several of those systems already established for content they can't
receive through a fixed call signature (``combat.set_data_registry``,
``loot.set_active_registry``, ``worldgen.set_data_registry``,
``effects.set_data_registry``, ``campaign.set_data_registry``,
``vaults.set_data_registry``).

Deliberately still out of scope here (see this component's PR description
for the follow-ups filed against the owning docs): a real pygame
display/input/audio/UI loop. Wiring `07`'s `InputHandler`/`Renderer`/
`AudioSystem` and `08`'s `UIRuntime` into a live frame loop requires
resolution/asset-path/window decisions no doc specifies, and `07`'s own
integration test already proves that trio works together in isolation —
adding it here without a specified UX surface would be inventing scope,
not wiring existing scope. `Application.process_turn()` below is the
headless "advance the game one player action" entry point a real input
loop (or, today, a scripted test) drives.
"""

from __future__ import annotations

import logging
from pathlib import Path

from engine.core.ecs import World
from engine.core.events import EventBus
from engine.core.registry import DataRegistry
from engine.core.spatial_hash import SpatialHash
from engine.modding.load_order import resolve_source_list
from engine.systems import campaign as campaign_module
from engine.systems import combat as combat_module
from engine.systems import effects as effects_module
from engine.systems import loot as loot_module
from engine.systems import vaults as vaults_module
from engine.systems import worldgen as worldgen_module
from engine.systems.campaign import CampaignSystem
from engine.systems.inventory import InventorySystem
from engine.systems.progression import ProgressionSystem
from engine.systems.sets import SetTrackerSystem
from engine.systems.spells import SpellSystem
from engine.systems.stats import StatsSystem
from engine.systems.status import StatusEffectsSystem
from engine.systems.vision import VisionSystem

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data"
DEFAULT_MODS_DIR = PROJECT_ROOT / "mods"


class Application:
    """Holds the process-wide singletons every system wires against.

    Wave 1+ components attach their own systems/subscriptions to an
    ``Application`` instance (or to the module-level ``engine.core.events``
    bus directly) rather than this class growing new responsibilities of
    its own — see CONTRACTS.md §2 rule 4 (systems never call each other
    directly; only the event bus or shared components).
    """

    def __init__(
        self,
        data_root: Path = DEFAULT_DATA_ROOT,
        mods_dir: Path = DEFAULT_MODS_DIR,
        base_archive_dir: Path | None = None,
    ) -> None:
        self.world = World()
        self.event_bus = EventBus()
        self.registry = DataRegistry()
        self.spatial_hash = SpatialHash()
        self.data_root = data_root
        self.mods_dir = mods_dir
        self.base_archive_dir = base_archive_dir
        self._running = False
        self._booted = False

        # Populated by boot() -- real Wave 1 gameplay systems, not fakes.
        self.stats_system: StatsSystem | None = None
        self.status_system: StatusEffectsSystem | None = None
        self.spell_system: SpellSystem | None = None
        self.inventory_system: InventorySystem | None = None
        self.set_tracker_system: SetTrackerSystem | None = None
        self.progression_system: ProgressionSystem | None = None
        self.vision_system: VisionSystem | None = None
        self.campaign_system: CampaignSystem | None = None

    def boot(self) -> None:
        """Load content and construct/cross-wire the real gameplay systems.

        Safe to call against a ``data_root`` with little or no content —
        an empty/near-empty data tree loads zero entries per namespace and
        still boots cleanly (CONTRACTS.md §2 rule 7). Same for ``mods_dir``
        having no ``load_order.txt`` or no mods listed in it, and for
        ``base_archive_dir`` being ``None`` (no base archives shipped) — a
        fresh checkout has zero mods active and boots identically to no
        archive/mod system existing at all.
        """
        tiers = resolve_source_list(
            base_archive_dir=self.base_archive_dir,
            mods_dir=self.mods_dir,
            loose_dirs=[self.data_root],
        )
        self.registry.load_sources(tiers)

        # Process-wide registry holders each of these modules already
        # exposes for exactly this "hand me the real DataRegistry at boot"
        # purpose (see this module's docstring) -- absence of any one of
        # these calls degrades that module to its own documented
        # zero-content fallback (CONTRACTS.md §2 rule 7), it never raises.
        combat_module.set_data_registry(self.registry)
        loot_module.set_active_registry(self.registry)
        worldgen_module.set_data_registry(self.registry)
        effects_module.set_data_registry(self.registry)
        campaign_module.set_data_registry(self.registry)
        vaults_module.set_data_registry(self.registry)

        world = self.world
        event_bus = self.event_bus

        self.stats_system = StatsSystem(world, event_bus)
        self.status_system = StatusEffectsSystem(world, event_bus, registry=self.registry)
        self.spell_system = SpellSystem(
            world, event_bus, registry=self.registry, spatial_hash=self.spatial_hash
        )
        self.inventory_system = InventorySystem(
            world, event_bus, self.registry, stats_system=self.stats_system
        )
        self.set_tracker_system = SetTrackerSystem(
            world, event_bus, self.registry, stats_system=self.stats_system
        )
        self.progression_system = ProgressionSystem(
            world, event_bus, registry=self.registry, stats_system=self.stats_system
        )
        self.vision_system = VisionSystem(
            world, event_bus, stats_system=self.stats_system, spatial_hash=self.spatial_hash
        )
        worldgen_module.set_vision_system(self.vision_system)
        self.campaign_system = CampaignSystem(world, event_bus)

        # 06's own doc left `game_complete` "for the entry point" to decide
        # (see engine/systems/campaign.py's module docstring/§9 Open
        # Questions) -- this is that entry point. Conservative single-
        # campaign default: completing the one loaded campaign completes
        # the game. A future multi-campaign menu can refine this without
        # changing CampaignSystem itself (CONTRACTS.md §2 rule 4).
        event_bus.subscribe("campaign_complete", self._on_campaign_complete)

        self._booted = True
        logger.info(
            "SLATE v2 booted (data_root=%s, mods_dir=%s)", self.data_root, self.mods_dir
        )

    def _on_campaign_complete(self, _payload: dict | None) -> None:
        self.event_bus.emit("game_complete", None)

    def process_monster_turns(self) -> None:
        """Runs every awake monster's AI turn once, through the real
        combat-owned entry point (``combat.process_monster_turns`` --
        see that module's own docstring: it owns the turn-order loop,
        02-ai-system.md owns what each monster decides). This is the
        "AI takes its turn after the player acts" half of a real game
        loop's per-action cycle; a real input/render loop (or, today, a
        scripted test) calls this after dispatching the player's action."""
        combat_module.process_monster_turns(self.world, self.event_bus)

    def tick(self) -> None:
        """One iteration of the outer loop.

        No real-time pacing exists yet (no renderer/clock is wired — see
        this module's docstring), so this stays a no-op placeholder for a
        frame loop; turn-based gameplay advances via
        :meth:`process_monster_turns` and direct system calls
        (``spell_system.cast(...)``, ``inventory_system.equip(...)``, ...)
        driven by input, not by this method.
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
