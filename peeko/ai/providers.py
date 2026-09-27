"""Chat providers: an engine-agnostic seam with one real implementation.

Peeko talks to the AI through a small interface
(:class:`ChatProvider` — one method, ``complete``) so the conversation
system never depends on a vendor. Today there is exactly one real provider:

* :class:`OpenAICompatibleProvider` — the standard OpenAI
  ``POST {base_url}/chat/completions`` API, which is also spoken by every
  OpenAI-compatible endpoint (OpenAI itself, Azure-style gateways, local
  servers such as Ollama, LM Studio or llama.cpp, and aggregators such as
  OpenRouter or Groq). The base URL and model come from the environment
  (``PEEKO_AI_BASE_URL`` / ``PEEKO_AI_MODEL``), so switching endpoint is a
  configuration change, not a code change.

Design rules this module follows:

* **stdlib only** — the request is made with :mod:`urllib.request`, no
  extra dependency to install or keep patched;
* **never blocks the UI thread** — callers run ``complete()`` on a worker
  (see :mod:`peeko.ai.worker`); the timeout is always finite;
* **the key is never logged** — it goes into one ``Authorization`` header
  and every user-facing message is scrubbed with
  :func:`peeko.ai.errors.redact`;
* **no shell, no OS control** — this module only ever performs an HTTPS
  request; nothing in the AI package can run a command.
"""

from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

from peeko.ai.errors import (
    AIConfigError,
    AIError,
    AIProviderError,
    AIResponseError,
    AITimeoutError,
    redact,
)

LOG = logging.getLogger("peeko.ai")

#: Where an OpenAI-compatible provider lives unless told otherwise.
DEFAULT_BASE_URL = "https://api.openai.com/v1"

#: How long Peeko waits for an answer before giving up (seconds).
DEFAULT_TIMEOUT_S = 30.0

#: Provider names accepted in ``PEEKO_AI_PROVIDER``.
SUPPORTED_PROVIDERS: tuple[str, ...] = ("openai", "openai-compatible")

#: Provider aliases normalised onto :data:`SUPPORTED_PROVIDERS`.
_PROVIDER_ALIASES = {
    "openai": "openai",
    "openai-compatible": "openai-compatible",
    "openai_compatible": "openai-compatible",
    "openai_compat": "openai-compatible",
    "compatible": "openai-compatible",
}

#: How much of an error body is quoted back in a message.
_ERROR_BODY_LIMIT = 300

#: Signature of the HTTP transport: (url, headers, payload, timeout) -> body.
Transport = Callable[[str, Mapping[str, str], Mapping[str, Any], float], str]


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #
@runtime_checkable
class ChatProvider(Protocol):
    """Anything Peeko can send a conversation to.

    Implementations must not touch the UI, must not raise anything except
    :class:`peeko.ai.errors.AIError` subclasses, and must return the
    assistant's raw text (validation happens in
    :mod:`peeko.ai.schema`).
    """

    name: str
    model: str

    def is_configured(self) -> bool:  # pragma: no cover - protocol
        ...

    def configuration_problem(self) -> str:  # pragma: no cover - protocol
        ...

    def complete(self, messages: Sequence[Mapping[str, str]], **options: Any
                 ) -> str:  # pragma: no cover - protocol
        ...


def normalise_provider_name(name: object) -> str:
    """Map a configured provider name onto a supported one (``""`` if not)."""
    if not isinstance(name, str):
        return ""
    return _PROVIDER_ALIASES.get(name.strip().lower(), "")


