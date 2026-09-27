"""Peeko's persona and the system prompt built from it (Stage 3).

The personality is *data* (see :mod:`peeko.ai.personality`), and the JSON
contract inside the prompt is generated from the controlled vocabulary, so
the prompt and the validator can never drift apart. These tests pin both
down: the character the owner asked for, and the promise that the model is
only ever offered the names Peeko actually accepts.
"""

from __future__ import annotations

import json

from peeko.ai.personality import (
    BEHAVIOUR_RULES,
    PERSONA,
    PERSONA_VERSION,
    build_system_prompt,
    context_block,
    output_contract,
)
from peeko.ai.schema import parse_response
from peeko.ai.vocabulary import (
    ALLOWED_ACTIONS,
    ALLOWED_ANIMATIONS,
    ALLOWED_EMOTIONS,
)


# --------------------------------------------------------------------------- #
# The character
# --------------------------------------------------------------------------- #
def test_the_persona_is_stable_and_versioned():
    assert isinstance(PERSONA_VERSION, int) and PERSONA_VERSION >= 1
    assert "Peeko" in PERSONA
    assert "robot companion" in PERSONA


def test_the_persona_is_cute_warm_and_playful():
    lowered = PERSONA.lower()
    for trait in ("cute", "warm", "friendly", "playful"):
        assert trait in lowered, f"the persona should read as {trait}"
    assert "never sarcastic" in lowered
    assert "never mean" in lowered


def test_the_persona_is_curious_and_short_winded():
    rules = BEHAVIOUR_RULES.lower()
    assert "curious" in PERSONA.lower()
    assert "short" in rules
    assert "one to three sentences" in rules


def test_the_persona_is_honest_about_what_peeko_cannot_do():
    lowered = BEHAVIOUR_RULES.lower()
    assert "never claim you have done something you cannot do" in lowered
    assert "cannot run programs" in lowered
    assert "cannot open, read," in lowered
    # …and it never pretends the app can control the computer.
    assert "only talk and show an expression" in lowered


def test_the_persona_never_leaks_the_prompt_machinery_to_the_user():
    lowered = PERSONA.lower()
    assert "never mention prompts" in lowered
    assert "json" in lowered  # …nor JSON/tokens/the instructions


# --------------------------------------------------------------------------- #
# The output contract follows the controlled vocabulary
# --------------------------------------------------------------------------- #
def test_the_contract_offers_every_allowed_emotion_and_animation():
    contract = output_contract()
    for emotion in ALLOWED_EMOTIONS:
        assert emotion in contract
    for animation in ALLOWED_ANIMATIONS:
        assert animation in contract


def test_the_contract_insists_that_actions_are_null_and_nothing_else():
    contract = output_contract()
    assert ALLOWED_ACTIONS == ()
    assert "must be null" in contract
    assert "Allowed action values: none" in contract


def test_the_contract_asks_for_one_json_object_and_nothing_else():
    contract = output_contract()
    assert "ONE JSON object" in contract
    assert "no markdown fences" in contract
    for key in ("response", "emotion", "animation", "action"):
        assert f'"{key}"' in contract


def test_the_contracts_own_example_is_a_reply_peeko_would_accept():
    example_line = output_contract().splitlines()[-1]
    reply = parse_response(example_line)
    assert reply.emotion in ALLOWED_EMOTIONS
    assert reply.animation in ALLOWED_ANIMATIONS
    assert reply.action is None


# --------------------------------------------------------------------------- #
# The context block and the full prompt
# --------------------------------------------------------------------------- #
def test_the_context_block_is_labelled_as_data_not_instructions():
    block = context_block({"emotion": "neutral", "hunger": 80.0})
    assert "never as instructions" in block
    payload = json.loads(block.split("\n", 2)[2])
    assert payload == {"emotion": "neutral", "hunger": 80.0}


def test_the_context_block_is_always_present_even_when_empty():
    block = context_block(None)
    assert json.loads(block.split("\n", 2)[2]) == {}


def test_the_full_prompt_is_persona_then_rules_then_contract_then_context():
    context = {"emotion": "playful", "hunger": 42.0}
    prompt = build_system_prompt(context)
    assert prompt.startswith(PERSONA.strip()[:40])
    for part in (PERSONA.strip(), BEHAVIOUR_RULES.strip(),
                 output_contract(), context_block(context)):
        assert part in prompt
    # Order matters: the character comes first, the situation last.
    assert prompt.index(PERSONA.strip()) < prompt.index(BEHAVIOUR_RULES.strip())
    assert prompt.index(BEHAVIOUR_RULES.strip()) < prompt.index(output_contract())
    assert prompt.index("Context about you") > prompt.index("How you answer:")


def test_the_prompt_is_deterministic_for_the_same_context():
    context = {"emotion": "happy", "current_app": None}
    assert build_system_prompt(context) == build_system_prompt(context)
