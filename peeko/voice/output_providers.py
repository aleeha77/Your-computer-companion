"""Text-to-speech providers: an engine-agnostic seam with one real engine.

The mirror image of :mod:`peeko.voice.providers`. That module turns speech
into text; this one turns text into speech. Peeko speaks through a small
interface (:class:`SpeechProvider` — one method, ``synthesize``) so the chat
window never depends on a vendor. Today there is exactly one real provider:

* :class:`OpenAISpeechProvider` — the standard OpenAI
  ``POST {base_url}/audio/speech`` endpoint, which is also spoken by every
  OpenAI-compatible gateway or local server (Ollama-style TTS servers,
  LocalAI, LiteLLM, …). Model, voice, speed, format and base URL come from
  the environment, so switching endpoint is a configuration change, not a
  code change.

There is also :class:`MockSpeechProvider`, which exists **only for the
tests**: it answers with canned audio bytes and records the texts it was
given, so the whole synthesise → play path can be exercised without a
speaker, a network or an API key. Nothing selects it at runtime — an unknown
engine name is refused honestly by :func:`build_speaker`.

Design rules this module follows (the same ones as
:mod:`peeko.voice.providers`):

* **stdlib only** — the request is built and sent with :mod:`json` and
  :mod:`urllib.request`, no extra dependency to install or keep patched;
* **audio never touches the disk** — the synthesiser returns the bytes it
  received, in memory, and nothing here opens a file for writing;
* **never blocks the UI thread** — callers run ``synthesize()`` on a worker
  (see :mod:`peeko.voice.worker`); the timeout is always finite;
* **the key is never logged** — it goes into one ``Authorization`` header and
  every user-facing message is scrubbed with :func:`peeko.voice.errors.redact`;
* **no shell, no OS control** — this module only ever performs one HTTPS
  request and cannot run a command.

Why WAV by default

The requested ``response_format`` defaults to WAV
(:data:`DEFAULT_RESPONSE_FORMAT`) because WAV is the one container Peeko can
decode with the standard library alone (see :mod:`peeko.voice.player`).
Asking for MP3 would mean adding a decoder dependency just to hear Peeko
talk, so the default trades a few bytes of bandwidth for zero new
dependencies.
"""

from __future__ import annotations

import json
import logging
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

from peeko.voice.errors import (
    TTSProviderError,
    TTSResponseError,
    TTSTimeoutError,
    VoiceConfigError,
    VoiceError,
    redact,
)

LOG = logging.getLogger("peeko.voice")

#: Where an OpenAI-compatible speech service lives unless told otherwise.
DEFAULT_BASE_URL = "https://api.openai.com/v1"

#: Speech model used when ``PEEKO_TTS_MODEL`` is not set.
DEFAULT_MODEL = "tts-1"

#: Voice used when ``PEEKO_TTS_VOICE`` is not set.
DEFAULT_VOICE = "alloy"

#: Speaking rate used when ``PEEKO_TTS_SPEED`` is not set (1.0 = normal).
DEFAULT_SPEED = 1.0

#: Playback volume used when ``PEEKO_TTS_VOLUME`` is not set (1.0 = full).
DEFAULT_VOLUME = 1.0

#: How long Peeko waits for a synthesis before giving up (seconds).
DEFAULT_TIMEOUT_S = 60.0

#: Engine names accepted in ``PEEKO_TTS_ENGINE``.
SUPPORTED_PROVIDERS: tuple[str, ...] = ("openai", "openai-compatible")

#: Provider aliases normalised onto :data:`SUPPORTED_PROVIDERS`.
_PROVIDER_ALIASES = {
    "openai": "openai",
    "openai-compatible": "openai-compatible",
    "openai_compatible": "openai-compatible",
    "openai_compat": "openai-compatible",
    "compatible": "openai-compatible",
    "tts": "openai-compatible",
    "speech": "openai-compatible",
}

#: Path appended to the base URL to reach the speech endpoint.
SPEECH_PATH = "/audio/speech"

