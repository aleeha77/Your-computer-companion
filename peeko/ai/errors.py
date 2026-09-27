"""Errors raised by the AI chat subsystem.

All of them derive from :class:`peeko.errors.PeekoError`, so their
``message`` is written to be shown to the user as-is: it names what went
wrong and what to do about it, and it never contains the API key (every
message goes through :func:`peeko.ai.errors.redact` first).
"""

from __future__ import annotations

from peeko.errors import PeekoError


class AIError(PeekoError):
    """Base class for expected AI-chat failures."""


class AIConfigError(AIError):
    """The chat cannot run yet: missing or unsupported configuration."""


class AIProviderError(AIError):
    """The AI service could not be reached, or answered with an error."""


class AITimeoutError(AIProviderError):
    """The AI service did not answer within the configured timeout."""


class AIResponseError(AIError):
    """The AI service answered with something Peeko cannot use."""


def redact(message: str, secret: str | None) -> str:
    """Replace ``secret`` with ``[REDACTED]`` in a user-facing message.

    Defence in depth: nothing in this package ever puts the key into a
    message, but error text that comes from a transport or an HTTP body is
    scrubbed before it can reach a log line or a dialog.
    """
    if not secret:
        return message
    return message.replace(secret, "[REDACTED]")


__all__ = [
    "AIError",
    "AIConfigError",
    "AIProviderError",
    "AIResponseError",
    "AITimeoutError",
    "redact",
]