# --------------------------------------------------------------------------- #
# Default transport (stdlib HTTPS POST)
# --------------------------------------------------------------------------- #
def urlopen_post_json(url: str, headers: Mapping[str, str],
                      payload: Mapping[str, Any], timeout: float) -> str:
    """POST ``payload`` as JSON and return the response body as text.

    Raises :class:`~peeko.ai.errors.AITimeoutError` /
    :class:`~peeko.ai.errors.AIProviderError` with a readable message.
    This is the only place in Peeko that talks to the network.
    """
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST", headers=dict(headers)
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:  # 4xx/5xx with a body
        detail = _body_snippet(exc)
        raise AIProviderError(
            f"The AI service answered with HTTP {exc.code}."
            + (f" It said: {detail}" if detail else "")
        ) from exc
    except urllib.error.URLError as exc:
        if _is_timeout(getattr(exc, "reason", None)):
            raise AITimeoutError(_timeout_message(timeout)) from exc
        raise AIProviderError(
            f"Could not reach the AI service at {url} "
            f"({_reason_text(getattr(exc, 'reason', exc))})."
        ) from exc
    except TimeoutError as exc:  # plain socket timeout
        raise AITimeoutError(_timeout_message(timeout)) from exc


def _timeout_message(timeout: float) -> str:
    return f"The AI service did not answer within {timeout:g} seconds."


def _is_timeout(reason: object) -> bool:
    return isinstance(reason, (TimeoutError, socket.timeout))


def _reason_text(reason: object) -> str:
    text = str(reason).strip() or reason.__class__.__name__
    return text[:200]


