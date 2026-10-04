"""AI animation names -> the avatar's real animations (Stage 3).

The AI layer never touches the state machine: it returns an *intent* (one of
the names in :data:`peeko.ai.vocabulary.ALLOWED_ANIMATIONS`) and
:mod:`peeko.avatar.expressions` maps it onto something the current artwork
manifest can actually play. These tests pin down the two promises that
matter: the mapping covers every name the AI may send, and anything the
manifest cannot play falls back to ``idle`` instead of breaking.
"""

from __future__ import annotations

import random

from peeko.ai.vocabulary import ALLOWED_ANIMATIONS
from peeko.avatar.expressions import (
    ANIMATION_STATE_MAP,
    FALLBACK_STATE,
    apply_expression,
    apply_listening,
    apply_speaking,
    expression_state,
    known_animations,
    listening_available,
    talking_available,
)
from peeko.avatar.manifest import load_manifest
from peeko.avatar.state_machine import (
    CLICK,
    CONFUSED,
    DOUBLE_CLICK,
    DRAGGING,
    HOVER,
    IDLE,
    LISTENING,
    TALKING,
    AvatarStateMachine,
)
from peeko.avatar.widget import DEFAULT_ASSETS_DIR
from tests.conftest import write_avatar_assets

PACKAGED_MANIFEST = DEFAULT_ASSETS_DIR / "manifest.json"


def machine_for(manifest_path, **kwargs) -> AvatarStateMachine:
    return AvatarStateMachine(load_manifest(manifest_path),
                              rng=random.Random(7), **kwargs)


def packaged_machine() -> AvatarStateMachine:
    return machine_for(PACKAGED_MANIFEST)


# --------------------------------------------------------------------------- #
# The table and the vocabulary can never drift apart
# --------------------------------------------------------------------------- #
def test_every_allowed_animation_has_a_mapping():
    assert set(ANIMATION_STATE_MAP) == set(ALLOWED_ANIMATIONS)
    assert known_animations() == tuple(ANIMATION_STATE_MAP)


def test_every_target_is_a_real_avatar_state():
    from peeko.avatar import state_machine

    for animation, state in ANIMATION_STATE_MAP.items():
        assert state == getattr(state_machine, state.upper()), animation


def test_the_talking_intents_map_onto_existing_animations():
    """The placeholder artwork has no talking frames — the mapping says so."""
    assert ANIMATION_STATE_MAP["talking"] == HOVER
    assert ANIMATION_STATE_MAP["talking_happy"] == CLICK
    assert ANIMATION_STATE_MAP["happy_bounce"] == CLICK
    assert ANIMATION_STATE_MAP["excited_bounce"] == DOUBLE_CLICK
    assert ANIMATION_STATE_MAP["talking_confused"] == CONFUSED


# --------------------------------------------------------------------------- #
# Resolution and fallback
# --------------------------------------------------------------------------- #
def test_every_allowed_animation_resolves_to_something_playable():
    machine = packaged_machine()
    for animation in ALLOWED_ANIMATIONS:
        state = expression_state(animation, machine)
        assert machine.can_play(state), animation
        assert state == ANIMATION_STATE_MAP[animation]


def test_an_unknown_animation_falls_back_to_idle():
    machine = packaged_machine()
    for junk in ("backflip", "os.system", "", None, "talking_happily"):
        assert expression_state(junk, machine) == FALLBACK_STATE
    assert FALLBACK_STATE == IDLE


def test_names_are_matched_case_insensitively():
    machine = packaged_machine()
    assert expression_state(" Talking_Happy ", machine) == CLICK


def test_an_animation_this_artwork_cannot_play_falls_back(tmp_path, manifest_data):
    """Reaction animations are optional: a manifest without them still works."""
    del manifest_data["animations"]["confused"]
    del manifest_data["animations"]["hover"]
    path = write_avatar_assets(tmp_path / "art", manifest_data)
    machine = machine_for(path)

    assert machine.can_play(CONFUSED) is False
    assert expression_state("talking_confused", machine) == IDLE
    assert expression_state("talking", machine) == IDLE
    # …while the states the manifest does have keep working.
    assert expression_state("talking_happy", machine) == CLICK


# --------------------------------------------------------------------------- #
# Playing a cue
# --------------------------------------------------------------------------- #
def test_applying_an_expression_plays_it_and_reports_the_state():
    machine = packaged_machine()
    assert apply_expression(machine, "talking_happy") == CLICK
    assert machine.state == CLICK


