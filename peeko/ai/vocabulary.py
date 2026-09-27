"""Controlled vocabulary for Peeko's AI chat (Stage 3).

Everything the language model is *allowed* to say about Peeko's body and
mood lives in this one module, as plain data:

* :data:`ALLOWED_EMOTIONS` — how Peeko may feel about the exchange;
* :data:`ALLOWED_ANIMATIONS` — which expression Peeko may play (the names
  the avatar layer understands — see :mod:`peeko.avatar.expressions`);
* :data:`ALLOWED_ACTIONS` — what Peeko may *do*.

**Actions are inert by design.** The owner's rule is that model output must
never be able to run anything on the computer: the only legal action value
today is ``null``, so :data:`ALLOWED_ACTIONS` is empty. When the virtual-pet
needs land (Stages 6/7) real, predefined pet actions (``"sleep"``,
``"eat"``…) can be added to this tuple — the validator reads it at call
time, so a new entry starts working immediately and nothing else changes.

Any value outside these lists is rejected and replaced by the documented
default; the caller is told what was rejected (see
:mod:`peeko.ai.schema`). Nothing here is executed, imported or interpreted.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Emotions
# --------------------------------------------------------------------------- #
#: Emotions the model may label its reply with. Lowercase, single words.
ALLOWED_EMOTIONS: tuple[str, ...] = (
    "neutral",
    "happy",
    "playful",
    "curious",
    "excited",
    "affectionate",
    "surprised",
    "confused",
    "sad",
    "tired",
    "sleepy",
    "hungry",
)

# --------------------------------------------------------------------------- #
# Animations
# --------------------------------------------------------------------------- #
#: Expressions the model may ask for. These are *AI-facing* names: the
#: translation into real avatar states lives in the avatar subsystem
#: (:mod:`peeko.avatar.expressions`) so the conversation system stays
#: independent of the on-screen robot.
#:
#: ``talking*`` / ``*_bounce`` names describe an intent ("answer happily");
#: the avatar maps each one onto the closest animation its current artwork
#: manifest actually has, and falls back to ``idle`` when it has none.
ALLOWED_ANIMATIONS: tuple[str, ...] = (
    "idle",
    "blink",
    "look_left",
    "look_right",
    "look_up",
    "look_down",
    "talking",
    "talking_happy",
    "talking_curious",
    "talking_confused",
    "happy_bounce",
    "excited_bounce",
)

# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
#: Actions the model may request. Deliberately **empty**: only ``null`` is
#: accepted, so no AI output can trigger anything at all. Later stages add
#: predefined pet actions here (never free-form strings).
ALLOWED_ACTIONS: tuple[str, ...] = ()

#: Values used when the model omits a field or sends one outside the lists.
DEFAULT_EMOTION = "neutral"
DEFAULT_ANIMATION = "idle"
DEFAULT_ACTION: str | None = None


# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #
def _normalise(value: object) -> str:
    """Lowercase/strip a candidate name; non-strings become ``""``."""
    if not isinstance(value, str):
        return ""
    return value.strip().lower().replace(" ", "_")


def is_allowed_emotion(value: object) -> bool:
    """Is ``value`` one of :data:`ALLOWED_EMOTIONS`?"""
    return _normalise(value) in ALLOWED_EMOTIONS


def is_allowed_animation(value: object) -> bool:
    """Is ``value`` one of :data:`ALLOWED_ANIMATIONS`?"""
    return _normalise(value) in ALLOWED_ANIMATIONS


def is_allowed_action(value: object) -> bool:
    """Is ``value`` one of :data:`ALLOWED_ACTIONS`?

    ``None`` always counts as allowed — it means "do nothing", which is the
    only action that exists at this stage.
    """
    if value is None:
        return True
    return _normalise(value) in ALLOWED_ACTIONS


def vocabulary_summary() -> dict[str, list[str]]:
    """The controlled lists as plain JSON-ready data (used by prompts/docs)."""
    return {
        "emotions": list(ALLOWED_EMOTIONS),
        "animations": list(ALLOWED_ANIMATIONS),
        "actions": list(ALLOWED_ACTIONS),
    }


__all__ = [
    "ALLOWED_ACTIONS",
    "ALLOWED_ANIMATIONS",
    "ALLOWED_EMOTIONS",
    "DEFAULT_ACTION",
    "DEFAULT_ANIMATION",
    "DEFAULT_EMOTION",
    "is_allowed_action",
    "is_allowed_animation",
    "is_allowed_emotion",
    "vocabulary_summary",
]