#: Audio container Peeko asks for (see the module docstring).
DEFAULT_RESPONSE_FORMAT = "wav"

#: Content type of each audio container Peeko knows how to name.
FORMAT_CONTENT_TYPES: dict[str, str] = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "pcm": "audio/pcm",
}

#: How much of an error body is quoted back in a message.
_ERROR_BODY_LIMIT = 300

#: Signature of the HTTP transport: (url, headers, body, timeout) -> bytes.
SpeechTransport = Callable[[str, Mapping[str, str], bytes, float], bytes]


# --------------------------------------------------------------------------- #
# The audio Peeko received
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SpeechClip:
    """Synthesised speech, held in memory.

    :param audio: the encoded audio exactly as the service sent it (WAV by
        default). It is *never* written to a file — see the privacy notes in
        :mod:`peeko.voice.output`.
    :param content_type: the container's media type (``audio/wav``).
    :param response_format: the container Peeko asked for (``wav``).
    :param voice: the voice that was requested (for diagnostics, never the
        text that was spoken).
    :param model: the model that was requested.
    :param characters: how many characters were sent for synthesis. The text
        itself is deliberately **not** kept: a clip cannot leak what Peeko
        said into a log line or a crash report.
    """

    audio: bytes = b""
    content_type: str = FORMAT_CONTENT_TYPES[DEFAULT_RESPONSE_FORMAT]
    response_format: str = DEFAULT_RESPONSE_FORMAT
    voice: str = ""
    model: str = ""
    characters: int = 0

    def is_empty(self) -> bool:
        """True when nothing was synthesised (no audio at all)."""
        return not self.audio

    @property
    def size_bytes(self) -> int:
        """Length of the audio payload, in bytes."""
        return len(self.audio)

    def __repr__(self) -> str:  # never echoes the spoken text
        return (
            f"SpeechClip(bytes={self.size_bytes}, "
            f"format={self.response_format!r}, voice={self.voice!r}, "
            f"model={self.model!r}, characters={self.characters})"
        )


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #
@runtime_checkable
class SpeechProvider(Protocol):
    """Anything Peeko can ask for spoken audio.

    Implementations must not touch the UI, must not raise anything except
    :class:`~peeko.voice.errors.VoiceError` subclasses, and must return a
    :class:`SpeechClip` (an *empty* one when there was nothing to say).
    """

    name: str
    model: str
    voice: str

    def is_configured(self) -> bool:  # pragma: no cover - protocol
        ...

    def configuration_problem(self) -> str:  # pragma: no cover - protocol
        ...

    def synthesize(self, text: str) -> SpeechClip:  # pragma: no cover - protocol
        ...


def normalise_provider_name(name: object) -> str:
    """Map a configured engine name onto a supported one (``""`` if not)."""
    if not isinstance(name, str):
        return ""
    return _PROVIDER_ALIASES.get(name.strip().lower(), "")


def resolve_engine(engine: object) -> str:
    """The supported engine ``engine`` names, or a clear configuration error.

    ``""``/``None`` means "use the default engine" (``openai-compatible``,
    i.e. the documented OpenAI speech API).
    """
    if engine is None or str(engine).strip() == "":
        return "openai-compatible"
    normalised = normalise_provider_name(engine)
    if not normalised:
        supported = ", ".join(SUPPORTED_PROVIDERS)
        raise VoiceConfigError(
            f"Speech engine {engine!r} is not supported yet "
            f"(supported: {supported}). Set PEEKO_TTS_ENGINE in .env."
        )
    return normalised


def content_type_for(response_format: str) -> str:
    """The media type for a requested container (``audio/wav`` fallback)."""
    key = (response_format or "").strip().lower()
    return FORMAT_CONTENT_TYPES.get(key, "application/octet-stream")


def looks_like_json(payload: bytes) -> bool:
    """Whether a response body is JSON rather than audio.

    A service that refuses a request usually answers with a JSON error object
    where audio should be, so this is how Peeko tells "the request failed"
    from "here is your audio" without depending on HTTP status handling.
    """
    return payload.lstrip()[:1] in (b"{", b"[")


