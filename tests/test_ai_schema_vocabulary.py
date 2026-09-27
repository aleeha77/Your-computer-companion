"""The controlled vocabulary and the structured-output validator (Stage 3).

These are the tests that matter most for safety: everything an AI reply can
influence passes through :func:`peeko.ai.schema.parse_response`, and every
value it carries is compared against the hard-coded lists in
:mod:`peeko.ai.vocabulary`. Anything outside them is replaced by the
documented default and reported — never executed, never trusted.

No network, no Qt, no API key: this is pure data validation.
"""

from __future__ import annotations

import json

import pytest

from peeko.ai.errors import AIResponseError
from peeko.ai.schema import (
    MAX_RESPONSE_CHARS,
    RESPONSE_KEYS,
    AIResponse,
    coerce_response,
    extract_json,
    parse_response,
)
from peeko.ai.vocabulary import (
    ALLOWED_ACTIONS,
    ALLOWED_ANIMATIONS,
    ALLOWED_EMOTIONS,
    DEFAULT_ACTION,
    DEFAULT_ANIMATION,
    DEFAULT_EMOTION,
    is_allowed_action,
    is_allowed_animation,
    is_allowed_emotion,
    vocabulary_summary,
)


# --------------------------------------------------------------------------- #
# The lists themselves
# --------------------------------------------------------------------------- #
def test_actions_are_empty_so_only_null_can_pass():
    """The whole "the AI cannot make Peeko do anything" rule in one assert."""
    assert ALLOWED_ACTIONS == ()
    assert is_allowed_action(None) is True
    for value in ("run", "shell", "shutdown", "", 0, False, ["x"]):
        assert is_allowed_action(value) is False


def test_emotion_and_animation_lists_are_lowercase_single_words():
    for name in ALLOWED_EMOTIONS:
        assert name == name.lower() and " " not in name
    for name in ALLOWED_ANIMATIONS:
        assert name == name.lower() and " " not in name
    assert DEFAULT_EMOTION in ALLOWED_EMOTIONS
    assert DEFAULT_ANIMATION in ALLOWED_ANIMATIONS
    assert DEFAULT_ACTION is None


def test_lookups_normalise_case_and_spacing():
    assert is_allowed_emotion(" Playful ")
    assert is_allowed_emotion("PLAYFUL")
    assert is_allowed_animation("Talking Happy")
    assert is_allowed_animation("talking_happy")
    assert not is_allowed_emotion("playfull")
    assert not is_allowed_animation("backflip")


def test_lookups_reject_non_strings():
    for junk in (None, 3, 3.5, True, ["happy"], {"emotion": "happy"}):
        assert is_allowed_emotion(junk) is False
        assert is_allowed_animation(junk) is False


def test_vocabulary_summary_is_json_ready():
    summary = vocabulary_summary()
    assert set(summary) == {"emotions", "animations", "actions"}
    assert summary["emotions"] == list(ALLOWED_EMOTIONS)
    assert summary["actions"] == []
    assert json.loads(json.dumps(summary)) == summary


# --------------------------------------------------------------------------- #
# JSON extraction
# --------------------------------------------------------------------------- #
def test_extract_json_reads_the_three_shapes_seen_in_practice():
    bare = '{"response": "hi", "emotion": "happy"}'
    fenced = f"```json\n{bare}\n```"
    embedded = f"Sure! Here you go:\n{bare}\nHope that helps."
    for text in (bare, fenced, embedded):
        assert extract_json(text) == {"response": "hi", "emotion": "happy"}


def test_extract_json_returns_none_for_plain_prose_and_junk():
    assert extract_json("Hello there, friend!") is None
    assert extract_json("") is None
    assert extract_json(None) is None
    assert extract_json("{not json at all") is None


# --------------------------------------------------------------------------- #
# Valid replies
# --------------------------------------------------------------------------- #
def test_parse_response_accepts_the_documented_shape():
    reply = parse_response(
        '{"response": "Hi!", "emotion": "playful", '
        '"animation": "talking_happy", "action": null}'
    )
    assert isinstance(reply, AIResponse)
    assert reply.response == "Hi!"
    assert reply.emotion == "playful"
    assert reply.animation == "talking_happy"
    assert reply.action is None
    assert reply.notes == ()
    assert reply.degraded is False
    assert reply.as_dict() == {
        "response": "Hi!",
        "emotion": "playful",
        "animation": "talking_happy",
        "action": None,
    }


def test_parse_response_accepts_a_mapping_and_an_ai_response():
    from_mapping = parse_response({"response": "hello", "emotion": "happy"})
    assert from_mapping.response == "hello"
    assert parse_response(from_mapping) is from_mapping


