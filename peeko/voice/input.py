"""Voice input (speech-to-text) for Peeko — Stage 4.

This module is the seam the chat window talks to: **microphone in, words
out**. It wires the two halves of the feature together —

* :mod:`peeko.voice.audio` — capture from a microphone (or any injected
  :class:`~peeko.voice.audio.AudioSource`), which is where the *lazy* audio
  dependency lives, and
* :mod:`peeko.voice.providers` — the speech-to-text service seam (one real
  OpenAI-compatible engine, one test double),

— and makes exactly one promise: it returns real words or an honest failure,
never an invented sentence.

    recognizer = SpeechRecognizer.from_settings(settings)
    text = recognizer.listen()      # blocking: call it off the UI thread

``listen()`` returns ``None`` when there was nothing to report (the user
cancelled, no audio arrived, or the service heard no words), and raises a
:class:`~peeko.voice.errors.VoiceError` subclass with a user-facing message
when something is genuinely wrong (no key, no microphone, permission denied,
capture failure, HTTP error, timeout).

Blocking by design

``listen()`` blocks for as long as you keep talking (up to the configured
cap), so the UI runs it on a worker thread — see
:mod:`peeko.voice.worker`. Nothing in this module imports Qt.

Privacy

* audio is captured in memory and uploaded **only** to the configured
  speech-to-text endpoint;
* nothing is written to disk — there is no file writing in this package;
* transcripts are not stored anywhere yet (persistent memory is Stage 8), so
  leaving the chat window open is the only place your words exist.
"""

from __future__ import annotations

import logging
from typing import Callable

from peeko.voice.audio import (
    DEFAULT_MAX_SECONDS,
    AudioClip,
    AudioSource,
    capture_clip,
    default_audio_source,
)
from peeko.voice.errors import (
    VoiceConfigError,
    VoiceError,
    VoiceUnavailableError,
)
from peeko.voice.providers import (
    DEFAULT_TIMEOUT_S,
    SpeechToTextProvider,
    build_transcriber,
)

LOG = logging.getLogger("peeko.voice")

#: Shown when the microphone itself cannot be used.
NO_MIC_CONFIGURED_TEXT = (
    "Voice input unavailable: no microphone is available to Peeko."
)