# --------------------------------------------------------------------------- #
# Default transport (stdlib HTTPS POST)
# --------------------------------------------------------------------------- #
def urlopen_post_bytes(url: str, headers: Mapping[str, str], body: bytes,
                       timeout: float) -> bytes:
    """POST a raw body and return the response bytes.

    Raises :class:`~peeko.voice.errors.TTSTimeoutError` /
    :class:`~peeko.voice.errors.TTSProviderError` with a readable message.
    This is the only place in this module that talks to the network.
    """
    request = urllib.request.Request(
        url, data=body, method="POST", headers=dict(headers)
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:  # 4xx/5xx with a body
        detail = _body_snippet(exc)
        raise TTSProviderError(
            f"The speech service answered with HTTP {exc.code}."
            + (f" It said: {detail}" if detail else "")
        ) from exc
    except urllib.error.URLError as exc:
        if _is_timeout(getattr(exc, "reason", None)):
            raise TTSTimeoutError(_timeout_message(timeout)) from exc
        raise TTSProviderError(
            f"Could not reach the speech service at {url} "
            f"({_reason_text(getattr(exc, 'reason', exc))})."
        ) from exc
    except TimeoutError as exc:  # plain socket timeout
        raise TTSTimeoutError(_timeout_message(timeout)) from exc


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
class OpenAISpeechProvider:
    """Text-to-speech through an OpenAI-compatible speech endpoint.

    :param api_key: the user's key (never logged, never re-serialised).
    :param model: speech model, e.g. ``tts-1``.
    :param voice: voice name, e.g. ``alloy``. An unknown name is refused by
        the service with an HTTP 4xx, which Peeko reports in plain words —
        it never invents a fallback voice.
    :param speed: speaking rate (``0.25``–``4.0`` at OpenAI).
    :param response_format: audio container to ask for (WAV by default).
    :param base_url: API root, e.g. ``https://api.openai.com/v1``. A full
        ``.../audio/speech`` URL is accepted too.
    :param timeout_s: seconds to wait for a synthesis.
    :param transport: HTTP transport override — the tests inject a fake so the
        suite never touches the network.
    :param name: provider name reported in logs/diagnostics.
    """

    name = "openai"

    def __init__(self, *, api_key: str = "", model: str = "", voice: str = "",
                 speed: float = DEFAULT_SPEED,
                 response_format: str = DEFAULT_RESPONSE_FORMAT,
                 base_url: str = "", timeout_s: float = DEFAULT_TIMEOUT_S,
                 transport: SpeechTransport | None = None,
                 name: str = "openai") -> None:
        self.name = name
        self.model = (model or "").strip() or DEFAULT_MODEL
        self.voice = (voice or "").strip() or DEFAULT_VOICE
        self.speed = _positive(speed, DEFAULT_SPEED)
        self.response_format = (
            (response_format or "").strip().lower() or DEFAULT_RESPONSE_FORMAT
        )
        self.base_url = (base_url or DEFAULT_BASE_URL).strip()
        self.timeout_s = _positive(timeout_s, DEFAULT_TIMEOUT_S)
        self._api_key = api_key or ""
        self._transport: SpeechTransport = transport or urlopen_post_bytes

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
        return bool(self._api_key and self.model and self.voice)

    def configuration_problem(self) -> str:
        """A user-facing sentence about what is missing (``""`` if fine)."""
        if not self._api_key:
            return (
                "Peeko cannot speak yet — set PEEKO_AI_API_KEY in .env "
                "(the same key Peeko uses to chat)."
            )
        if not self.model:
            return (
                "Peeko cannot speak yet — set PEEKO_TTS_MODEL in .env "
                f"(for example {DEFAULT_MODEL})."
            )
        if not self.voice:
            return (
                "Peeko cannot speak yet — set PEEKO_TTS_VOICE in .env "
                f"(for example {DEFAULT_VOICE})."
            )
        return ""

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(name={self.name!r}, model={self.model!r}, "
            f"voice={self.voice!r}, speed={self.speed:g}, "
            f"format={self.response_format!r}, base_url={self.base_url!r}, "
            f"api_key={'<set>' if self._api_key else '<unset>'})"
        )

    # -- request building ----------------------------------------------- #
    def speech_url(self) -> str:
        """The full speech URL for the configured base URL."""
        base = self.base_url.rstrip("/")
        if base.endswith(SPEECH_PATH):
            return base
        return f"{base}{SPEECH_PATH}"

    def request_headers(self) -> dict[str, str]:
        """Headers for one request (the only place the key appears)."""
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": content_type_for(self.response_format),
        }

    def request_body(self, text: str) -> bytes:
        """The JSON body for one utterance (never written to disk)."""
        return json.dumps({
            "model": self.model,
            "input": text,
            "voice": self.voice,
            "speed": self.speed,
            "response_format": self.response_format,
        }).encode("utf-8")

    # -- calling --------------------------------------------------------- #
    def synthesize(self, text: str, **options: Any) -> SpeechClip:
        """Ask for ``text`` to be spoken and return the audio bytes.

        Runs a blocking HTTP request — call it off the UI thread. Raises
        :class:`~peeko.voice.errors.VoiceError` subclasses whose messages are
        safe to show the user. An empty/whitespace text yields an *empty*
        clip without any request at all.
        """
        spoken = (text or "").strip()
        if not spoken:
            # Nothing to say: no request, no invented audio.
            LOG.debug("No text to speak — skipping the request.")
            return SpeechClip(voice=self.voice, model=self.model)

        problem = self.configuration_problem()
        if problem:
            raise VoiceConfigError(problem)

        url = self.speech_url()
        body = self.request_body(spoken)
        headers = self.request_headers()
        LOG.debug(
            "Speech request: provider=%s model=%s voice=%s speed=%g "
            "format=%s url=%s characters=%d key_set=%s",
            self.name, self.model, self.voice, self.speed,
            self.response_format, url, len(spoken), self.api_key_set,
        )
        raw = self._call_transport(url, headers, body)
        return self.parse_body(raw, characters=len(spoken))

    def _call_transport(self, url: str, headers: Mapping[str, str],
                        body: bytes) -> bytes:
        """Call the transport, normalising every failure into a VoiceError."""
        try:
            return self._transport(url, headers, body, self.timeout_s)
        except VoiceError as exc:
            raise type(exc)(redact(exc.message, self._api_key)) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise TTSTimeoutError(_timeout_message(self.timeout_s)) from exc
        except Exception as exc:  # noqa: BLE001 - transport is pluggable
            # The exception text comes from a library we do not control, so it
            # is redacted before it can reach a log line (see
            # peeko.voice.errors.redact), and the traceback is deliberately
            # not logged: it would re-introduce the unredacted text.
            LOG.debug("Speech transport failed: %s",
                      redact(_reason_text(exc), self._api_key))
            raise TTSProviderError(
                redact(
                    f"Could not reach the speech service "
                    f"({_reason_text(exc)}).",
                    self._api_key,
                )
            ) from exc

    def parse_body(self, raw: bytes, *, characters: int = 0) -> SpeechClip:
        """Turn a response body into a :class:`SpeechClip`.

        A JSON body where audio was expected is an error the service is
        reporting, so it is surfaced as a readable message (never played as
        if it were sound). Empty audio is legitimate — some services answer
        with nothing for an empty utterance — and yields an empty clip.
        """
        if raw is None:
            raw = b""
        if isinstance(raw, str):  # a transport that decoded by accident
            raw = raw.encode("utf-8", errors="replace")
        if raw and looks_like_json(raw):
            detail = _json_error_detail(raw)
            raise TTSResponseError(
                redact(
                    "The speech service returned an error instead of audio"
                    + (f": {detail}" if detail else "."),
                    self._api_key,
                )
            )
        return SpeechClip(
            audio=raw,
            content_type=content_type_for(self.response_format),
            response_format=self.response_format,
            voice=self.voice,
            model=self.model,
            characters=int(characters),
        )


