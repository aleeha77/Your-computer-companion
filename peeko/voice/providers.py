"""Speech-to-text providers: an engine-agnostic seam with one real engine.

Peeko turns speech into text through a small interface
(:class:`SpeechToTextProvider` — one method, ``transcribe``) so the chat
window never depends on a vendor. Today there is exactly one real provider:

* :class:`OpenAITranscriptionProvider` — the standard OpenAI
  ``POST {base_url}/audio/transcriptions`` endpoint, which is also spoken by
  every OpenAI-compatible gateway or local server. The base URL and model
  come from the environment (``PEEKO_STT_BASE_URL`` / ``PEEKO_STT_MODEL``),
  so switching endpoint is a configuration change, not a code change.

There is also :class:`MockTranscriptionProvider`, which exists **only for the
tests**: it answers with canned text and records the clips it was given, so
the whole capture → provider → UI path can be exercised without a microphone,
a network or an API key. Nothing selects it at runtime — an unknown engine
name is refused honestly by :func:`build_transcriber`.

Design rules this module follows (the same ones as :mod:`peeko.ai.providers`):

* **stdlib only** — the request is built and sent with :mod:`uuid` and
  :mod:`urllib.request`, no extra dependency to install or keep patched;
* **audio never touches the disk** — the clip is encoded to WAV in memory and
  uploads as one ``multipart/form-data`` part;
* **never blocks the UI thread** — callers run ``transcribe()`` on a worker
  (see :mod:`peeko.voice.worker`); the timeout is always finite;
* **the key is never logged** — it goes into one ``Authorization`` header and
  every user-facing message is scrubbed with :func:`peeko.voice.errors.redact`;
* **no shell, no OS control** — this module only ever performs one HTTPS
  request and cannot run a command.
"""

from __future__ import annotations

import json
import logging
import socket
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

from peeko.voice.audio import AudioClip
from peeko.voice.errors import (
    STTProviderError,
    STTResponseError,
    STTTimeoutError,
    VoiceConfigError,
    VoiceError,
    redact,
)

LOG = logging.getLogger("peeko.voice")

#: Where an OpenAI-compatible speech service lives unless told otherwise.
DEFAULT_BASE_URL = "https://api.openai.com/v1"

#: Transcription model used when ``PEEKO_STT_MODEL`` is not set.
DEFAULT_MODEL = "whisper-1"

#: How long Peeko waits for a transcription before giving up (seconds).
DEFAULT_TIMEOUT_S = 60.0

#: Engine names accepted in ``PEEKO_VOICE_INPUT_ENGINE``.
SUPPORTED_PROVIDERS: tuple[str, ...] = ("openai", "openai-compatible")

#: Provider aliases normalised onto :data:`SUPPORTED_PROVIDERS`.
_PROVIDER_ALIASES = {
    "openai": "openai",
    "openai-compatible": "openai-compatible",
    "openai_compatible": "openai-compatible",
    "openai_compat": "openai-compatible",
    "compatible": "openai-compatible",
    "whisper": "openai-compatible",
}

#: Path appended to the base URL to reach the transcription endpoint.
TRANSCRIPTIONS_PATH = "/audio/transcriptions"

#: Filename reported to the service (the audio is never a real file).
UPLOAD_FILENAME = "peeko-voice.wav"

#: Content type of the uploaded part.
UPLOAD_CONTENT_TYPE = "audio/wav"

#: How much of an error body is quoted back in a message.
_ERROR_BODY_LIMIT = 300

#: Signature of the HTTP transport: (url, headers, body, timeout) -> text.
TranscriptionTransport = Callable[[str, Mapping[str, str], bytes, float], str]


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #
@runtime_checkable
class SpeechToTextProvider(Protocol):
    """Anything Peeko can send captured audio to.

    Implementations must not touch the UI, must not raise anything except
    :class:`~peeko.voice.errors.VoiceError` subclasses, and must return the
    recognised text (``""`` when the audio contained no words).
    """

    name: str
    model: str

    def is_configured(self) -> bool:  # pragma: no cover - protocol
        ...

    def configuration_problem(self) -> str:  # pragma: no cover - protocol
        ...

    def transcribe(self, clip: AudioClip) -> str:  # pragma: no cover - protocol
        ...