def _body_snippet(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - the body is only a nicety
        return ""
    return " ".join(raw.split())[:_ERROR_BODY_LIMIT]


# --------------------------------------------------------------------------- #
# OpenAI-compatible provider
# --------------------------------------------------------------------------- #
class OpenAICompatibleProvider:
    """Chat-completions provider for OpenAI and compatible endpoints.

    :param api_key: the user's key (never logged, never re-serialised).
    :param model: model name, e.g. ``gpt-4o-mini``.
    :param base_url: API root, e.g. ``https://api.openai.com/v1``. A full
        ``.../chat/completions`` URL is accepted too.
    :param timeout_s: seconds to wait for an answer.
    :param transport: HTTP transport override — the tests inject a fake so
        the suite never touches the network.
    :param name: provider name reported in logs/diagnostics.
    """

    name = "openai"

    def __init__(self, *, api_key: str = "", model: str = "",
                 base_url: str = "", timeout_s: float = DEFAULT_TIMEOUT_S,
                 transport: Transport | None = None,
                 name: str = "openai") -> None:
        self.name = name
        self.model = (model or "").strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).strip()
        self.timeout_s = float(timeout_s) if timeout_s else DEFAULT_TIMEOUT_S
        self._api_key = api_key or ""
        self._transport: Transport = transport or urlopen_post_json

    # -- configuration -------------------------------------------------- #
    @property
    def api_key(self) -> str:
        """The key (kept for the settings view; never logged)."""
        return self._api_key

    @property
    def api_key_set(self) -> bool:
        """Whether a key is configured — safe to show/log."""
        return bool(self._api_key)

    def is_configured(self) -> bool:
        return bool(self._api_key and self.model)

    def configuration_problem(self) -> str:
        """A user-facing sentence about what is missing (``""`` if fine)."""
        if not self._api_key:
            return "AI not configured — set PEEKO_AI_API_KEY in .env"
        if not self.model:
            return "AI not configured — set PEEKO_AI_MODEL in .env (for example gpt-4o-mini)"
        return ""

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(name={self.name!r}, model={self.model!r}, "
            f"base_url={self.base_url!r}, "
            f"api_key={'<set>' if self._api_key else '<unset>'})"
        )

    # -- request building ----------------------------------------------- #
    def completions_url(self) -> str:
        """The full chat-completions URL for the configured base URL."""
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def request_headers(self) -> dict[str, str]:
        """Headers for the request (the only place the key appears)."""
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def request_payload(self, messages: Sequence[Mapping[str, str]],
                        **options: Any) -> dict[str, Any]:
        """The JSON body sent to the provider (inspectable in tests)."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [dict(message) for message in messages],
        }
        for key in ("temperature", "max_tokens"):
            value = options.get(key)
            if value is not None:
                payload[key] = value
        return payload

    # -- calling --------------------------------------------------------- #
    def complete(self, messages: Sequence[Mapping[str, str]],
                 **options: Any) -> str:
        """Send the conversation and return the assistant's raw text.

        Runs a blocking HTTP request — call it off the UI thread.
        """
        problem = self.configuration_problem()
        if problem:
            raise AIConfigError(problem)
        checked = [_checked_message(m) for m in messages]
        if not checked:
            raise AIConfigError("There is nothing to send to the AI service.")

        url = self.completions_url()
        payload = self.request_payload(checked, **options)
        LOG.debug(
            "AI request: provider=%s model=%s url=%s messages=%d key_set=%s",
            self.name, self.model, url, len(checked), self.api_key_set,
        )
        body = self._call_transport(url, payload)
        return self.parse_body(body)

    def _call_transport(self, url: str, payload: Mapping[str, Any]) -> str:
        """Call the transport, normalising every failure into an AIError."""
        try:
            return self._transport(
                url, self.request_headers(), payload, self.timeout_s
            )
        except AIError as exc:
            raise type(exc)(redact(exc.message, self._api_key)) from exc
        except _TIMEOUT_ERRORS as exc:
            raise AITimeoutError(_timeout_message(self.timeout_s)) from exc
        except Exception as exc:  # noqa: BLE001 - transport is pluggable
            LOG.debug("AI transport failed", exc_info=True)
            raise AIProviderError(
                redact(
                    f"Could not reach the AI service ({_reason_text(exc)}).",
                    self._api_key,
                )
            ) from exc

    def parse_body(self, body: str) -> str:
        """Pull the assistant text out of a chat-completions body."""
        try:
            data = json.loads(body)
        except (ValueError, TypeError) as exc:
            raise AIResponseError(
                "The AI service sent a response Peeko could not read "
                "(it was not valid JSON)."
            ) from exc
        if not isinstance(data, dict):
            raise AIResponseError(
                "The AI service sent an unexpected response shape."
            )
        if isinstance(data.get("error"), (dict, str)):
            message = data["error"]
            if isinstance(message, dict):
                message = message.get("message", "unknown error")
            raise AIProviderError(
                redact(f"The AI service reported an error: {message}",
                       self._api_key)
            )
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AIResponseError(
                "The AI service returned no answer (no choices in the reply)."
            )
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):  # some endpoints send content parts
            content = "".join(
                str(part.get("text", "")) for part in content
                if isinstance(part, dict)
            )
        if not isinstance(content, str) or not content.strip():
            raise AIResponseError(
                "The AI service returned an answer Peeko could not read."
            )
        return content.strip()


_TIMEOUT_ERRORS: tuple[type[BaseException], ...] = (TimeoutError, socket.timeout)


def _checked_message(message: Mapping[str, str]) -> dict[str, str]:
    """Validate one chat message before it is sent."""
    if not isinstance(message, Mapping):
        raise AIConfigError("A chat message must be a mapping with a role.")
    role = message.get("role")
    content = message.get("content")
    if not isinstance(role, str) or not role.strip():
        raise AIConfigError("A chat message was missing its role.")
    if not isinstance(content, str) or not content.strip():
        raise AIConfigError("A chat message was missing its text.")
    return {"role": role.strip(), "content": content}


def build_provider(provider_name: str, *, api_key: str = "", model: str = "",
                   base_url: str = "", timeout_s: float = DEFAULT_TIMEOUT_S,
                   transport: Transport | None = None) -> OpenAICompatibleProvider:
    """Create the provider for ``provider_name``.

    :raises ~peeko.ai.errors.AIConfigError: for a provider Peeko does not
        support — named honestly, with the list of supported ones.
    """
    normalised = normalise_provider_name(provider_name)
    if not normalised:
        supported = ", ".join(SUPPORTED_PROVIDERS)
        raise AIConfigError(
            f"AI provider {provider_name!r} is not supported yet "
            f"(supported: {supported}). Set PEEKO_AI_PROVIDER in .env."
        )
    return OpenAICompatibleProvider(
        api_key=api_key, model=model, base_url=base_url,
        timeout_s=timeout_s, transport=transport, name=normalised,
    )


__all__ = [
    "ChatProvider",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT_S",
    "OpenAICompatibleProvider",
    "SUPPORTED_PROVIDERS",
    "Transport",
    "build_provider",
    "normalise_provider_name",
    "urlopen_post_json",
]
