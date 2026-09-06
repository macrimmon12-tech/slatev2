"""Shared pytest fixtures/setup.

Sets a headless SDL video/audio driver before any test imports ``pygame``,
so the full suite (including component 09's ``AnimSystem.draw()`` tests,
which construct a real ``pygame.Surface``) runs without a display or audio
device -- required in CI/containers and harmless locally. ``setdefault``
so a developer's own environment override (e.g. wanting a real window for
manual debugging) still wins.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
