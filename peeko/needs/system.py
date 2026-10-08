"""Needs subsystem: Peeko's virtual-pet needs (hunger, sleep, ...) and food.

Stage 7 makes the needs simulation real end to end. This module is the data
model and the documented tuning values:

* :class:`PetNeeds` — the five needs, each in ``[0, 100]``, where **100 means
  fully satisfied** and ``0`` means the need is at its worst (this is why
  ``sleepiness`` is the *inverse* of the ``sleep`` need; see
  :meth:`peeko.emotions.engine.EmotionEngine.sleepiness`);
* :data:`DECAY_PER_HOUR` — how fast each need falls while Peeko is awake;
* :data:`SLEEP_RECOVERY_PER_HOUR` — how fast the ``sleep`` and ``energy``
  needs *recover* while Peeko is asleep, and the needs that freeze entirely
  while he naps (:data:`FROZEN_WHILE_ASLEEP`);
* :data:`FOODS` — the feeding catalogue the Feed menu offers: each food's
  documented deltas on hunger, energy and mood, and which ones are junk.

The behaviour those values drive (yawning, auto-sleep, boredom nudges) lives
in :mod:`peeko.needs.behaviour`; the engine that applies them and exposes them
to the UI is :class:`peeko.emotions.engine.EmotionEngine`. Nothing here is
persisted yet — Stage 8 (persistent memory) is what will save and restore it.
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

#: Per-hour decay rates while Peeko is **awake** (simple linear model).
#: ``boredom`` is how *entertained* Peeko is, so it falls fastest of all: an
#: idle robot gets bored quickly, which is what the Stage 7 nudge limit in
#: :mod:`peeko.needs.behaviour` keeps civil.
DECAY_PER_HOUR: dict[Need, float] = {
    Need.HUNGER: 4.0,
    Need.SLEEP: 3.5,
    Need.BOREDOM: 5.0,
    Need.ENERGY: 3.0,
    Need.FRIENDSHIP: 1.0,
}

#: Per-hour **recovery** while Peeko is asleep (positive = more satisfied).
#: Sleeping is the only way the ``sleep`` need comes back; it also restores
#: energy, which is why a nap is worth taking and not just cosmetic.
SLEEP_RECOVERY_PER_HOUR: dict[Need, float] = {
    Need.SLEEP: 15.0,
    Need.ENERGY: 12.0,
}

#: Needs that do not change at all while Peeko is asleep: a sleeping pet
#: cannot be bored and does not feel lonely. Hunger is *not* here — a sleeping
#: pet still gets hungry, which is documented and observable in the tests.
FROZEN_WHILE_ASLEEP: tuple[Need, ...] = (Need.BOREDOM, Need.FRIENDSHIP)

#: Hunger at or above this (0..100, 100 = full) means Peeko refuses food.
#: Feeding him anyway would waste the food, so the engine says so instead.
FULL_HUNGER = 95.0


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

    def tick(self, hours: float, *, sleeping: bool = False) -> None:
        """Advance the simulated clock by ``hours``.

        Awake (the default) each need decays by :data:`DECAY_PER_HOUR`. Asleep
        (``sleeping=True``) the ``sleep`` and ``energy`` needs *recover* by
        :data:`SLEEP_RECOVERY_PER_HOUR`, hunger keeps decaying normally, and
        the needs in :data:`FROZEN_WHILE_ASLEEP` do not move at all. Values
        still clamp to ``[0, 100]``, and time never runs backwards.
        """
        if hours < 0:
            raise ValueError(f"hours must be >= 0, got {hours!r}")
        if sleeping:
            for need, rate in SLEEP_RECOVERY_PER_HOUR.items():
                self.set(need, self.get(need) + rate * hours)
            for need, rate in DECAY_PER_HOUR.items():
                if need in SLEEP_RECOVERY_PER_HOUR or need in FROZEN_WHILE_ASLEEP:
                    continue
                self.set(need, self.get(need) - rate * hours)
            return
        for need, rate in DECAY_PER_HOUR.items():
            self.set(need, self.get(need) - rate * hours)

    def is_full(self, threshold: float = FULL_HUNGER) -> bool:
        """Is Peeko too full to eat? (``hunger`` at/above ``threshold``.)"""
        return self.get(Need.HUNGER) >= threshold

    def lowest(self) -> Need:
        """The need in most urgent state (smallest value)."""
        return min(self.values, key=self.values.get)

    def summary(self) -> dict[str, float]:
        return {need.value: round(value, 1)
                for need, value in self.values.items()}


# --------------------------------------------------------------------------- #
# Food catalogue (Stage 7)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Food:
    """One food the Feed menu can offer, with its documented deltas.

    ``hunger`` / ``energy`` are deltas on the 0..100 need scale (positive =
    more satisfied); ``pleasure`` / ``arousal`` are PAD deltas (unit cube),
    exactly like :class:`peeko.emotions.engine.InteractionEffect`. Nothing
    here is randomised: the same food always does the same thing, so the
    README table, the picker and the tests all agree.
    """

    id: str
    label: str
    emoji: str
    summary: str
    hunger: float
    energy: float = 0.0
    pleasure: float = 0.0
    arousal: float = 0.0
    friendship: float = 0.0
    boredom: float = 0.0
    junk: bool = False

    def detail(self) -> str:
        """A one-line, honest description of what eating this really does."""
        parts = [f"hunger +{self.hunger:.0f}"]
        if self.energy:
            parts.append(f"energy {self.energy:+.0f}")
        if self.friendship:
            parts.append(f"friendship {self.friendship:+.0f}")
        if self.boredom:
            parts.append(f"boredom {self.boredom:+.0f}")
        if self.pleasure:
            parts.append(f"happiness {self.pleasure * 50:+.0f}")
        if self.junk:
            parts.append("junk food")
        return f"{self.summary} ({', '.join(parts)})"


#: The five foods, in the order the Feed picker shows them. The values are
#: the Stage 7 tuning: healthy food restores hunger *and* energy, junk food
#: fills Peeko up faster and tastes better but costs energy later (the
#: documented "sugar rush, then a dip" for the cookie), and milk is the light
#: option that also calms him down (negative arousal).
FOODS: dict[str, Food] = {
    "apple": Food(
        id="apple", label="Apple", emoji="🍎",
        summary="crisp and healthy — a steady, sensible snack",
        hunger=18.0, energy=4.0, pleasure=0.08,
    ),
    "pizza": Food(
        id="pizza", label="Pizza", emoji="🍕",
        summary="a whole slice — very filling, and Peeko loves it",
        hunger=30.0, energy=4.0, pleasure=0.16, arousal=0.04,
        boredom=2.0, junk=True,
    ),
    "cookie": Food(
        id="cookie", label="Cookie", emoji="🍪",
        summary="sweet, tiny, and gone in one bite — a rush now, a dip after",
        hunger=8.0, energy=-1.0, pleasure=0.15, friendship=1.0, junk=True,
    ),
    "burger": Food(
        id="burger", label="Burger", emoji="🍔",
        summary="a proper meal — the most filling thing in the menu",
        hunger=35.0, energy=6.0, pleasure=0.14, arousal=0.03,
        boredom=2.0, junk=True,
    ),
    "milk": Food(
        id="milk", label="Milk", emoji="🥛",
        summary="light and soothing — settles him down for a nap",
        hunger=12.0, energy=5.0, pleasure=0.05, arousal=-0.05,
    ),
}

#: Food ids in menu/picker order.
FOOD_ORDER: tuple[str, ...] = tuple(FOODS)


def find_food(food_id: str) -> Food | None:
    """Look a food up by id; ``None`` when it is not on the menu."""
    return FOODS.get(food_id) if isinstance(food_id, str) else None


__all__ = [
    "DECAY_PER_HOUR",
    "DEFAULT_VALUES",
    "FOODS",
    "FOOD_ORDER",
    "FROZEN_WHILE_ASLEEP",
    "FULL_HUNGER",
    "Food",
    "Need",
    "PetNeeds",
    "SLEEP_RECOVERY_PER_HOUR",
    "find_food",
]
