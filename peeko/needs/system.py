"""Needs subsystem: Peeko's virtual-pet needs (hunger, sleep, ...).

Stage 7 (full needs simulation driving behaviour): NOT IMPLEMENTED YET —
feeding, sleeping and their UI are still to come. The core data model below
is real: needs live in [0, 100] and decay over real time via
:meth:`PetNeeds.tick`. Stage 6 already owns a live :class:`PetNeeds` inside
:class:`peeko.emotions.engine.EmotionEngine` and drifts it while the app
runs, so these values are read live by the chat context and Check Status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Need(Enum):
    """The virtual-pet needs Peeko tracks."""

    HUNGER = "hunger"
    SLEEP = "sleep"
    BOREDOM = "boredom"
    ENERGY = "energy"
    FRIENDSHIP = "friendship"


#: Default starting value for each need, 0 = starved, 100 = satiated.
DEFAULT_VALUES: dict[Need, float] = {
    Need.HUNGER: 80.0,
    Need.SLEEP: 90.0,
    Need.BOREDOM: 70.0,
    Need.ENERGY: 80.0,
    Need.FRIENDSHIP: 50.0,
}

#: Per-hour decay rates (Stage 7 tuning values; simple linear model).
DECAY_PER_HOUR: dict[Need, float] = {
    Need.HUNGER: 4.0,
    Need.SLEEP: 3.5,
    Need.BOREDOM: 5.0,
    Need.ENERGY: 3.0,
    Need.FRIENDSHIP: 1.0,
}


@dataclass
class PetNeeds:
    """Needs state for one pet session, values clamped to [0, 100]."""

    values: dict[Need, float] = field(
        default_factory=lambda: dict(DEFAULT_VALUES)
    )

    def get(self, need: Need) -> float:
        return self.values[need]

    def set(self, need: Need, value: float) -> None:
        self.values[need] = max(0.0, min(100.0, value))

    def tick(self, hours: float) -> None:
        """Advance the simulated clock by ``hours``, decaying each need."""
        if hours < 0:
            raise ValueError(f"hours must be >= 0, got {hours!r}")
        for need, rate in DECAY_PER_HOUR.items():
            self.set(need, self.get(need) - rate * hours)

    def lowest(self) -> Need:
        """The need in most urgent state (smallest value)."""
        return min(self.values, key=self.values.get)

    def summary(self) -> dict[str, float]:
        return {need.value: round(value, 1)
                for need, value in self.values.items()}