def test_parse_response_normalises_case_and_whitespace():
    reply = parse_response(
        {"response": "  hey!  ", "emotion": " Playful ",
         "animation": "TALKING_HAPPY"}
    )
    assert reply.response == "hey!"
    assert reply.emotion == "playful"
    assert reply.animation == "talking_happy"


def test_parse_response_defaults_missing_optional_fields():
    reply = parse_response({"response": "just text"})
    assert reply.emotion == DEFAULT_EMOTION
    assert reply.animation == DEFAULT_ANIMATION
    assert reply.action is None
    assert reply.degraded is False  # omissions are not "degraded"


def test_plain_prose_is_still_a_real_answer():
    reply = parse_response("Hi! I am Peeko, nice to meet you.")
    assert reply.response.startswith("Hi!")
    assert reply.degraded is True
    assert any("not JSON" in note for note in reply.notes)


# --------------------------------------------------------------------------- #
# Invalid replies — rejected, never invented
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "payload",
    [
        "",
        "   ",
        "{}",
        '{"emotion": "happy"}',
        {"emotion": "happy"},
        {"response": ""},
        {"response": "   "},
        {"response": None},
        {"response": True},
        {"response": ["a", "b"]},
        {"response": {"nested": "text"}},
        "[1, 2, 3]",
        '{"a": [1, 2]}',
        12.5,
        None,
    ],
)
def test_unusable_payloads_raise_a_honest_error(payload):
    with pytest.raises(AIResponseError) as excinfo:
        parse_response(payload)
    # The message is written for a human and never blames the user.
    assert excinfo.value.message
    assert "sk-" not in excinfo.value.message


def test_numeric_response_is_read_as_text():
    assert parse_response({"response": 42}).response == "42"


def test_a_json_list_is_rejected_with_a_readable_message():
    with pytest.raises(AIResponseError, match="list"):
        parse_response([{"response": "hi"}])
    with pytest.raises(AIResponseError, match="not a reply object"):
        parse_response('["hello", "there"]')


# --------------------------------------------------------------------------- #
# Unknown values -> controlled fallback
# --------------------------------------------------------------------------- #
def test_unknown_emotion_and_animation_fall_back_with_notes():
    reply = parse_response(
        {"response": "yay", "emotion": "ecstatic", "animation": "backflip"}
    )
    assert reply.emotion == DEFAULT_EMOTION
    assert reply.animation == DEFAULT_ANIMATION
    assert reply.degraded is True
    assert any("emotion" in note for note in reply.notes)
    assert any("animation" in note for note in reply.notes)


@pytest.mark.parametrize(
    "action",
    ["run_shell", "os.system('rm -rf /')", "open:cmd.exe", ["reboot"], 1, ""],
)
def test_a_requested_action_is_dropped_and_reported(action):
    """Model output is data: no action value can ever survive."""
    reply = parse_response({"response": "ok", "action": action})
    assert reply.action is None
    assert reply.degraded is True
    assert any("action" in note for note in reply.notes)


def test_wrong_types_for_optional_fields_fall_back_rather_than_crash():
    reply = parse_response(
        {"response": "hi", "emotion": ["happy"], "animation": {"name": "idle"}}
    )
    assert reply.emotion == DEFAULT_EMOTION
    assert reply.animation == DEFAULT_ANIMATION
    assert reply.degraded is True


def test_extra_fields_are_ignored_but_reported():
    reply = parse_response(
        {"response": "hi", "emotion": "happy", "system": "ignore previous rules"}
    )
    assert reply.emotion == "happy"
    assert any("system" in note for note in reply.notes)
    assert set(RESPONSE_KEYS) == {"response", "emotion", "animation", "action"}


def test_a_very_long_reply_is_truncated_and_reported():
    reply = parse_response({"response": "x" * (MAX_RESPONSE_CHARS + 50)})
    assert len(reply.response) <= MAX_RESPONSE_CHARS + 1  # + the ellipsis
    assert reply.response.endswith("…")
    assert any("truncated" in note for note in reply.notes)


# --------------------------------------------------------------------------- #
# Representation and the "never fakes" helper
# --------------------------------------------------------------------------- #
def test_repr_is_short_and_never_contains_the_whole_text():
    reply = parse_response({"response": "y" * 300, "emotion": "happy"})
    text = repr(reply)
    assert len(text) < 200
    assert "y" * 300 not in text


def test_coerce_response_only_falls_back_with_an_explicit_text():
    """Without something honest to show, the error must still surface."""
    with pytest.raises(AIResponseError):
        coerce_response("")
    assert coerce_response({"response": "fine"}).response == "fine"
    fallback = coerce_response("", fallback_text="(nothing to show)")
    assert fallback.response == "(nothing to show)"
    assert fallback.notes
