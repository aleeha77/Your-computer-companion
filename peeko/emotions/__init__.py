"""Emotions subsystem package.

Stage 6 full emotion dynamics: not implemented yet. The data model in
:mod:`peeko.emotions.state` (PAD space + named emotions) is real and
unit-tested.
"""

from peeko.emotions.state import EmotionalState, Emotion

__all__ = ["EmotionalState", "Emotion"]