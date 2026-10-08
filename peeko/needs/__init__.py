"""Needs subsystem package (virtual-pet needs, food and needs-driven behaviour).

Stage 7 added feeding and sleeping, so the package now exposes three things:
the need model (:mod:`peeko.needs.system`), the food catalogue in the same
module, and the behaviour rules — thresholds and rate limits for yawning,
auto-sleep and attention-seeking — in :mod:`peeko.needs.behaviour`.
"""

from peeko.needs.behaviour import (
    EVENT_ATTENTION,
    EVENT_AUTO_SLEEP,
    EVENT_AUTO_WAKE,
    EVENT_KINDS,
    EVENT_YAW,
    NeedsBehaviour,
    NeedsEvent,
)
from peeko.needs.system import (
    DECAY_PER_HOUR,
    DEFAULT_VALUES,
    FOOD_ORDER,
    FOODS,
    FROZEN_WHILE_ASLEEP,
    FULL_HUNGER,
    SLEEP_RECOVERY_PER_HOUR,
    Food,
    Need,
    PetNeeds,
    find_food,
)

__all__ = [
    "DECAY_PER_HOUR",
    "DEFAULT_VALUES",
    "EVENT_ATTENTION",
    "EVENT_AUTO_SLEEP",
    "EVENT_AUTO_WAKE",
    "EVENT_KINDS",
    "EVENT_YAW",
    "FOODS",
    "FOOD_ORDER",
    "FROZEN_WHILE_ASLEEP",
    "FULL_HUNGER",
    "Food",
    "Need",
    "NeedsBehaviour",
    "NeedsEvent",
    "PetNeeds",
    "SLEEP_RECOVERY_PER_HOUR",
    "find_food",
]
