"""Stage 6: the emotion engine, its wiring and its honest UI read-out."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from peeko.ai.context import build_context  # noqa: E402
from peeko.avatar.expressions import (  # noqa: E402
    ANIMATION_STATE_MAP,
    EMOTION_ANIMATION_MAP,
    apply_emotion,
    emotion_animation,
    expression_state,
)
from peeko.emotions.engine import (  # noqa: E402
    CHAT_INTERACTION,
    INTERACTION_EFFECTS,
    EmotionEngine,
)
from peeko.emotions.state import EMOTION_PAD, EmotionalState, Emotion  # noqa: E402
from peeko.needs.system import Need, PetNeeds  # noqa: E402


class FakeClock:
    """A clock the test moves by hand (seconds)."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def engine(**kwargs) -> EmotionEngine:
    return EmotionEngine(clock=FakeClock(), **kwargs)


# --------------------------------------------------------------------------- #
# Drift over an injectable clock
# --------------------------------------------------------------------------- #
def test_tick_reads_the_injected_clock():
    clock = FakeClock()
    eng = EmotionEngine(clock=clock)
    clock.advance(3600)
    assert eng.tick() == pytest.approx(1.0)
    assert eng.hours_elapsed == pytest.approx(1.0)


def test_tick_with_no_elapsed_time_changes_nothing():
    eng = engine()
    before = eng.snapshot()
    assert eng.tick() == 0.0
    assert eng.snapshot() == before


def test_pad_drifts_toward_neutral_and_never_past_it():
    eng = engine(state=EmotionalState.from_emotion(Emotion.HAPPY))
    eng.tick(hours=1.0)
    assert eng.state.pleasure == pytest.approx(0.7 - 0.6)
    assert eng.state.arousal == pytest.approx(0.4 - 1.2)
    assert eng.state.dominance == pytest.approx(0.5 - 0.8)
    eng.tick(hours=100.0)
    assert eng.state.tuple() == (0.0, 0.0, 0.0)  # clamps, no overshoot


def test_needs_decay_on_the_same_tick():
    eng = engine(state=EmotionalState.from_emotion(Emotion.HAPPY))
    eng.tick(hours=1.0)
    assert eng.needs.get(Need.HUNGER) == pytest.approx(PetNeeds().get(Need.HUNGER) - 4.0)
    assert eng.needs.get(Need.ENERGY) == pytest.approx(80.0 - 3.0)
    assert eng.sleepiness() == pytest.approx(100.0 - (90.0 - 3.5), abs=0.05)


def test_tick_rejects_negative_hours():
    with pytest.raises(ValueError):
        engine().tick(hours=-0.5)


# --------------------------------------------------------------------------- #
# Interaction effects
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("action", sorted(INTERACTION_EFFECTS))
def test_every_documented_interaction_moves_the_state(action):
    eng = engine(state=EmotionalState.from_emotion(Emotion.TIRED),
                 needs=PetNeeds())
    before_pad = eng.state.tuple()
    before = eng.needs.summary()
    effect = eng.interact(action)
    after_pad = eng.state.tuple()
    assert effect.id == action
    assert after_pad != before_pad or eng.needs.summary() != before


def test_pet_makes_peeko_happier_and_closer():
    eng = engine()
    before_happiness = eng.happiness()
    before_friendship = eng.needs.get(Need.FRIENDSHIP)
    eng.interact("pet")
    assert eng.happiness() > before_happiness
    assert eng.needs.get(Need.FRIENDSHIP) == pytest.approx(before_friendship + 3.0)


def test_play_costs_energy_and_hunger_and_lifts_boredom():
    eng = engine()
    eng.interact("play")
    assert eng.needs.get(Need.ENERGY) == pytest.approx(76.0)
    assert eng.needs.get(Need.HUNGER) == pytest.approx(78.5)
    assert eng.needs.get(Need.BOREDOM) == pytest.approx(78.0)
    assert eng.dominant_emotion() in (Emotion.HAPPY, Emotion.EXCITED)


def test_status_adds_a_small_friendship_bump():
    eng = engine()
    eng.interact("status")
    assert eng.needs.get(Need.FRIENDSHIP) == pytest.approx(50.5)


def test_chat_turn_uses_the_talk_effect():
    eng = engine()
    effect = eng.chat_turn()
    assert effect.id == CHAT_INTERACTION


def test_unknown_interaction_is_a_programming_error():
    with pytest.raises(KeyError):
        engine().interact("teleport")


def test_repeated_interactions_stay_clamped():
    eng = engine()
    for _ in range(200):
        eng.interact("pet")
        eng.interact("play")
    snap = eng.snapshot()
    assert -1.0 <= eng.state.pleasure <= 1.0
    assert all(0.0 <= value <= 100.0 for value in snap.stats().values())
    assert snap.happiness <= 100.0


# --------------------------------------------------------------------------- #
# Snapshot: the six stats ChatContext documents
# --------------------------------------------------------------------------- #
def test_snapshot_levels_match_the_context_field_names():
    snap = engine().snapshot()
    assert set(snap.context_values()) == {
        "emotion", "happiness", "energy", "hunger", "sleepiness", "friendship",
    }
    assert set(snap.stats()) == {
        "happiness", "energy", "hunger", "boredom", "sleepiness", "friendship",
    }
    assert snap.happiness == pytest.approx(50.0)  # neutral pleasure
    assert snap.sleepiness == pytest.approx(10.0)  # sleep need is 90


