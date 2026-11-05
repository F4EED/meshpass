"""Horloge injectable pour les délais de filtre et les tests."""

from __future__ import annotations

import time


class Clock:
    def monotonic(self) -> float:
        return time.monotonic()

    def wall(self) -> float:
        return time.time()


class FakeClock:
    def __init__(self, mono: float = 1_000_000.0, wall: float = 1_700_000_000.0) -> None:
        self._mono = mono
        self._wall = wall

    def monotonic(self) -> float:
        return self._mono

    def wall(self) -> float:
        return self._wall

    def advance(self, seconds: float) -> None:
        self._mono += seconds
        self._wall += seconds