def normalise_provider_name(name: object) -> str:
    """Map a configured engine name onto a supported one (``""`` if not)."""
    if not isinstance(name, str):
        return ""
    return _PROVIDER_ALIASES.get(name.strip().lower(), "")


def resolve_engine(engine: object) -> str:
    """The supported engine ``engine`` names, or a clear configuration error.

    ``""``/``None`` means "use the default engine" (``openai-compatible``,
    i.e. the documented OpenAI transcription API).
    """
    if engine is None or str(engine).strip() == "":
        return "openai-compatible"
    normalised = normalise_provider_name(engine)
    if not normalised:
        supported = ", ".join(SUPPORTED_PROVIDERS)
        raise VoiceConfigError(
            f"Voice input engine {engine!r} is not supported yet "
            f"(supported: {supported}). Set PEEKO_VOICE_INPUT_ENGINE in .env."
        )
    return normalised


# --------------------------------------------------------------------------- #
# multipart/form-data encoding (stdlib only)
# --------------------------------------------------------------------------- #
def make_boundary() -> str:
    """A fresh multipart boundary (unique per request)."""
    return f"peeko-{uuid.uuid4().hex}"


def encode_multipart(fields: Mapping[str, str], *, name: str,
                     filename: str, content: bytes, content_type: str,
                     boundary: str) -> bytes:
    """Encode ``fields`` plus one file part as a ``multipart/form-data`` body.

    Written by hand on purpose: ``requests``/``urllib3`` are not dependencies
    of this project, and the format is small enough to build correctly here
    (and to test exactly).
    """
    lines: list[bytes] = []
    for field_name, value in fields.items():
        lines.append(f"--{boundary}".encode("utf-8"))
        lines.append(
            f'Content-Disposition: form-data; name="{field_name}"'.encode(
                "utf-8"
            )
        )
        lines.append(b"")
        lines.append(str(value).encode("utf-8"))
    lines.append(f"--{boundary}".encode("utf-8"))
    lines.append(
        f'Content-Disposition: form-data; name="{name}"; '
        f'filename="{filename}"'.encode("utf-8")
    )
    lines.append(f"Content-Type: {content_type}".encode("utf-8"))
    lines.append(b"")
    lines.append(content)
    lines.append(f"--{boundary}--".encode("utf-8"))
    lines.append(b"")
    # Every part is separated by CRLF; joining with CRLF is exactly what the
    # format asks for (and what servers expect).
    return b"\r\n".join(lines)