class SpeechRecognizer:
    """Microphone → text for Peeko (Stage 4).

    :param source: the :class:`~peeko.voice.audio.AudioSource` to capture
        from. Defaults to the real ``sounddevice`` source, which imports its
        audio library lazily — a machine without one still runs Peeko.
    :param provider: the speech-to-text provider. Defaults to ``None``,
        meaning "not configured yet"; :meth:`from_settings` builds the real
        one.
    :param max_duration_s: hard cap on one recording, in seconds.
    :param engine_problem: a message recorded when the configured engine name
        could not be used (kept instead of raising, so a typo in ``.env``
        cannot crash anything — it is reported when the user tries to talk).
    """

    def __init__(self, *, source: AudioSource | None = None,
                 provider: SpeechToTextProvider | None = None,
                 max_duration_s: float = DEFAULT_MAX_SECONDS,
                 engine_problem: str = "") -> None:
        self._source: AudioSource = source or default_audio_source()
        self._provider = provider
        self.max_duration_s = (
            float(max_duration_s)
            if max_duration_s and float(max_duration_s) > 0
            else DEFAULT_MAX_SECONDS
        )
        self._engine_problem = engine_problem

    # ------------------------------------------------------------------ #
    # Construction from the app's settings
    # ------------------------------------------------------------------ #
    @classmethod
    def from_settings(cls, settings: object, *,
                      source: AudioSource | None = None,
                      provider: SpeechToTextProvider | None = None,
                      transport=None) -> "SpeechRecognizer":
        """Build a recognizer from :class:`peeko.settings.Settings`.

        The speech-to-text key is the same ``PEEKO_AI_API_KEY`` the chat uses
        (one key to configure, as long as your provider serves both); the
        engine, model and base URL come from ``PEEKO_VOICE_INPUT_ENGINE``,
        ``PEEKO_STT_MODEL`` and ``PEEKO_STT_BASE_URL`` — the latter falling
        back to ``PEEKO_AI_BASE_URL`` so an OpenAI-compatible gateway only has
        to be configured once.

        A bad engine name never raises here: it is recorded and reported
        honestly by :meth:`availability_problem` when the user presses Mic.
        """
        engine_problem = ""
        built: SpeechToTextProvider | None = provider
        if built is None:
            try:
                built = build_transcriber(
                    str(getattr(settings, "voice_input_engine", "") or ""),
                    api_key=str(getattr(settings, "ai_api_key", "") or ""),
                    model=str(getattr(settings, "voice_stt_model", "") or ""),
                    base_url=str(
                        getattr(settings, "voice_stt_base_url", "")
                        or getattr(settings, "ai_base_url", "")
                        or ""
                    ),
                    timeout_s=float(
                        getattr(settings, "voice_timeout_s", DEFAULT_TIMEOUT_S)
                        or DEFAULT_TIMEOUT_S
                    ),
                    transport=transport,
                )
            except VoiceError as exc:
                LOG.warning("Voice input engine unusable: %s", exc.message)
                engine_problem = exc.message
        return cls(
            source=source,
            provider=built,
            max_duration_s=float(
                getattr(settings, "voice_max_seconds", DEFAULT_MAX_SECONDS)
                or DEFAULT_MAX_SECONDS
            ),
            engine_problem=engine_problem,
        )

    # ------------------------------------------------------------------ #
    # Configuration state (honest, never leaking the key)
    # ------------------------------------------------------------------ #
    @property
    def source(self) -> AudioSource:
        """The audio source this recognizer captures from."""
        return self._source

    @property
    def provider(self) -> SpeechToTextProvider | None:
        """The speech-to-text provider (``None`` when unusable)."""
        return self._provider

    @property
    def engine(self) -> str:
        """The engine name in use (``"none"`` when nothing can be used)."""
        return getattr(self._provider, "name", "") or "none"

    @property
    def model(self) -> str:
        """The transcription model in use (``""`` when nothing can be used)."""
        return getattr(self._provider, "model", "") or ""

    @property
    def api_key_set(self) -> bool:
        """Whether a key is configured — safe to log or display."""
        return bool(getattr(self._provider, "api_key_set", False))

    def configuration_problem(self) -> str:
        """What stops Peeko from transcribing (``""`` when fine)."""
        if self._engine_problem:
            return self._engine_problem
        if self._provider is None:
            return (
                "Voice input not configured — set PEEKO_AI_API_KEY in .env "
                "(the same key Peeko uses to chat)."
            )
        return self._provider.configuration_problem()

    def microphone_problem(self) -> str:
        """What stops Peeko from capturing (``""`` when the mic is usable)."""
        try:
            return self._source.availability()
        except Exception as exc:  # noqa: BLE001 - a duck-typed source
            LOG.debug("Audio source availability check failed", exc_info=True)
            return (
                "Voice input unavailable: the audio input could not be "
                f"checked ({_reason_text(exc)})."
            )

    def availability_problem(self) -> str:
        """Everything that would stop a capture, joined for display.

        Both halves are checked **before** the microphone is opened: with no
        key configured Peeko never touches your microphone at all.
        """
        parts = [p for p in (self.configuration_problem(),
                             self.microphone_problem()) if p]
        return " ".join(parts)

    def is_available(self) -> bool:
        """Could a capture start right now?"""
        return self.availability_problem() == ""

    def describe(self) -> str:
        """One safe diagnostic line (never contains the key)."""
        return (
            f"engine={self.engine} model={self.model or '<unset>'} "
            f"mic={self._source.name} "
            f"api_key={'<set>' if self.api_key_set else '<unset>'} "
            f"max_seconds={self.max_duration_s:g} "
            f"available={self.is_available()}"
        )

    def __repr__(self) -> str:  # never leak the key in repr
        return (
            f"SpeechRecognizer(engine={self.engine!r}, "
            f"model={self.model!r}, source={self._source.name!r}, "
            f"api_key={'<set>' if self.api_key_set else '<unset>'})"
        )

    # ------------------------------------------------------------------ #
    # Listening
    # ------------------------------------------------------------------ #
    def listen(self, *, should_stop: Callable[[], bool] | None = None,
               on_level: Callable[[float], None] | None = None
               ) -> str | None:
        """Capture one utterance and transcribe it.

        Blocking — run it off the UI thread.

        :param should_stop: polled between audio chunks; when it returns True
            the capture stops and the result is ``None``.
        :param on_level: optional loudness callback (``0.0 .. 1.0`` per
            chunk), best-effort.
        :returns: the recognised text, or ``None`` when there is nothing to
            report (cancelled, no audio captured, or no words in the audio).
        :raises ~peeko.voice.errors.VoiceConfigError: no key / bad engine.
        :raises ~peeko.voice.errors.VoiceUnavailableError: no microphone.
        :raises ~peeko.voice.errors.VoiceError: capture or service failure.
        """
        problem = self.configuration_problem()
        if problem:
            raise VoiceConfigError(problem)
        problem = self.microphone_problem()
        if problem:
            raise VoiceUnavailableError(problem)
        if self._provider is None:  # pragma: no cover - covered above
            raise VoiceConfigError(self.configuration_problem())

        clip = capture_clip(
            self._source,
            max_duration_s=self.max_duration_s,
            should_stop=should_stop,
            on_level=on_level,
        )
        if clip is None:
            LOG.info("Voice capture cancelled — nothing transcribed.")
            return None
        if clip.is_empty():
            LOG.info("No audio was captured — nothing transcribed.")
            return None
        return self.transcribe(clip)

    def transcribe(self, clip: AudioClip) -> str | None:
        """Transcribe an already-captured clip (no microphone involved).

        :returns: the recognised text, or ``None`` when the audio held no
            words (an empty transcript is *not* an error and never becomes an
            invented sentence).
        """
        problem = self.configuration_problem()
        if problem:
            raise VoiceConfigError(problem)
        if clip is None or clip.is_empty():
            return None
        if self._provider is None:  # pragma: no cover - covered above
            raise VoiceConfigError(self.configuration_problem())
        text = (self._provider.transcribe(clip) or "").strip()
        if not text:
            LOG.info("The speech service heard no words (%.2fs of audio).",
                     clip.duration_s)
            return None
        LOG.info("Voice input transcribed %d character(s) from %.2fs.",
                 len(text), clip.duration_s)
        return text

    def __enter__(self) -> "SpeechRecognizer":  # pragma: no cover - symmetry
        return self

    def __exit__(self, *_exc) -> None:  # pragma: no cover - symmetry
        self.close()

    def close(self) -> None:
        """Release the audio source (safe to call repeatedly)."""
        try:
            self._source.close()
        except Exception:  # noqa: BLE001 - closing is best-effort
            LOG.debug("Ignoring an error while closing the audio source",
                      exc_info=True)


def _reason_text(exc: object) -> str:
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text[:200]


def build_recognizer(settings: object, **kwargs) -> SpeechRecognizer:
    """Shorthand for :meth:`SpeechRecognizer.from_settings`."""
    return SpeechRecognizer.from_settings(settings, **kwargs)


__all__ = [
    "NO_MIC_CONFIGURED_TEXT",
    "SpeechRecognizer",
    "build_recognizer",
]