def test_an_unplayable_expression_plays_idle_rather_than_failing():
    machine = packaged_machine()
    assert apply_expression(machine, "nonsense") == IDLE
    assert machine.state == IDLE


def test_user_input_wins_while_the_robot_is_being_dragged():
    """A reply must never interrupt the user carrying the robot."""
    machine = packaged_machine()
    machine.press()
    machine.drag_started()
    assert machine.state == "dragging"
    assert apply_expression(machine, "talking_happy") is None
    assert machine.state == "dragging"


def test_user_input_wins_while_the_mouse_button_is_held():
    machine = packaged_machine()
    machine.press()
    assert apply_expression(machine, "happy_bounce") is None


# --------------------------------------------------------------------------- #
# Stage 4: the sustained listening state (voice input)
# --------------------------------------------------------------------------- #
def test_apply_listening_shows_and_ends_the_pose():
    machine = machine_for(PACKAGED_MANIFEST)

    assert apply_listening(machine, True) == LISTENING
    assert machine.state == LISTENING
    assert apply_listening(machine, False) == IDLE
    assert machine.state == IDLE
    assert apply_listening(machine, False) is None   # nothing left to end


def test_the_packaged_artwork_ships_the_listening_animation():
    machine = machine_for(PACKAGED_MANIFEST)
    assert listening_available(machine) is True
    assert machine.can_play(LISTENING) is True


def test_a_manifest_without_the_animation_declines_and_says_nothing(
    tmp_path, manifest_data
):
    """Older artwork keeps working: no pose, and the window still says so."""
    manifest_data["animations"].pop("listening", None)
    machine = machine_for(write_avatar_assets(tmp_path / "art", manifest_data))

    assert listening_available(machine) is False
    assert apply_listening(machine, True) is None
    assert apply_listening(machine, False) is None
    assert machine.state == IDLE


def test_the_listening_pose_yields_to_a_drag():
    """Expressions never fight the user: dragging always wins."""
    machine = machine_for(PACKAGED_MANIFEST)
    assert apply_listening(machine, True) == LISTENING

    machine.press()
    machine.drag_started()
    assert apply_listening(machine, True) is None     # declined while dragging
    assert machine.state == DRAGGING
    assert apply_listening(machine, False) is None    # no pose to end


# --------------------------------------------------------------------------- #
# Stage 5: the sustained talking state (voice output)
# --------------------------------------------------------------------------- #
def test_apply_speaking_shows_and_ends_the_pose():
    machine = machine_for(PACKAGED_MANIFEST)

    assert apply_speaking(machine, True) == TALKING
    assert machine.state == TALKING
    assert machine.talking is True
    assert apply_speaking(machine, False) == IDLE
    assert machine.state == IDLE
    assert apply_speaking(machine, False) is None     # nothing left to end


def test_the_packaged_artwork_ships_the_talking_animation():
    machine = machine_for(PACKAGED_MANIFEST)
    assert talking_available(machine) is True
    assert machine.can_play(TALKING) is True
    assert machine.talking_available is True


def test_a_manifest_without_the_talking_animation_declines_and_says_nothing(
    tmp_path, manifest_data
):
    """Older artwork keeps working: no pose, and the window still says so."""
    manifest_data["animations"].pop("talking", None)
    machine = machine_for(write_avatar_assets(tmp_path / "art", manifest_data))

    assert talking_available(machine) is False
    assert apply_speaking(machine, True) is None
    assert apply_speaking(machine, False) is None
    assert machine.state == IDLE


def test_the_talking_pose_yields_to_a_drag():
    """Expressions never fight the user: dragging always wins."""
    machine = machine_for(PACKAGED_MANIFEST)
    assert apply_speaking(machine, True) == TALKING

    machine.press()
    machine.drag_started()
    assert apply_speaking(machine, True) is None      # declined while dragging
    assert machine.state == DRAGGING
    assert apply_speaking(machine, False) is None     # no pose to end


def test_talking_outranks_the_listening_pose():
    """Speaking while the microphone is still open shows the talking pose."""
    machine = machine_for(PACKAGED_MANIFEST)
    assert apply_listening(machine, True) == LISTENING

    assert apply_speaking(machine, True) == TALKING
    assert machine.state == TALKING
    # …and the listening pose comes back by itself when the playback ends.
    assert apply_speaking(machine, False) == LISTENING
    assert machine.state == LISTENING
    assert apply_listening(machine, False) == IDLE
