"""Peeko's structured AI output: parsing and *controlled* validation.

The model is asked to answer with JSON such as::

    {"response": "...", "emotion": "playful",
     "animation": "talking_happy", "action": null}

Model output is treated as **untrusted data**. :func:`parse_response`
turns it into an :class:`AIResponse` and enforces

* a non-empty ``response`` string (without one there is nothing to show,
  so the caller gets an honest :class:`~peeko.ai.errors.AIResponseError`
  instead of a made-up reply);
* ``emotion`` / ``animation`` values from the controlled lists in
  :mod:`peeko.ai.vocabulary` — anything else is replaced by the documented
  default and recorded in :attr:`AIResponse.notes`;
* ``action`` — only values from :data:`peeko.ai.vocabulary.ALLOWED_ACTIONS`
  survive (currently none: only ``null`` passes), so no model output can
  ever ask Peeko to do something that is not explicitly implemented.

Parsing is forgiving about *packaging* (a reply wrapped in ```` ```json ````
fences, or plain prose instead of JSON) but strict about *values*: Peeko
shows what the model really said, with the fallbacks it had to apply.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Any

from peeko.ai.errors import AIResponseError
from peeko.ai.vocabulary import (
    DEFAULT_ACTION,
    DEFAULT_ANIMATION,
    DEFAULT_EMOTION,
    is_allowed_action,
    is_allowed_animation,
    is_allowed_emotion,
)

LOG = logging.getLogger("peeko.ai")

#: Longest reply Peeko will display from one message (characters).
MAX_RESPONSE_CHARS = 1200

#: Keys a well-formed reply may use (only ``response`` is mandatory).
RESPONSE_KEYS = ("response", "emotion", "animation", "action")

_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<body>.*?)```", re.DOTALL | re.IGNORECASE)
_JSON_START_RE = re.compile(r"[{[]")


@dataclass(frozen=True)
class AIResponse:
    """One validated AI reply, ready to show and to act on.

    :param response: the text to display to the user.
    :param emotion: one of :data:`peeko.ai.vocabulary.ALLOWED_EMOTIONS`.
    :param animation: one of :data:`peeko.ai.vocabulary.ALLOWED_ANIMATIONS`.
    :param action: ``None`` at this stage (see
        :data:`peeko.ai.vocabulary.ALLOWED_ACTIONS`).
    :param notes: human-readable notes about every value that was rejected
        or adjusted — empty for a clean reply.
    """

    response: str
    emotion: str = DEFAULT_EMOTION
    animation: str = DEFAULT_ANIMATION
    action: str | None = DEFAULT_ACTION
    notes: tuple[str, ...] = ()

    @property
    def degraded(self) -> bool:
        """True when at least one field had to be adjusted or rejected."""
        return bool(self.notes)

    def as_dict(self) -> dict[str, Any]:
        """Plain, JSON-ready view of the reply (the documented shape)."""
        return {
            "response": self.response,
            "emotion": self.emotion,
            "animation": self.animation,
            "action": self.action,
        }

    def __repr__(self) -> str:  # keep logs short — the text can be long
        return (
            f"AIResponse(emotion={self.emotion!r}, animation={self.animation!r},"
            f" action={self.action!r}, response={self.response[:40]!r}"
            f"{'...' if len(self.response) > 40 else ''}, notes={len(self.notes)})"
        )


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def extract_json(text: str) -> Any | None:
    """Best-effort extraction of a JSON object from a model reply.

    Handles the three shapes seen in practice: bare JSON, JSON inside a
    ```` ```json ```` fence, and JSON embedded in prose. Returns the parsed
    object, or ``None`` when no JSON can be read (a normal case — smaller
    models often answer with plain prose, which is not an error).
    """
    if not isinstance(text, str):
        return None
    candidate = text.strip()
    if not candidate:
        return None

    for chunk in _json_candidates(candidate):
        try:
            return json.loads(chunk)
        except (ValueError, TypeError):
            continue
    return None


def _json_candidates(text: str):
    """Yield strings worth trying as JSON, most likely first."""
    yield text
    for match in _FENCE_RE.finditer(text):
        yield match.group("body").strip()
    start = _JSON_START_RE.search(text)
    if start is not None:
        yield text[start.start():].strip()
        # Also try the balanced tail: first "{" through the last "}".
        opener = text[start.start()]
        closer = "}" if opener == "{" else "]"
        end = text.rfind(closer)
        if end > start.start():
            yield text[start.start():end + 1]


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _clean_response_text(value: Any) -> str:
    """The reply text, or ``""`` when there is nothing usable."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _validate_emotion(value: Any, notes: list[str]) -> str:
    if value is None:
        return DEFAULT_EMOTION
    if is_allowed_emotion(value):
        return str(value).strip().lower()
    notes.append(f"emotion {value!r} is not allowed — using {DEFAULT_EMOTION!r}")
    return DEFAULT_EMOTION


def _validate_animation(value: Any, notes: list[str]) -> str:
    if value is None:
        return DEFAULT_ANIMATION
    if is_allowed_animation(value):
        return str(value).strip().lower()
    notes.append(
        f"animation {value!r} is not allowed — using {DEFAULT_ANIMATION!r}"
    )
    return DEFAULT_ANIMATION


def _validate_action(value: Any, notes: list[str]) -> str | None:
    """Only predefined actions survive — never free-form model output.

    Peeko cannot do anything yet, so every requested action is dropped and
    reported. The model can never reach the operating system through this
    field: the value is a *name* compared against
    :data:`peeko.ai.vocabulary.ALLOWED_ACTIONS` and nothing else.
    """
    if value is None:
        return DEFAULT_ACTION
    if is_allowed_action(value):
        return str(value).strip().lower()
    notes.append(f"action {value!r} is not implemented — ignored (None)")
    return DEFAULT_ACTION


def parse_response(payload: str | dict | AIResponse) -> AIResponse:
    """Validate a model reply into an :class:`AIResponse`.

    :param payload: the raw model text, or an already-decoded mapping.
    :raises ~peeko.ai.errors.AIResponseError: when the payload contains no
        usable reply text at all (empty body, a JSON list, or a mapping
        without a readable ``response``) — the caller must then tell the
        user honestly instead of inventing an answer.
    """
    if isinstance(payload, AIResponse):
        return payload

    notes: list[str] = []
    if isinstance(payload, dict):
        data: dict = payload
    elif isinstance(payload, (list, tuple)):
        raise AIResponseError(
            "The AI answered with a list instead of a reply — Peeko needs "
            "an object with a 'response' field."
        )
    elif isinstance(payload, str):
        extracted = extract_json(payload)
        if isinstance(extracted, dict):
            data = extracted
        elif extracted is not None:
            raise AIResponseError(
                "The AI answered with JSON that is not a reply object — "
                "Peeko expected a 'response' field."
            )
        else:
            # No JSON at all: a plain-prose answer is still a real answer.
            text = payload.strip()
            if not text:
                raise AIResponseError("The AI returned an empty reply.")
            notes.append("reply was not JSON — using it as plain text")
            data = {"response": text}
    else:
        raise AIResponseError(
            f"The AI returned an unexpected payload ({type(payload).__name__})."
        )

    raw_text = data.get("response")
    text = _clean_response_text(raw_text)
    if not text:
        raise AIResponseError(
            "The AI reply had no readable text in its 'response' field."
        )
    if len(text) > MAX_RESPONSE_CHARS:
        text = text[:MAX_RESPONSE_CHARS].rstrip() + "…"
        notes.append(
            f"reply was longer than {MAX_RESPONSE_CHARS} characters — truncated"
        )

    unknown_keys = sorted(set(data) - set(RESPONSE_KEYS))
    if unknown_keys:
        notes.append("ignored extra field(s): " + ", ".join(unknown_keys))

    response = AIResponse(
        response=text,
        emotion=_validate_emotion(data.get("emotion"), notes),
        animation=_validate_animation(data.get("animation"), notes),
        action=_validate_action(data.get("action"), notes),
        notes=tuple(notes),
    )

    for note in response.notes:
        LOG.info("AI reply adjusted: %s", note)
    return response


def coerce_response(payload: str | dict | AIResponse,
                    fallback_text: str | None = None) -> AIResponse:
    """Like :func:`parse_response`, but never raises.

    Used on paths where an exception must not escape (background workers
    reporting back to the UI). Without ``fallback_text`` an unusable
    payload still raises, because there is nothing honest to show.
    """
    try:
        return parse_response(payload)
    except AIResponseError:
        if fallback_text is None:
            raise
        return replace(
            AIResponse(response=fallback_text),
            notes=("the AI reply could not be read — showing nothing else",),
        )


__all__ = [
    "AIResponse",
    "MAX_RESPONSE_CHARS",
    "RESPONSE_KEYS",
    "coerce_response",
    "extract_json",
    "parse_response",
]