# --------------------------------------------------------------------------- #
# Default transport (stdlib HTTPS POST)
# --------------------------------------------------------------------------- #
def urlopen_post_body(url: str, headers: Mapping[str, str], body: bytes,
                      timeout: float) -> str:
    """POST a raw body and return the response text.

    Raises :class:`~peeko.voice.errors.STTTimeoutError` /
    :class:`~peeko.voice.errors.STTProviderError` with a readable message.
    This is the only place in the voice package that talks to the network.
    """
    request = urllib.request.Request(
        url, data=body, method="POST", headers=dict(headers)
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:  # 4xx/5xx with a body
        detail = _body_snippet(exc)
        raise STTProviderError(
            f"The speech service answered with HTTP {exc.code}."
            + (f" It said: {detail}" if detail else "")
        ) from exc
    except urllib.error.URLError as exc:
        if _is_timeout(getattr(exc, "reason", None)):
            raise STTTimeoutError(_timeout_message(timeout)) from exc
        raise STTProviderError(
            f"Could not reach the speech service at {url} "
            f"({_reason_text(getattr(exc, 'reason', exc))})."
        ) from exc
    except TimeoutError as exc:  # plain socket timeout
        raise STTTimeoutError(_timeout_message(timeout)) from exc


def _timeout_message(timeout: float) -> str:
    return f"The speech service did not answer within {timeout:g} seconds."


def _is_timeout(reason: object) -> bool:
    return isinstance(reason, (TimeoutError, socket.timeout))


def _reason_text(reason: object) -> str:
    text = " ".join(str(reason).split()) or type(reason).__name__
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
class OpenAITranscriptionProvider:
    """Speech-to-text through an OpenAI-compatible transcription endpoint.

    :param api_key: the user's key (never logged, never re-serialised).
    :param model: transcription model, e.g. ``whisper-1``.
    :param base_url: API root, e.g. ``https://api.openai.com/v1``. A full
        ``.../audio/transcriptions`` URL is accepted too.
    :param timeout_s: seconds to wait for a transcription.
    :param transport: HTTP transport override — the tests inject a fake so the
        suite never touches the network.
    :param name: provider name reported in logs/diagnostics.
    """

    name = "openai"

    def __init__(self, *, api_key: str = "", model: str = "",
                 base_url: str = "", timeout_s: float = DEFAULT_TIMEOUT_S,
                 transport: TranscriptionTransport | None = None,
                 name: str = "openai") -> None:
        self.name = name
        self.model = (model or "").strip() or DEFAULT_MODEL
        self.base_url = (base_url or DEFAULT_BASE_URL).strip()
        self.timeout_s = float(timeout_s) if timeout_s else DEFAULT_TIMEOUT_S
        self._api_key = api_key or ""
        self._transport: TranscriptionTransport = transport or urlopen_post_body

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
            return (
                "Voice input not configured — set PEEKO_AI_API_KEY in .env "
                "(the same key Peeko uses to chat)."
            )
        if not self.model:
            return (
                "Voice input not configured — set PEEKO_STT_MODEL in .env "
                f"(for example {DEFAULT_MODEL})."
            )
        return ""

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(name={self.name!r}, model={self.model!r}, "
            f"base_url={self.base_url!r}, "
            f"api_key={'<set>' if self._api_key else '<unset>'})"
        )

    # -- request building ----------------------------------------------- #
    def transcriptions_url(self) -> str:
        """The full transcription URL for the configured base URL."""
        base = self.base_url.rstrip("/")
        if base.endswith(TRANSCRIPTIONS_PATH):
            return base
        return f"{base}{TRANSCRIPTIONS_PATH}"

    def request_headers(self, boundary: str) -> dict[str, str]:
        """Headers for one request (the only place the key appears)."""
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": (
                f"multipart/form-data; boundary={boundary}"
            ),
            "Accept": "application/json",
        }

    def request_body(self, clip: AudioClip, boundary: str) -> bytes:
        """The multipart body for one clip (WAV encoded in memory)."""
        return encode_multipart(
            {"model": self.model, "response_format": "json"},
            name="file",
            filename=UPLOAD_FILENAME,
            content=clip.to_wav_bytes(),
            content_type=UPLOAD_CONTENT_TYPE,
            boundary=boundary,
        )

    # -- calling --------------------------------------------------------- #
    def transcribe(self, clip: AudioClip, **options: Any) -> str:
        """Send one clip and return the recognised text (``""`` for silence).

        Runs a blocking HTTP request — call it off the UI thread. Raises
        :class:`~peeko.voice.errors.VoiceError` subclasses whose messages are
        safe to show the user.
        """
        problem = self.configuration_problem()
        if problem:
            raise VoiceConfigError(problem)
        if clip is None or clip.is_empty():
            # Nothing to transcribe: no request, no invented text.
            LOG.debug("No audio to transcribe — skipping the request.")
            return ""

        url = self.transcriptions_url()
        boundary = make_boundary()
        body = self.request_body(clip, boundary)
        headers = self.request_headers(boundary)
        LOG.debug(
            "Voice request: provider=%s model=%s url=%s audio=%.2fs bytes=%d "
            "key_set=%s",
            self.name, self.model, url, clip.duration_s, len(body),
            self.api_key_set,
        )
        raw = self._call_transport(url, headers, body)
        return self.parse_body(raw)

    def _call_transport(self, url: str, headers: Mapping[str, str],
                        body: bytes) -> str:
        """Call the transport, normalising every failure into a VoiceError."""
        try:
            return self._transport(url, headers, body, self.timeout_s)
        except VoiceError as exc:
            raise type(exc)(redact(exc.message, self._api_key)) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise STTTimeoutError(_timeout_message(self.timeout_s)) from exc
        except Exception as exc:  # noqa: BLE001 - transport is pluggable
            # The exception text comes from a library we do not control, so it
            # is redacted before it can reach a log line (see
            # peeko.voice.errors.redact), and the traceback is deliberately
            # not logged: it would re-introduce the unredacted text.
            LOG.debug("Voice transport failed: %s",
                      redact(_reason_text(exc), self._api_key))
            raise STTProviderError(
                redact(
                    f"Could not reach the speech service "
                    f"({_reason_text(exc)}).",
                    self._api_key,
                )
            ) from exc

    def parse_body(self, body: str) -> str:
        """Pull the recognised text out of a transcription response.

        A response with an empty ``text`` is legitimate — it means the audio
        held no words — and yields ``""`` rather than an invented sentence.
        """
        try:
            data = json.loads(body)
        except (ValueError, TypeError) as exc:
            raise STTResponseError(
                "The speech service sent a response Peeko could not read "
                "(it was not valid JSON)."
            ) from exc
        if not isinstance(data, dict):
            raise STTResponseError(
                "The speech service sent an unexpected response shape."
            )
        if isinstance(data.get("error"), (dict, str)):
            message = data["error"]
            if isinstance(message, dict):
                message = message.get("message", "unknown error")
            raise STTProviderError(
                redact(f"The speech service reported an error: {message}",
                       self._api_key)
            )
        if "text" not in data:
            raise STTResponseError(
                "The speech service returned no text (no 'text' field in the "
                "reply)."
            )
        text = data.get("text")
        if not isinstance(text, str):
            raise STTResponseError(
                "The speech service returned a 'text' field Peeko could not "
                "read."
            )
        return text.strip()


