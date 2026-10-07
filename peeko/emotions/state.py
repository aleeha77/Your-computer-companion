"""Emotions subsystem: Peeko's emotional state model.

Stage 6 (the emotion engine that owns this state, drifts it over time,
applies interaction effects and drives avatar expressions) is implemented in
:mod:`peeko.emotions.engine`; this module is the data model it builds on.

The model uses the well-known PAD (pleasure–arousal–dominance)
dimensional theory of emotion plus a named :class:`Emotion` enum, giving
future stages a concrete, testable substrate for mood dynamics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Emotion(Enum):
    """Named emotions Peeko can express (Stage 6 maps these to animation)."""

    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISED = "surprised"
    TIRED = "tired"
    EXCITED = "excited"

    @property
    def label(self) -> str:
        return self.value


#: Typical PAD coordinates for each named emotion (clamped to [-1, 1]).
EMOTION_PAD: dict[Emotion, tuple[float, float, float]] = {
    Emotion.NEUTRAL: (0.0, 0.0, 0.0),
    Emotion.HAPPY: (0.7, 0.4, 0.5),
    Emotion.SAD: (-0.6, -0.3, -0.4),
    Emotion.ANGRY: (-0.5, 0.6, 0.5),
    Emotion.SURPRISED: (0.2, 0.7, -0.3),
    Emotion.TIRED: (-0.2, -0.6, -0.4),
    Emotion.EXCITED: (0.6, 0.8, 0.3),
}


@dataclass
class EmotionalState:
    """Dimensional emotional state in PAD space, all in [-1, 1].

    Clamped on assignment so the model can never drift out of range.
    """

    pleasure: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.0

    def __post_init__(self) -> None:
        self.pleasure = max(-1.0, min(1.0, self.pleasure))
        self.arousal = max(-1.0, min(1.0, self.arousal))
        self.dominance = max(-1.0, min(1.0, self.dominance))

    @classmethod
    def from_emotion(cls, emotion: Emotion) -> "EmotionalState":
        """Return the canonical PAD state for a named emotion."""
        pleasure, arousal, dominance = EMOTION_PAD[emotion]
        return cls(pleasure=pleasure, arousal=arousal, dominance=dominance)

    def blend(self, other: "EmotionalState", weight: float = 0.5) -> None:
        """Blend ``other`` into this state by ``weight`` (0..1), in place."""
        if not 0.0 <= weight <= 1.0:
            raise ValueError(f"weight must be in [0, 1], got {weight!r}")
        w = weight
        self.pleasure = self.pleasure * (1 - w) + other.pleasure * w
        self.arousal = self.arousal * (1 - w) + other.arousal * w
        self.dominance = self.dominance * (1 - w) + other.dominance * w

    def nearest_emotion(self) -> Emotion:
        """Return the named emotion closest to the current PAD state."""
        return min(
            EMOTION_PAD, key=lambda e: _distance(self, EMOTION_PAD[e])
        )

    def tuple(self) -> tuple[float, float, float]:
        return (self.pleasure, self.arousal, self.dominance)


def _distance(state: EmotionalState, pad: tuple[float, float, float]) -> float:
    return (
        (state.pleasure - pad[0]) ** 2
        + (state.arousal - pad[1]) ** 2
        + (state.dominance - pad[2]) ** 2
    ) ** 0.5