"""Errors raised by the voice subsystem (Stage 4 in, Stage 5 out).

Like :mod:`peeko.ai.errors`, every message here is written to be shown to
the user **as-is**: it names what went wrong and what to do about it, and it
never contains the API key (every message passes through :func:`redact`
first, and no module in this package ever builds a message out of a secret).

The classes are split so the UI can be honest about *what kind* of problem
it hit — a missing key is something the owner fixes in ``.env``, a missing
microphone is something they fix on their machine, and a service error is
something that may just work again later.
"""

from __future__ import annotations

from peeko.errors import PeekoError


class VoiceError(PeekoError):
    """Base class for expected voice failures (safe to show the user)."""


class VoiceConfigError(VoiceError):
    """Voice cannot run yet: missing or unsupported configuration."""


class VoiceUnavailableError(VoiceError):
    """Audio cannot be used on this machine (no audio library or no device).

    Used by both halves of the feature: a machine with no microphone cannot
    *listen* (:mod:`peeko.voice.audio`) and one with no speakers — or without
    the optional audio library — cannot *speak* (:mod:`peeko.voice.player`).
    """


class VoicePermissionError(VoiceUnavailableError):
    """The operating system refused access to the microphone."""


class VoiceCaptureError(VoiceError):
    """The microphone was opened but capturing from it failed."""


class STTProviderError(VoiceError):
    """The speech-to-text service could not be reached, or answered badly."""


class STTTimeoutError(STTProviderError):
    """The speech-to-text service did not answer within the timeout."""


class STTResponseError(VoiceError):
    """The service answered with something Peeko cannot use."""


class TTSProviderError(VoiceError):
    """The text-to-speech service could not be reached, or answered badly."""


class TTSTimeoutError(TTSProviderError):
    """The text-to-speech service did not answer within the timeout."""


class TTSResponseError(VoiceError):
    """The service sent audio (or a reply) Peeko cannot play.

    A different kind of problem from a service error: the request worked, but
    what came back is not audio Peeko can hand to the speakers.
    """


class TTSPlaybackError(VoiceError):
    """The speech was synthesised but could not be played on this machine."""


def redact(message: str, secret: str | None) -> str:
    """Replace ``secret`` with ``[REDACTED]`` in a user-facing message.

    Defence in depth: the voice package never puts the key into a message in
    the first place, but text that comes back from an HTTP body or a library
    exception is scrubbed before it can reach a log line or the chat window.
    """
    if not secret:
        return message
    return message.replace(secret, "[REDACTED]")


__all__ = [
    "STTProviderError",
    "STTResponseError",
    "STTTimeoutError",
    "TTSPlaybackError",
    "TTSProviderError",
    "TTSResponseError",
    "TTSTimeoutError",
    "VoiceCaptureError",
    "VoiceConfigError",
    "VoiceError",
    "VoicePermissionError",
    "VoiceUnavailableError",
    "redact",
]