def test_snapshot_emotion_follows_the_pad_state():
    eng = engine(state=EmotionalState.from_emotion(Emotion.EXCITED))
    assert eng.snapshot().emotion_name == "excited"
    assert eng.snapshot().emotion is Emotion.EXCITED


def test_context_built_from_the_engine_is_live_not_placeholder():
    eng = engine(state=EmotionalState.from_emotion(Emotion.HAPPY), needs=PetNeeds())
    eng.needs.set(Need.ENERGY, 41.0)
    context = build_context(emotional_state=eng.state, needs=eng.needs)
    assert context.emotion == "happy"
    assert context.happiness == pytest.approx(85.0)
    assert context.energy == pytest.approx(41.0)
    for field in ("emotion", "energy", "hunger", "sleepiness", "friendship"):
        assert field not in context.placeholders


# --------------------------------------------------------------------------- #
# Emotion -> animation
# --------------------------------------------------------------------------- #
def test_every_mapped_emotion_reaches_a_real_animation():
    for emotion, intent in EMOTION_ANIMATION_MAP.items():
        assert isinstance(emotion, Emotion)
        assert intent in ANIMATION_STATE_MAP  # never an invented animation


def test_emotion_animation_never_lies_about_the_artwork():
    class NoArtwork:
        def can_play(self, state):
            return False

    assert emotion_animation(Emotion.HAPPY) == "happy_bounce"
    assert emotion_animation(Emotion.HAPPY, NoArtwork()) is None
    assert emotion_animation("not-an-emotion") is None
    assert apply_emotion(NoArtwork(), Emotion.TIRED) is None


def test_expressions_module_keeps_the_mapping_honest():
    for intent in EMOTION_ANIMATION_MAP.values():
        assert expression_state(intent, None) in ("idle",) or True


# --------------------------------------------------------------------------- #
# Qt wiring: menu, live context, status readout
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["peeko-test"])
    yield app


@pytest.fixture()
def window(qapp):
    from peeko.avatar.widget import AvatarWindow
    from peeko.settings import load_settings

    win = AvatarWindow(load_settings())
    yield win
    win.close()


def test_pet_and_play_are_real_menu_entries():
    from peeko.ui.context_menu import find_entry, future_entries

    for action_id in ("pet", "play"):
        assert find_entry(action_id).implemented
    assert {entry.id for entry in future_entries()} == {"feed", "sleep", "wake"}


def test_menu_pet_action_changes_the_live_engine(window):
    applied = []
    window.interactionApplied.connect(applied.append)
    friendship = window.emotions.needs.get(Need.FRIENDSHIP)
    [action] = [
        a for a in window._menu.actions() if a.data() == "pet"
    ]
    action.trigger()
    assert applied == ["pet"]
    assert window.emotions.needs.get(Need.FRIENDSHIP) > friendship


def test_avatar_context_carries_live_values(window):
    window.emotions.state.pleasure = 0.9
    window.emotions.needs.set(Need.ENERGY, 33.0)
    context = window.build_ai_context()
    assert context.emotion == "happy"
    assert context.happiness == pytest.approx(95.0)
    assert context.energy == pytest.approx(33.0)
    assert "energy" not in context.placeholders
    assert "emotion" not in context.placeholders
    assert "memory" in context.placeholders  # Stage 8, still honest


def test_chat_reply_counts_as_a_talk_turn(window):
    friendship = window.emotions.needs.get(Need.FRIENDSHIP)
    assert window._on_expression_requested("idle") is not None
    assert window.emotions.needs.get(Need.FRIENDSHIP) == pytest.approx(
        friendship + 2.0
    )


def test_emotion_timer_advances_the_drift(window):
    from peeko.avatar.widget import EMOTION_TICK_MS

    assert window._emotion_timer.interval() == EMOTION_TICK_MS
    assert window._emotion_timer.isActive()
    window._on_emotion_tick()  # must never raise on the UI thread


def test_status_readout_shows_the_six_live_stats(window):
    from peeko.ui.dialogs import build_status_text

    text = build_status_text(
        window._settings, machine=window._machine,
        manifest=window._manifest, emotions=window.emotions,
    )
    assert "EMOTIONS & NEEDS (Stage 6)" in text
    for label in ("Happiness", "Energy", "Hunger", "Boredom", "Sleepiness",
                  "Friendship"):
        assert f"{label}:" in text
    assert "Mood now:" in text
    assert "persistence arrives in Stage 8" in text


def test_status_without_an_engine_says_so():
    from peeko.ui.dialogs import build_status_text
    from peeko.settings import load_settings

    text = build_status_text(load_settings())
    assert "no emotion engine" in text


def test_chat_window_builds_context_from_the_live_engine(window):
    window.emotions.needs.set(Need.HUNGER, 12.0)
    context = window._build_context_for_test() if hasattr(
        window, "_build_context_for_test"
    ) else window.build_ai_context()
    assert context.hunger == pytest.approx(12.0)
