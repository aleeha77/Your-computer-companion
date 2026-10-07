"""Emotions subsystem package.

Stage 6 implements the full emotion dynamics:

* :mod:`peeko.emotions.state` — the data model (PAD space + named emotions);
* :mod:`peeko.emotions.engine` — :class:`~peeko.emotions.engine.EmotionEngine`,
  which owns a live :class:`EmotionalState` and a live
  :class:`peeko.needs.system.PetNeeds`, drifts both over an injectable clock,
  applies documented interaction effects and reports the six ``0..100`` stats
  the chat context and Check Status show.

The engine is in-memory for one run; saving and restoring it is Stage 8
(persistent memory).
"""

from peeko.emotions.engine import (
    CHAT_INTERACTION,
    INTERACTION_EFFECTS,
    EmotionEngine,
    EmotionSnapshot,
    InteractionEffect,
)
from peeko.emotions.state import EmotionalState, Emotion

__all__ = [
    "CHAT_INTERACTION",
    "INTERACTION_EFFECTS",
    "Emotion",
    "EmotionEngine",
    "EmotionSnapshot",
    "EmotionalState",
    "InteractionEffect",
]
