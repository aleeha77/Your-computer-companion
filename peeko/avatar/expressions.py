"""Expressions: turning an AI reply into the avatar's *existing* animations.

Stage 3 plugs the conversation system into the robot without teaching the
avatar anything about AI. The AI layer returns an *intent* (one of the
names in :data:`peeko.ai.vocabulary.ALLOWED_ANIMATIONS`); this module — the
only place that knows about both worlds — maps that intent onto a state the
current artwork manifest can actually play.

The mapping is honestly approximate: the placeholder artwork has no
dedicated *reaction* for the AI's ``talking`` intent, so that one uses the
manifest's attentive ``hover`` frames and ``talking_happy`` uses the happy
``click`` bounce. When the owner replaces the artwork (or a later stage adds
better frames), only this table changes. (The AI intent named ``talking`` is
a one-shot reaction and is *not* the same thing as the sustained ``talking``
state Stage 5 plays while Peeko's voice is actually being heard — see
:func:`apply_speaking`.)

Rules:

* an unknown animation name → ``idle`` (never an error, never a crash);
* an intent whose state this manifest cannot play (reaction animations are
  optional — see :mod:`peeko.avatar.state_machine`) → ``idle``;
* never interrupts dragging or an active click (the state machine decides,
  see :meth:`peeko.avatar.state_machine.AvatarStateMachine.play_cued`).

Stage 4 (voice input) uses the same seam from the other direction: the chat
window says "a capture started/stopped", and :func:`apply_listening` shows or
ends the sustained ``listening`` state — the only state that does not settle
back to idle on its own.

Stage 5 (voice output) is the mirror image again: the chat window says "Peeko
started/stopped speaking", and :func:`apply_speaking` shows or ends the
sustained ``talking`` state. Both sustained states are optional artwork and
both yield to the user: dragging the robot always wins.
"""

from __future__ import annotations

from peeko.avatar.state_machine import (
    BLINK,
    CLICK,
    CONFUSED,
    DOUBLE_CLICK,
    HOVER,
    IDLE,
    LISTENING,
    LOOK_DOWN,
    LOOK_LEFT,
    LOOK_RIGHT,
    LOOK_UP,
    TALKING,
)
from peeko.emotions.state import Emotion

#: AI animation name -> avatar state. Keys must stay a subset of
#: :data:`peeko.ai.vocabulary.ALLOWED_ANIMATIONS` (a test enforces this).
ANIMATION_STATE_MAP: dict[str, str] = {
    # literal states
    "idle": IDLE,
    "blink": BLINK,
    "look_left": LOOK_LEFT,
    "look_right": LOOK_RIGHT,
    "look_up": LOOK_UP,
    "look_down": LOOK_DOWN,
    # "talking" intents -> the closest animation the placeholder art has
    "talking": HOVER,             # attentive, half-lidded: mid-sentence
    "talking_happy": CLICK,       # the happy squash-and-bounce
    "talking_curious": LOOK_UP,   # a small upward glance: "hmm?"
    "talking_confused": CONFUSED,  # the "huh?" head-shake
    # explicit mood bounces
    "happy_bounce": CLICK,
    "excited_bounce": DOUBLE_CLICK,
}

#: What Peeko plays when the AI asks for something he cannot do.
FALLBACK_STATE = IDLE

# --------------------------------------------------------------------------- #
# Stage 6: the emotion engine's dominant mood -> an existing animation
# --------------------------------------------------------------------------- #
#: Dominant :class:`peeko.emotions.state.Emotion` -> the AI animation *intent*
#: the avatar already understands. Every value here must be a key of
#: :data:`ANIMATION_STATE_MAP` (a test enforces that), so an emotion can only
#: ever reach artwork that really exists — never an invented animation.
#:
#: The mapping is honestly approximate while the placeholder artwork has no
#: dedicated mood frames: there is no sleepy or angry animation yet, so
#: ``tired`` borrows the blink and ``angry`` borrows the "huh?" head-shake (the
#: only displeased-looking reaction in the manifest). When the owner replaces
#: the artwork — or a later stage adds real mood frames — only this table
#: changes. :func:`emotion_animation` returns ``None`` for a mood the current
#: artwork cannot play, and the caller keeps whatever animation is running
#: rather than showing something unrelated.
EMOTION_ANIMATION_MAP: dict[Emotion, str] = {
    Emotion.NEUTRAL: "idle",
    Emotion.HAPPY: "happy_bounce",
    Emotion.EXCITED: "excited_bounce",
    Emotion.SURPRISED: "look_up",
    Emotion.SAD: "look_down",
    Emotion.TIRED: "blink",
    Emotion.ANGRY: "talking_confused",
}


