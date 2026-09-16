"""Tests for the emotions and needs data models (real Stage 0 code)."""

from __future__ import annotations

import pytest

from peeko.emotions import EmotionalState, Emotion
from peeko.needs import Need, PetNeeds


# ---------------------------------------------------------------------------
# Emotions
# ---------------------------------------------------------------------------
def test_emotion_pad_roundtrip():
    state = EmotionalState.from_emotion(Emotion.HAPPY)
    assert state.pleasure > 0
    assert state.nearest_emotion() == Emotion.HAPPY


def test_state_clamps_to_unit_cube():
    state = EmotionalState(pleasure=5.0, arousal=-9.0)
    assert state.pleasure == 1.0
    assert state.arousal == -1.0


def test_blend_moves_toward_other():
    neutral = EmotionalState()
    happy = EmotionalState.from_emotion(Emotion.HAPPY)
    neutral.blend(happy, weight=1.0)
    assert neutral.tuple() == pytest.approx(happy.tuple())


def test_blend_rejects_bad_weight():
    with pytest.raises(ValueError, match="weight"):
        EmotionalState().blend(EmotionalState(), weight=1.5)


# ---------------------------------------------------------------------------
# Needs
# ---------------------------------------------------------------------------
def test_needs_defaults_within_range():
    needs = PetNeeds()
    for need in Need:
        assert 0.0 <= needs.get(need) <= 100.0


def test_tick_decays_every_need():
    needs = PetNeeds()
    before = dict(needs.values)
    needs.tick(hours=2.0)
    for need in Need:
        assert needs.get(need) < before[need]


def test_tick_clamps_at_zero():
    needs = PetNeeds()
    needs.tick(hours=10_000.0)
    for need in Need:
        assert needs.get(need) == 0.0


def test_set_clamps_high():
    needs = PetNeeds()
    needs.set(Need.HUNGER, 150.0)
    assert needs.get(Need.HUNGER) == 100.0


def test_lowest_returns_most_urgent():
    needs = PetNeeds()
    needs.set(Need.SLEEP, 1.0)
    assert needs.lowest() == Need.SLEEP


def test_tick_rejects_negative_time():
    with pytest.raises(ValueError, match="hours"):
        PetNeeds().tick(hours=-1)


def test_summary_is_serialisable():
    summary = PetNeeds().summary()
    assert set(summary) == {need.value for need in Need}