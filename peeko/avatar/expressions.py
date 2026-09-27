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
"""

from __future__ import annotations

from peeko.avatar.state_machine import (
    BLINK,
    CLICK,
    CONFUSED,
    DOUBLE_CLICK,
    HOVER,
    IDLE,
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


__all__ = [
    "ANIMATION_STATE_MAP",
    "FALLBACK_STATE",
    "apply_expression",
    "expression_state",
    "known_animations",
]
