"""Unit tests for engine.audio.audio_system -- see
docs/components/07-input-renderer-audio.md §8/§9 for the Definition of
Done bullets these correspond to.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

import engine.audio.audio_system as audio_system_module
from engine.audio.audio_system import AudioSystem
from engine.core.events import EventBus

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture(scope="module", autouse=True)
def _pygame_headless():
    pygame.init()
    pygame.mixer.init()
    yield
    pygame.quit()


@pytest.fixture(autouse=True)
def _reset_missing_cache():
    # Module-level "log once"/missing-file caches are process-lifetime by
    # design (§2.4) -- reset between tests so one test's misses don't
    # silently short-circuit another's assertions.
    audio_system_module._missing_audio_cache.clear()
    audio_system_module._warned_missing_audio.clear()
    yield


def load_fixture_config() -> dict:
    with (FIXTURES / "audio_config_minimal.json").open() as fh:
        return json.load(fh)


class FakeRegistry:
    def __init__(self, configs: dict[str, dict]) -> None:
        self._configs = configs

    def get(self, namespace: str, id: str):
        assert namespace == "configs"
        return self._configs.get(id)


def test_subscribes_to_every_configured_event_plus_play_sound():
    bus = EventBus()
    registry = FakeRegistry({"audio_config": load_fixture_config()})
    AudioSystem(bus, registry)

    assert "damage_dealt" in bus._handlers
    assert "miss" in bus._handlers
    assert "play_sound" in bus._handlers


def test_missing_config_falls_back_to_play_sound_only():
    bus = EventBus()
    registry = FakeRegistry({})
    AudioSystem(bus, registry)

    assert "play_sound" in bus._handlers
    assert "damage_dealt" not in bus._handlers


def test_default_sound_played_on_config_mapped_event():
    bus = EventBus()
    registry = FakeRegistry({"audio_config": load_fixture_config()})
    audio = AudioSystem(bus, registry)

    with patch.object(audio, "play") as mock_play:
        bus.emit("miss", {"attacker_id": 1, "defender_id": 2})

    mock_play.assert_called_once_with("sfx/whiff.ogg", None)


def test_by_damage_type_sub_lookup():
    bus = EventBus()
    registry = FakeRegistry({"audio_config": load_fixture_config()})
    audio = AudioSystem(bus, registry)

    with patch.object(audio, "play") as mock_play:
        bus.emit("damage_dealt", {"target_id": 1, "damage_type": "fire", "amount": 5})

    mock_play.assert_called_once_with("sfx/hit_fire.ogg", None)


def test_by_effect_id_sub_lookup():
    bus = EventBus()
    config = {
        "event_sounds": {
            "status_applied": {
                "default": "sfx/status_generic.ogg",
                "by_effect_id": {"poisoned": "sfx/status_poison.ogg"},
            }
        }
    }
    registry = FakeRegistry({"audio_config": config})
    audio = AudioSystem(bus, registry)

    with patch.object(audio, "play") as mock_play:
        bus.emit("status_applied", {"entity_id": 1, "effect_id": "poisoned", "instance_id": "x", "duration": 3})

    mock_play.assert_called_once_with("sfx/status_poison.ogg", None)


def test_by_damage_type_falls_back_to_default_when_unmapped():
    bus = EventBus()
    registry = FakeRegistry({"audio_config": load_fixture_config()})
    audio = AudioSystem(bus, registry)

    with patch.object(audio, "play") as mock_play:
        bus.emit("damage_dealt", {"target_id": 1, "damage_type": "cold", "amount": 5})

    mock_play.assert_called_once_with("sfx/hit_generic.ogg", None)


def test_payload_sound_override_wins_over_config_mapping():
    bus = EventBus()
    registry = FakeRegistry({"audio_config": load_fixture_config()})
    audio = AudioSystem(bus, registry)

    with patch.object(audio, "play") as mock_play:
        bus.emit(
            "damage_dealt",
            {"target_id": 1, "damage_type": "fire", "sound": "sfx/goblin_death_2.ogg"},
        )

    mock_play.assert_called_once_with("sfx/goblin_death_2.ogg", None)


def test_play_sound_universal_channel():
    bus = EventBus()
    registry = FakeRegistry({"audio_config": load_fixture_config()})
    audio = AudioSystem(bus, registry)

    with patch.object(audio, "play") as mock_play:
        bus.emit("play_sound", {"sound_id": "sfx/custom.ogg", "position": (3, 4)})

    mock_play.assert_called_once_with("sfx/custom.ogg", (3, 4))


def test_position_passed_through_to_play():
    bus = EventBus()
    registry = FakeRegistry({"audio_config": load_fixture_config()})
    audio = AudioSystem(bus, registry)

    with patch.object(audio, "play") as mock_play:
        bus.emit("miss", {"attacker_id": 1, "defender_id": 2, "position": (10, 20)})

    mock_play.assert_called_once_with("sfx/whiff.ogg", (10, 20))


# -- missing-file caching (§2.4, §8) ------------------------------------------


def test_missing_file_is_cached_after_first_check():
    bus = EventBus()
    registry = FakeRegistry({})
    audio = AudioSystem(bus, registry)

    with patch("pathlib.Path.exists", return_value=False) as mock_exists:
        for _ in range(5):
            audio.play("sfx/does_not_exist.ogg")

    mock_exists.assert_called_once()


def test_missing_file_never_attempts_to_load_sound():
    bus = EventBus()
    registry = FakeRegistry({})
    audio = AudioSystem(bus, registry)

    with patch("pathlib.Path.exists", return_value=False):
        with patch("pygame.mixer.Sound") as mock_sound:
            audio.play("sfx/does_not_exist.ogg")
            audio.play("sfx/does_not_exist.ogg")

    mock_sound.assert_not_called()


def test_existing_file_is_played():
    bus = EventBus()
    registry = FakeRegistry({})
    audio = AudioSystem(bus, registry)

    fake_sound = MagicMock()
    with patch("pathlib.Path.exists", return_value=True):
        with patch("pygame.mixer.Sound", return_value=fake_sound) as mock_sound_cls:
            audio.play("sfx/hit_generic.ogg")

    mock_sound_cls.assert_called_once()
    fake_sound.play.assert_called_once()


def test_no_sound_id_is_a_noop():
    bus = EventBus()
    registry = FakeRegistry({"audio_config": load_fixture_config()})
    audio = AudioSystem(bus, registry)

    with patch("pygame.mixer.Sound") as mock_sound:
        bus.emit("play_sound", {"position": (1, 1)})

    mock_sound.assert_not_called()