#: The one real provider, under its descriptive name.
OpenAICompatibleTranscriptionProvider = OpenAITranscriptionProvider


# --------------------------------------------------------------------------- #
# Test double
# --------------------------------------------------------------------------- #
class MockTranscriptionProvider:
    """A canned speech-to-text provider — **for the tests only**.

    It never touches the network: it records every clip it is handed and
    answers with queued texts (or a queued error). :func:`build_transcriber`
    never returns one of these, so it cannot be reached from configuration.
    """

    name = "mock"
    model = "mock-model"

    def __init__(self, text: str = "hello", *,
                 texts: Sequence[str] | None = None,
                 error: BaseException | None = None,
                 delay_s: float = 0.0) -> None:
        self.text = text
        self._queued = list(texts) if texts is not None else None
        self.error = error
        self.delay_s = float(delay_s)
        self.clips: list[AudioClip] = []

    def is_configured(self) -> bool:
        return True

    def configuration_problem(self) -> str:
        return ""

    def transcribe(self, clip: AudioClip) -> str:
        self.clips.append(clip)
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        if self._queued is not None:
            return self._queued.pop(0) if self._queued else ""
        return self.text

    @property
    def call_count(self) -> int:
        return len(self.clips)

    @property
    def last_clip(self) -> AudioClip:
        return self.clips[-1]


def build_transcriber(engine: str, *, api_key: str = "", model: str = "",
                      base_url: str = "",
                      timeout_s: float = DEFAULT_TIMEOUT_S,
                      transport: TranscriptionTransport | None = None
                      ) -> OpenAICompatibleTranscriptionProvider:
    """Create the provider for ``engine``.

    :raises ~peeko.voice.errors.VoiceConfigError: for an engine Peeko does
        not support — named honestly, with the list of supported ones.
    """
    normalised = resolve_engine(engine)
    return OpenAICompatibleTranscriptionProvider(
        api_key=api_key, model=model, base_url=base_url,
        timeout_s=timeout_s, transport=transport, name=normalised,
    )


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_S",
    "MockTranscriptionProvider",
    "OpenAICompatibleTranscriptionProvider",
    "OpenAITranscriptionProvider",
    "SUPPORTED_PROVIDERS",
    "SpeechToTextProvider",
    "TRANSCRIPTIONS_PATH",
    "TranscriptionTransport",
    "UPLOAD_CONTENT_TYPE",
    "UPLOAD_FILENAME",
    "build_transcriber",
    "encode_multipart",
    "make_boundary",
    "normalise_provider_name",
    "resolve_engine",
    "urlopen_post_body",
]
