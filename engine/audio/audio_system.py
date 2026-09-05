"""AudioSystem: a thin, data-driven ``pygame.mixer`` wrapper.

See ``docs/components/07-input-renderer-audio.md`` §2.4 for the full spec.
All event->sound pairing lives in ``data/config/audio_config.json`` -- this
module never hardcodes a pairing in Python. Per CONTRACTS.md §2 rule 7
("absence = zero cost"), a missing sound file, a missing audio device, or a
missing/empty config must never raise, block, or re-stat the filesystem
after the first miss.

Coordination note: this component and `09-animation-vfx.md` both subscribe
to the same combat/status events (§3's boundary note) -- each reads only
the fields it cares about, no coordination needed (CONTRACTS.md §3
dispatches to every subscriber independently). This module never inspects
``vfx_play``'s visual fields, only whether a sound is mapped to it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pygame

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ASSETS_ROOT = PROJECT_ROOT / "assets"

# path (str) -> True if that path is known missing/unplayable. Module-level
# and permanent for the life of the process (§2.4's exact requirement):
# once a path is cached here, `play()` never touches the filesystem for it
# again.
_missing_audio_cache: dict[str, bool] = {}
_warned_missing_audio: set[str] = set()
_warned_no_mixer: set[str] = set()
_warned_config_missing: set[str] = set()


def _is_missing(resolved_path: Path) -> bool:
    """The one place this module checks the filesystem for a sound file.
    Cached forever per path -- see module docstring."""
    key = str(resolved_path)
    cached = _missing_audio_cache.get(key)
    if cached is not None:
        return cached

    missing = not resolved_path.exists()
    _missing_audio_cache[key] = missing
    if missing and key not in _warned_missing_audio:
        logger.warning("Missing audio file %s; caching as permanently unplayable.", resolved_path)
        _warned_missing_audio.add(key)
    return missing


class AudioSystem:
    """Loads ``data/config/audio_config.json``, subscribes to every event
    it names plus the universal ``play_sound`` override channel, and plays
    sounds through ``pygame.mixer``."""

    def __init__(self, event_bus: Any, registry: Any) -> None:
        self._event_bus = event_bus
        self._registry = registry

        config = registry.get("configs", "audio_config") if registry is not None else None
        if not config:
            if "audio_config_missing" not in _warned_config_missing:
                logger.warning(
                    "data/config/audio_config.json missing/empty; AudioSystem will "
                    "subscribe to 'play_sound' only, with no config-mapped sounds."
                )
                _warned_config_missing.add("audio_config_missing")
            config = {}
        self._event_sounds: dict[str, dict] = config.get("event_sounds", {}) or {}

        self._mixer_ok = self._init_mixer()

        self._subscriptions: list[int] = []
        for event_name in self._event_sounds:
            self._subscriptions.append(
                event_bus.subscribe(event_name, self._make_config_handler(event_name))
            )
        self._subscriptions.append(event_bus.subscribe("play_sound", self._on_play_sound))

    @staticmethod
    def _init_mixer() -> bool:
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            return True
        except pygame.error:
            if "no_mixer" not in _warned_no_mixer:
                logger.warning("No audio device available; AudioSystem.play() will no-op.")
                _warned_no_mixer.add("no_mixer")
            return False

    # -- public API (component doc §2.4) -------------------------------------

    def play(self, sound_id: str, position: tuple[int, int] | None = None) -> None:
        """Play ``sound_id`` (a path relative to ``assets/``). ``position``
        is accepted for the documented signature/future positional-audio
        use but not otherwise consulted -- pygame.mixer here is
        non-positional stereo playback."""
        if not self._mixer_ok or not sound_id:
            return

        resolved_path = ASSETS_ROOT / sound_id
        if _is_missing(resolved_path):
            return

        try:
            sound = pygame.mixer.Sound(str(resolved_path))
            sound.play()
        except pygame.error:
            # Existed per the stat above but failed to load (corrupt/
            # unsupported format) -- cache as missing too so this doesn't
            # retry every frame.
            _missing_audio_cache[str(resolved_path)] = True
            if str(resolved_path) not in _warned_missing_audio:
                logger.warning("Audio file %s failed to load; caching as permanently unplayable.", resolved_path)
                _warned_missing_audio.add(str(resolved_path))

    # -- event handling ---------------------------------------------------------

    def _make_config_handler(self, event_name: str):
        def handler(payload: dict | None) -> None:
            payload = payload or {}
            sound_id = payload.get("sound")
            if sound_id is None:
                sound_id = self._resolve_from_config(event_name, payload)
            if sound_id is None:
                return
            self.play(sound_id, payload.get("position"))

        return handler

    def _resolve_from_config(self, event_name: str, payload: dict) -> str | None:
        spec = self._event_sounds.get(event_name)
        if not spec:
            return None

        damage_type = payload.get("damage_type")
        by_damage_type = spec.get("by_damage_type")
        if damage_type and by_damage_type and damage_type in by_damage_type:
            return by_damage_type[damage_type]

        effect_id = payload.get("effect_id")
        by_effect_id = spec.get("by_effect_id")
        if effect_id and by_effect_id and effect_id in by_effect_id:
            return by_effect_id[effect_id]

        return spec.get("default")

    def _on_play_sound(self, payload: dict | None) -> None:
        payload = payload or {}
        # CONTRACTS.md §3.2 documents this event's own payload key as
        # "sound_id"; also accept "sound" for consistency with the
        # per-payload override key every other subscribed event uses.
        sound_id = payload.get("sound_id", payload.get("sound"))
        if sound_id is None:
            return
        self.play(sound_id, payload.get("position"))


__all__ = ["AudioSystem"]
