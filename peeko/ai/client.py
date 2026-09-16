"""AI subsystem: conversational intelligence for Peeko.

Stage 3 (AI chat): NOT IMPLEMENTED YET.

This module defines the planned interface plus configuration-only
support. The owner will supply their own optional API key via
``PEEKO_AI_API_KEY``; nothing here requires network access at Stage 0,
and no AI work will ever block the UI thread.

The :class:`AIClient` interface below is the contract for Stage 3; its
methods raise ``NotImplementedError`` until implemented.
"""

from __future__ import annotations

import logging

LOG = logging.getLogger("peeko.ai")


class AIClient:
    """Planned conversational AI client (Stage 3).

    Holds configuration only at Stage 0. ``api_key`` is stored but must
    never be logged or included in repr.
    """

    def __init__(self, provider: str = "openai", model: str = "",
                 api_key: str = "") -> None:
        self.provider = provider
        self.model = model
        self._api_key = api_key
        LOG.debug("AIClient configured: provider=%r model=%r key_set=%s",
                  provider, model, bool(api_key))

    def is_configured(self) -> bool:
        """True once a usable provider + model + key are set (Stage 3)."""
        return bool(self.provider and self.api_key)

    @property
    def api_key(self) -> str:
        return self._api_key

    def __repr__(self) -> str:  # never leak the key in repr
        return (f"AIClient(provider={self.provider!r}, model={self.model!r}, "
                f"api_key={'<set>' if self._api_key else '<unset>'})")

    # -- Stage 3 interface (unimplemented) -------------------------------
    def chat(self, messages: list[dict], **kwargs) -> str:
        """Send a chat conversation and return the assistant reply.

        .. note:: Stage 3 — not implemented yet.
        """
        raise NotImplementedError(
            "AIClient.chat is not implemented until Stage 3 (AI chat)."
        )