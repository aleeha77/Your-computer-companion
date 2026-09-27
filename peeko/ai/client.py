"""The conversation engine: persona + context in, validated reply out.

:class:`AIClient` is the single entry point the UI uses. It knows nothing
about Qt and nothing about the on-screen robot — it builds the prompt
(persona + structured context + recent turns), asks the configured
:class:`peeko.ai.providers.ChatProvider` for an answer, and validates the
model's JSON against the controlled vocabulary in
:mod:`peeko.ai.vocabulary`.

    client = AIClient.from_settings(settings)
    reply = client.respond("hello!", context=build_context())

``reply`` is an :class:`peeko.ai.schema.AIResponse` — real text, an allowed
emotion, an allowed animation and a ``None`` action (nothing the model says
can make Peeko do anything else).

The client is **blocking by design** (plain HTTP); the UI runs it on a
worker thread (:mod:`peeko.ai.worker`) so the animation never stops. The
API key is stored, handed to the provider, and never logged, never put in
``repr`` and never sent anywhere except the configured provider.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping, Sequence

from peeko.ai.context import ChatContext
from peeko.ai.errors import AIConfigError, AIError
from peeko.ai.errors import AIProviderError  # re-exported for callers
from peeko.ai.personality import build_system_prompt
from peeko.ai.providers import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT_S,
    SUPPORTED_PROVIDERS,
    ChatProvider,
    OpenAICompatibleProvider,
    Transport,
    build_provider,
    normalise_provider_name,
)
from peeko.ai.schema import AIResponse, parse_response

LOG = logging.getLogger("peeko.ai")

#: How many previous turns are sent back to the model by default.
DEFAULT_HISTORY_MESSAGES = 12

#: Roles that may appear in a conversation sent to the provider.
_VALID_ROLES = ("system", "user", "assistant")

_MISSING_KEY = "AI not configured — set PEEKO_AI_API_KEY in .env"
_MISSING_MODEL = ("AI not configured — set PEEKO_AI_MODEL in .env "
                  "(for example gpt-4o-mini)")


class AIClient:
    """Engine-agnostic chat client for Peeko.

    :param provider: provider name from ``PEEKO_AI_PROVIDER`` (``openai`` or
        ``openai-compatible``).
    :param model: model name from ``PEEKO_AI_MODEL``.
    :param api_key: the user's key from ``PEEKO_AI_API_KEY`` — a secret.
    :param base_url: API root from ``PEEKO_AI_BASE_URL`` (any
        OpenAI-compatible endpoint).
    :param timeout_s: request timeout from ``PEEKO_AI_TIMEOUT_S``.
    :param transport: HTTP transport override (tests inject a fake).
    :param chat_provider: a ready-made :class:`ChatProvider` — used by the
        tests, and by any future engine that is not HTTP-based.
    """

    def __init__(self, provider: str = "openai", model: str = "",
                 api_key: str = "", *, base_url: str = "",
                 timeout_s: float | None = None,
                 transport: Transport | None = None,
                 chat_provider: ChatProvider | None = None,
                 history_limit: int = DEFAULT_HISTORY_MESSAGES) -> None:
        self.provider = (provider or "openai").strip()
        self.model = (model or "").strip()
        self.base_url = (base_url or "").strip()
        self.timeout_s = float(timeout_s) if timeout_s else DEFAULT_TIMEOUT_S
        self.history_limit = int(history_limit)
        self._api_key = api_key or ""
        self._transport = transport
        self._chat_provider = chat_provider
        self._built_provider: OpenAICompatibleProvider | None = None
        LOG.debug(
            "AIClient configured: provider=%r model=%r base_url=%r "
            "timeout=%ss key_set=%s",
            self.provider, self.model,
            self.base_url or DEFAULT_BASE_URL, self.timeout_s,
            bool(self._api_key),
        )

    # ------------------------------------------------------------------ #
    # Construction from the app's settings
    # ------------------------------------------------------------------ #
    @classmethod
    def from_settings(cls, settings: object, *,
                      transport: Transport | None = None,
                      chat_provider: ChatProvider | None = None) -> "AIClient":
        """Build a client from :class:`peeko.settings.Settings`."""
        return cls(
            provider=str(getattr(settings, "ai_provider", "") or "openai"),
            model=str(getattr(settings, "ai_model", "") or ""),
            api_key=str(getattr(settings, "ai_api_key", "") or ""),
            base_url=str(getattr(settings, "ai_base_url", "") or ""),
            timeout_s=getattr(settings, "ai_timeout_s", DEFAULT_TIMEOUT_S),
            transport=transport,
            chat_provider=chat_provider,
        )

    # ------------------------------------------------------------------ #
    # Configuration state (honest, never leaking the key)
    # ------------------------------------------------------------------ #
    @property
    def api_key(self) -> str:
        """The configured key (never logged, never in ``repr``)."""
        return self._api_key

    @property
    def api_key_set(self) -> bool:
        """Whether a key is configured — safe to log or display."""
        return bool(self._api_key)

    @property
    def provider_name(self) -> str:
        """The configured provider name (alias of :attr:`provider`)."""
        return self.provider

    def is_configured(self) -> bool:
        """Can Peeko actually hold a conversation right now?"""
        if self._chat_provider is not None:
            return self._chat_provider.is_configured()
        return bool(
            normalise_provider_name(self.provider) and self._api_key and self.model
        )

    def configuration_problem(self) -> str:
        """What is missing, in words a user can act on (``""`` if fine)."""
        if self._chat_provider is not None:
            return self._chat_provider.configuration_problem()
        if not normalise_provider_name(self.provider):
            supported = ", ".join(SUPPORTED_PROVIDERS)
            return (
                f"AI provider {self.provider!r} is not supported yet "
                f"(supported: {supported}). Set PEEKO_AI_PROVIDER in .env."
            )
        if not self._api_key:
            return _MISSING_KEY
        if not self.model:
            return _MISSING_MODEL
        return ""

    def describe(self) -> str:
        """One safe diagnostic line (never contains the key)."""
        return (
            f"provider={self.provider} model={self.model or '<unset>'} "
            f"base_url={self.base_url or DEFAULT_BASE_URL} "
            f"api_key={'<set>' if self._api_key else '<unset>'} "
            f"configured={self.is_configured()}"
        )

    def __repr__(self) -> str:  # never leak the key in repr
        return (
            f"AIClient(provider={self.provider!r}, model={self.model!r}, "
            f"api_key={'<set>' if self._api_key else '<unset>'})"
        )

    # ------------------------------------------------------------------ #
    # Prompt building
    # ------------------------------------------------------------------ #
    def provider_instance(self) -> ChatProvider:
        """The provider Peeko will talk to (built lazily, cached)."""
        if self._chat_provider is not None:
            return self._chat_provider
        if self._built_provider is None:
            self._built_provider = build_provider(
                self.provider,
                api_key=self._api_key,
                model=self.model,
                base_url=self.base_url,
                timeout_s=self.timeout_s,
                transport=self._transport,
            )
        return self._built_provider

    def context_dict(self, context: ChatContext | Mapping[str, Any] | None
                     ) -> dict[str, Any]:
        """Normalise any accepted context into the plain dict sent as JSON."""
        if context is None:
            return {}
        if isinstance(context, ChatContext):
            return context.as_dict()
        return {str(key): value for key, value in dict(context).items()}

    def _history_messages(self, history: Iterable[Mapping[str, Any]]
                          ) -> list[dict[str, str]]:
        """Validate and trim previous turns (newest kept)."""
        cleaned: list[dict[str, str]] = []
        for message in history or ():
            if not isinstance(message, Mapping):
                LOG.debug("Dropping a history entry that is not a mapping")
                continue
            role = str(message.get("role", "")).strip()
            content = message.get("content")
            if role not in _VALID_ROLES or not isinstance(content, str):
                LOG.debug("Dropping a history entry with role=%r", role)
                continue
            cleaned.append({"role": role, "content": content})
        if self.history_limit > 0:
            cleaned = cleaned[-self.history_limit:]
        return cleaned

    def build_prompt(self, user_message: str, *,
                     context: ChatContext | Mapping[str, Any] | None = None,
                     history: Iterable[Mapping[str, Any]] = (),
                     system_prompt: str | None = None) -> list[dict[str, str]]:
        """The full conversation sent to the provider, as plain messages."""
        messages: list[dict[str, str]] = [{
            "role": "system",
            "content": system_prompt or build_system_prompt(
                self.context_dict(context)
            ),
        }]
        messages.extend(self._history_messages(history))
        messages.append({"role": "user", "content": user_message.strip()})
        return messages

    # ------------------------------------------------------------------ #
    # Talking
    # ------------------------------------------------------------------ #
    def chat(self, messages: Sequence[Mapping[str, str]], **options: Any) -> str:
        """Send a ready-made conversation; return the raw assistant text.

        Blocking — call it from a worker thread.
        """
        problem = self.configuration_problem()
        if problem:
            raise AIConfigError(problem)
        provider = self.provider_instance()
        text = provider.complete(messages, **options)
        LOG.debug("AI raw reply: %d character(s)", len(text))
        return text

    def respond(self, user_message: str, *,
                context: ChatContext | Mapping[str, Any] | None = None,
                history: Iterable[Mapping[str, Any]] = (),
                system_prompt: str | None = None,
                **options: Any) -> AIResponse:
        """Answer one user message with a validated :class:`AIResponse`.

        Blocking — call it from a worker thread. Raises
        :class:`peeko.ai.errors.AIError` subclasses with a message that is
        safe to show the user.
        """
        if not isinstance(user_message, str) or not user_message.strip():
            raise AIError("There is nothing to answer — type a message first.")
        messages = self.build_prompt(
            user_message, context=context, history=history,
            system_prompt=system_prompt,
        )
        raw = self.chat(messages, **options)
        reply = parse_response(raw)
        LOG.info(
            "AI reply accepted: emotion=%s animation=%s action=%r notes=%d",
            reply.emotion, reply.animation, reply.action, len(reply.notes),
        )
        return reply


__all__ = [
    "AIClient",
    "AIConfigError",
    "AIError",
    "AIProviderError",
    "AIResponse",
    "DEFAULT_HISTORY_MESSAGES",
]