def emotion_animation(emotion, machine=None) -> str | None:
    """The animation intent for a dominant emotion, or ``None``.

    ``None`` means "leave the current animation alone": the emotion is
    unknown, the mapping has no entry for it, or the current artwork cannot
    play the mapped state. Callers use :func:`apply_emotion` to actually cue
    it.
    """
    try:
        intent = EMOTION_ANIMATION_MAP.get(emotion)
    except TypeError:  # unhashable / not an Emotion at all
        return None
    if intent is None:
        return None
    if machine is not None and not machine.can_play(
        ANIMATION_STATE_MAP[intent]
    ):
        return None
    return intent


def apply_emotion(machine, emotion) -> str | None:
    """Cue the animation for a dominant emotion; return the state played.

    ``None`` means nothing changed: no artwork-supported animation for that
    mood, or the machine declined because the user is dragging the robot —
    the same precedence every other cue follows.
    """
    intent = emotion_animation(emotion, machine)
    if intent is None:
        return None
    return apply_expression(machine, intent)

#: What Peeko plays while a voice capture is running (Stage 4). Optional
#: artwork: without a ``listening`` animation nothing is played, and the chat
#: window's own "listening…" indicator is what tells the user.
LISTENING_STATE = LISTENING

#: What Peeko plays while its reply is being spoken out loud (Stage 5).
#: Optional artwork in exactly the same way: without a ``talking`` animation
#: nothing is played, and the chat window's own "Peeko is speaking…" indicator
#: plus the Speak button are what tell the user.
TALKING_STATE = TALKING


def expression_state(animation: str, machine) -> str:
    """The avatar state that will play for ``animation``.

    Never returns something the machine cannot play: unknown names, and
    intents whose states are missing from the artwork, resolve to
    :data:`FALLBACK_STATE`.
    """
    name = (animation or "").strip().lower()
    state = ANIMATION_STATE_MAP.get(name)
    if state is None:
        return FALLBACK_STATE
    if not machine.can_play(state):
        return FALLBACK_STATE
    return state


def apply_expression(machine, animation: str) -> str | None:
    """Play the expression for ``animation``; return the state played.

    ``None`` means the machine declined (the user is dragging or holding
    the avatar — user input always wins) and nothing was changed.
    """
    state = expression_state(animation, machine)
    if machine.play_cued(state):
        return state
    return None


def known_animations() -> tuple[str, ...]:
    """Every AI animation name this table understands."""
    return tuple(ANIMATION_STATE_MAP)


# --------------------------------------------------------------------------- #
# Stage 4: the voice-input listening state
# --------------------------------------------------------------------------- #
def listening_available(machine) -> bool:
    """Can the current artwork show the listening animation at all?"""
    return bool(machine.can_play(LISTENING))


def apply_listening(machine, listening: bool) -> str | None:
    """Show (or end) the listening animation for a voice capture.

    :param machine: the avatar state machine.
    :param listening: ``True`` when a capture started, ``False`` when it
        ended.
    :returns: the state played, or ``None`` when the machine declined — the
        artwork has no ``listening`` animation, the user is dragging the robot
        right now, or there was no pose to end. When a pose *was* ended the
        state now on screen is returned (idle, or the talking pose if Peeko is
        still speaking). The chat window keeps showing its own "listening…"
        indicator either way, so a decline is never a lie.
    """
    if listening:
        return LISTENING if machine.start_listening() else None
    if not machine.stop_listening():
        return None
    return machine.state


# --------------------------------------------------------------------------- #
# Stage 5: the voice-output talking state
# --------------------------------------------------------------------------- #
def talking_available(machine) -> bool:
    """Can the current artwork show the talking animation at all?"""
    return bool(machine.can_play(TALKING))


def apply_speaking(machine, speaking: bool) -> str | None:
    """Show (or end) the talking animation while Peeko speaks.

    :param machine: the avatar state machine.
    :param speaking: ``True`` when a playback started, ``False`` when it
        finished (or failed, or was stopped).
    :returns: the state played, or ``None`` when the machine declined — the
        artwork has no ``talking`` animation, the user is dragging the robot
        right now, or there was no playback to end. When a pose *was* ended the
        state now on screen is returned: idle, or the **listening** pose when
        the microphone is still open (talking outranks listening, so the
        listening pose is what comes back). The chat window keeps showing its
        own "Peeko is speaking…" indicator either way, so a decline is never a
        lie.
    """
    if speaking:
        return TALKING if machine.start_talking() else None
    if not machine.stop_talking():
        return None
    return machine.state


__all__ = [
    "ANIMATION_STATE_MAP",
    "EMOTION_ANIMATION_MAP",
    "FALLBACK_STATE",
    "LISTENING_STATE",
    "TALKING_STATE",
    "apply_emotion",
    "apply_expression",
    "emotion_animation",
    "apply_listening",
    "apply_speaking",
    "expression_state",
    "known_animations",
    "listening_available",
    "talking_available",
]
