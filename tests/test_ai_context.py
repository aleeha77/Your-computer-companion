"""The structured context Peeko sends to the AI (Stage 3).

Stage 3 must hand the model a *truthful* picture: real values where a real
subsystem exists (the interaction log today), and clearly documented
placeholders where it does not (emotions, needs, memory and app awareness
arrive in Stages 6/7/8). Nothing here may invent a value, and nothing here
touches the network, Qt or the operating system.
"""

from __future__ import annotations

import json

from peeko.ai.context import (
    DEFAULT_INTERACTION_HISTORY,
    DEFAULT_MEMORY_LIMIT,
    ChatContext,
    InteractionLog,
    build_context,
)
from peeko.emotions.state import Emotion, EmotionalState
from peeko.needs.system import Need, PetNeeds


# --------------------------------------------------------------------------- #
# The shape of the block
# --------------------------------------------------------------------------- #
EXPECTED_KEYS = {
    "emotion",
    "happiness",
    "energy",
    "hunger",
    "sleepiness",
    "friendship",
    "current_app",
    "recent_interactions",
    "memory",
    "placeholders",
}


def test_context_has_every_field_the_spec_asks_for():
    context = build_context()
    assert set(context.as_dict()) == EXPECTED_KEYS
    assert json.loads(context.to_json()) == context.as_dict()


def test_empty_context_is_documented_placeholders_not_invented_values():
    context = build_context()
    # Documented defaults, straight from the needs system.
    assert context.energy == PetNeeds().get(Need.ENERGY)
    assert context.hunger == PetNeeds().get(Need.HUNGER)
    assert context.friendship == PetNeeds().get(Need.FRIENDSHIP)
    assert context.sleepiness == round(100.0 - PetNeeds().get(Need.SLEEP), 1)
    assert context.emotion == "neutral"
    assert context.happiness == 50.0
    assert context.current_app is None
    assert context.recent_interactions == ()
    assert context.memory == ()
    # …and it says so, so the model is never misled.
    assert set(context.placeholders) == {
        "emotion", "energy", "hunger", "sleepiness", "friendship",
        "current_app", "memory", "recent_interactions",
    }


def test_context_is_json_serialisable_with_no_exotic_types():
    context = build_context(
        interactions=["user clicked the robot"],
        memory_provider=lambda: ["likes penguins"],
        active_app_provider=lambda: "code — editor",
    )
    payload = json.loads(context.to_json())
    assert isinstance(payload["hunger"], float)
    assert isinstance(payload["recent_interactions"], list)
    assert payload["current_app"] == "code — editor"


def test_context_is_frozen_and_has_sane_defaults():
    context = ChatContext()
    assert context.placeholders == ()
    try:
        context.emotion = "angry"  # type: ignore[misc]
    except Exception as exc:  # noqa: BLE001
        assert isinstance(exc, AttributeError)
    else:
        raise AssertionError("ChatContext must be immutable")


# --------------------------------------------------------------------------- #
# The interaction log (real data today)
# --------------------------------------------------------------------------- #
def test_interaction_log_keeps_the_tail_in_order():
    log = InteractionLog(max_entries=3)
    for i in range(5):
        log.record(f"event {i}")
    assert log.entries() == ("event 2", "event 3", "event 4")
    assert len(log) == 3
    log.clear()
    assert log.entries() == ()
    assert log.max_entries == 3


def test_interaction_log_ignores_blank_lines_and_validates_size():
    log = InteractionLog(max_entries=2)
    log.record("   ")
    log.record("")
    log.record(None)  # type: ignore[arg-type]
    assert len(log) == 0
    log.record(" real ")
    assert log.entries() == ("real",)
    try:
        InteractionLog(max_entries=0)
    except ValueError:
        pass
    else:
        raise AssertionError("max_entries must be >= 1")


def test_interactions_are_sent_verbatim_and_bounded():
    log = InteractionLog(max_entries=DEFAULT_INTERACTION_HISTORY)
    for i in range(DEFAULT_INTERACTION_HISTORY + 5):
        log.record(f"event {i}")
    context = build_context(interactions=log)
    assert len(context.recent_interactions) == DEFAULT_INTERACTION_HISTORY
    assert context.recent_interactions[-1] == (
        f"event {DEFAULT_INTERACTION_HISTORY + 4}"
    )
    assert "recent_interactions" not in context.placeholders
    # A plain iterable works too.
    assert build_context(interactions=["a", "", "b"]).recent_interactions == ("a", "b")


# --------------------------------------------------------------------------- #
# The seam later stages plug into
# --------------------------------------------------------------------------- #
def test_real_emotion_and_needs_are_copied_with_documented_scales():
    needs = PetNeeds()
    needs.set(Need.ENERGY, 12.5)
    needs.set(Need.HUNGER, 33.0)
    needs.set(Need.SLEEP, 90.0)
    needs.set(Need.FRIENDSHIP, 77.0)
    state = EmotionalState.from_emotion(Emotion.EXCITED)

    context = build_context(emotional_state=state, needs=needs)
    assert context.emotion == Emotion.EXCITED.value
    assert context.happiness == 80.0  # PAD pleasure 0.6 -> 0..100
    assert context.energy == 12.5
    assert context.hunger == 33.0
    assert context.friendship == 77.0
    assert context.sleepiness == 10.0
    for name in ("emotion", "energy", "hunger", "sleepiness", "friendship"):
        assert name not in context.placeholders


def test_a_sad_emotion_maps_to_low_happiness():
    sad = EmotionalState.from_emotion(Emotion.SAD)  # pleasure -0.6
    assert build_context(emotional_state=sad).happiness == 20.0


def test_unknown_active_app_is_reported_as_unknown_not_guessed():
    assert build_context(active_app_provider=lambda: None).current_app is None
    assert "current_app" in build_context(
        active_app_provider=lambda: None
    ).placeholders
    assert "current_app" not in build_context(
        active_app_provider=lambda: "Firefox"
    ).placeholders


def test_broken_providers_never_crash_the_context():
    def boom(*_args, **_kwargs):
        raise RuntimeError("the platform ate my window list")

    context = build_context(
        active_app_provider=boom,
        memory_provider=boom,
        emotional_state=object(),  # not an EmotionalState
        needs="not needs",  # not a PetNeeds
    )
    assert context.current_app is None
    assert context.memory == ()
    assert context.emotion == "neutral"
    assert context.energy == PetNeeds().get(Need.ENERGY)
    assert "memory" in context.placeholders


def test_memory_is_bounded_and_blank_entries_dropped():
    memories = [f"fact {i}" for i in range(DEFAULT_MEMORY_LIMIT + 4)] + ["", "  "]
    context = build_context(memory_provider=lambda: memories)
    assert len(context.memory) == DEFAULT_MEMORY_LIMIT
    assert context.memory[0] == "fact 0"
    assert "memory" not in context.placeholders
    # Nothing remembered yet -> honest placeholder, never an empty-string lie.
    assert "memory" in build_context(memory_provider=lambda: []).placeholders


def test_extra_field_is_merged_into_the_block():
    context = build_context(extra={"mood_note": "the owner is debugging"})
    assert context.as_dict()["mood_note"] == "the owner is debugging"


def test_extra_may_not_override_a_documented_field():
    context = build_context(
        extra={"hunger": 999, "emotion": "furious", "nickname": "Ada"}
    )
    block = context.as_dict()
    assert block["hunger"] == PetNeeds().get(Need.HUNGER)
    assert block["emotion"] == "neutral"
    assert block["nickname"] == "Ada"