def _json_error_detail(raw: bytes) -> str:
    """A short, single-line description of a JSON error body."""
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, TypeError):
        return " ".join(raw.decode("utf-8", errors="replace").split())[
            :_ERROR_BODY_LIMIT
        ]
    message = data.get("error") if isinstance(data, dict) else data
    if isinstance(message, dict):
        message = message.get("message", "unknown error")
    if isinstance(message, str):
        return " ".join(message.split())[:_ERROR_BODY_LIMIT]
    return json.dumps(message)[:_ERROR_BODY_LIMIT]


def _positive(value: object, default: float) -> float:
    """A strictly positive float, or ``default`` for anything unusable."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return parsed if parsed > 0 else float(default)


#: The one real provider, under its descriptive name.
OpenAICompatibleSpeechProvider = OpenAISpeechProvider


# --------------------------------------------------------------------------- #
# Test double
# --------------------------------------------------------------------------- #
class MockSpeechProvider:
    """A canned text-to-speech provider — **for the tests only**.

    It never touches the network: it records every text it is handed and
    answers with queued audio (or a queued error). :func:`build_speaker`
    never returns one of these, so it cannot be reached from configuration.
    """

    name = "mock"
    model = "mock-model"

    def __init__(self, audio: bytes = b"", *,
                 audios: Sequence[bytes] | None = None,
                 voice: str = "mock-voice",
                 error: BaseException | None = None,
                 delay_s: float = 0.0) -> None:
        self.audio = audio
        self._queued = list(audios) if audios is not None else None
        self.voice = voice
        self.error = error
        self.delay_s = float(delay_s)
        self.texts: list[str] = []
        self.calls = 0

    def is_configured(self) -> bool:
        return True

    def configuration_problem(self) -> str:
        return ""

    def synthesize(self, text: str) -> SpeechClip:
        self.calls += 1
        self.texts.append(text)
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        payload = self.audio
        if self._queued is not None:
            payload = self._queued.pop(0) if self._queued else b""
        return SpeechClip(
            audio=payload, voice=self.voice, model=self.model,
            characters=len((text or "").strip()),
        )

    @property
    def call_count(self) -> int:
        return self.calls

    @property
    def last_text(self) -> str:
        return self.texts[-1]


def build_speaker(engine: str, *, api_key: str = "", model: str = "",
                  voice: str = "", speed: float = DEFAULT_SPEED,
                  response_format: str = DEFAULT_RESPONSE_FORMAT,
                  base_url: str = "", timeout_s: float = DEFAULT_TIMEOUT_S,
                  transport: SpeechTransport | None = None
                  ) -> OpenAISpeechProvider:
    """Create the provider for ``engine``.

    :raises ~peeko.voice.errors.VoiceConfigError: for an engine Peeko does
        not support — named honestly, with the list of supported ones.
    """
    normalised = resolve_engine(engine)
    return OpenAICompatibleSpeechProvider(
        api_key=api_key, model=model, voice=voice, speed=speed,
        response_format=response_format, base_url=base_url,
        timeout_s=timeout_s, transport=transport, name=normalised,
    )


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_RESPONSE_FORMAT",
    "DEFAULT_SPEED",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_VOICE",
    "FORMAT_CONTENT_TYPES",
    "MockSpeechProvider",
    "OpenAICompatibleSpeechProvider",
    "OpenAISpeechProvider",
    "SPEECH_PATH",
    "SUPPORTED_PROVIDERS",
    "SpeechClip",
    "SpeechProvider",
    "SpeechTransport",
    "build_speaker",
    "content_type_for",
    "looks_like_json",
    "normalise_provider_name",
    "resolve_engine",
    "urlopen_post_bytes",
]
