"""Expressions: turning an AI reply into the avatar's *existing* animations.

Stage 3 plugs the conversation system into the robot without teaching the
avatar anything about AI. The AI layer returns an *intent* (one of the
names in :data:`peeko.ai.vocabulary.ALLOWED_ANIMATIONS`); this module — the
only place that knows about both worlds — maps that intent onto a state the
current artwork manifest can actually play.

The mapping is honestly approximate: the placeholder artwork has no
dedicated talking animation, so ``talking`` uses the manifest's attentive
``hover`` frames and ``talking_happy`` uses the happy ``click`` bounce.
When the owner replaces the artwork (or a later stage adds real talking
frames), only this table changes.

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
)

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

#: What Peeko plays while a voice capture is running (Stage 4). Optional
#: artwork: without a ``listening`` animation nothing is played, and the chat
#: window's own "listening…" indicator is what tells the user.
LISTENING_STATE = LISTENING


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
        artwork has no ``listening`` animation, or the user is dragging the
        robot right now. The chat window keeps showing its own "listening…"
        indicator either way, so a decline is never a lie.
    """
    if listening:
        return LISTENING if machine.start_listening() else None
    return IDLE if machine.stop_listening() else None


__all__ = [
    "ANIMATION_STATE_MAP",
    "FALLBACK_STATE",
    "LISTENING_STATE",
    "apply_expression",
    "apply_listening",
    "expression_state",
    "known_animations",
    "listening_available",
]
