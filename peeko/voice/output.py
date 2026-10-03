"""Voice output (text-to-speech) for Peeko — Stage 5.

This module is the seam the chat window talks to: **text in, sound out**. It
wires the two halves of the feature together —

* :mod:`peeko.voice.output_providers` — the speech service seam (one real
  OpenAI-compatible ``/audio/speech`` engine, one test double), and
* :mod:`peeko.voice.player` — playback, which is where the *lazy* audio
  dependency lives,

— and makes exactly one promise: it either plays the words it was given or
reports an honest failure, never a pretend utterance.

    speaker = SpeechSynthesizer.from_settings(settings)
    speaker.speak("Hi! I'm Peeko.")   # blocking: call it off the UI thread

``speak()`` returns the :class:`~peeko.voice.output_providers.SpeechClip` it
played, or ``None`` when there was nothing to report (empty text, empty audio
from the service, or a cancelled playback). It raises a
:class:`~peeko.voice.errors.VoiceError` subclass with a user-facing message
when something is genuinely wrong (voice switched off, no key, no audio
device, a bad voice name, an HTTP error, a timeout, unplayable audio).

Blocking by design

Synthesis is one HTTP round trip and playback lasts as long as the sentence
does, so both happen on the caller's thread — the UI runs the whole thing on a
worker thread (see :mod:`peeko.voice.worker`). Nothing in this module imports
Qt.

Privacy

* the reply text is sent to **exactly one place**: the speech endpoint you
  configured, and nowhere else;
* the audio comes back into memory and goes straight to the speakers —
  nothing is written to disk (there is no file writing anywhere in this
  package, and a test enforces it);
* neither the text nor the audio is stored: the clip keeps only a character
  count, so a log line or a crash dump cannot leak what Peeko said (see
  :class:`~peeko.voice.output_providers.SpeechClip`).

Honest failures

This module never guesses. A voice name the service rejects, a service that
cannot be reached, a machine with no speakers and a switched-off feature each
produce their own plain-language message, and :meth:`SpeechSynthesizer.speak`
leaves the caller with ``None`` rather than a lie.
"""

from __future__ import annotations

import logging
from typing import Callable

from peeko.voice.errors import (
    VoiceConfigError,
    VoiceError,
    VoiceUnavailableError,
)
from peeko.voice.output_providers import (
    DEFAULT_SPEED,
    DEFAULT_TIMEOUT_S,
    DEFAULT_VOLUME,
    SpeechClip,
    SpeechProvider,
    build_speaker,
)
from peeko.voice.player import AudioPlayer, default_player

LOG = logging.getLogger("peeko.voice")

#: Shown when the user asks Peeko to speak while the feature is switched off.
DISABLED_TEXT = (
    "Peeko's voice is switched off. Set PEEKO_TTS_ENABLED=1 in .env (next to "
    "the app) and restart Peeko to hear it speak."
)

#: Shown when no speech service could be built at all.
NO_PROVIDER_TEXT = (
    "Peeko cannot speak yet — set PEEKO_AI_API_KEY in .env "
    "(the same key Peeko uses to chat)."
)


class SpeechSynthesizer:
    """Text → speech → speakers for Peeko (Stage 5).

    :param provider: the speech service. Defaults to ``None``, meaning "not
        configured yet"; :meth:`from_settings` builds the real one.
    :param player: the :class:`~peeko.voice.player.AudioPlayer` to play on.
        Defaults to the real ``sounddevice`` player, which imports its audio
        library lazily — a machine without one still runs Peeko.
    :param enabled: whether speaking is switched on at all (see
        ``PEEKO_TTS_ENABLED``). A disabled synthesizer refuses to speak
        instead of pretending to.
    :param volume: playback level, ``0.0 .. 1.0`` (applied in memory).
    :param engine_problem: a message recorded when the configured engine name
        could not be used (kept instead of raising, so a typo in ``.env``
        cannot crash anything — it is reported when the user asks Peeko to
        speak).
    """

    def __init__(self, *, provider: SpeechProvider | None = None,
                 player: AudioPlayer | None = None,
                 enabled: bool = True,
                 volume: float = DEFAULT_VOLUME,
                 engine_problem: str = "") -> None:
        self._provider = provider
        self._player: AudioPlayer = player or default_player()
        self.enabled = bool(enabled)
        self.volume = _clamp_volume(volume)
        self._engine_problem = engine_problem
        self._cancel = False

    # ------------------------------------------------------------------ #
    # Construction from the app's settings
    # ------------------------------------------------------------------ #
    @classmethod
    def from_settings(cls, settings: object, *,
                      provider: SpeechProvider | None = None,
                      player: AudioPlayer | None = None,
                      transport=None) -> "SpeechSynthesizer":
        """Build a synthesizer from :class:`peeko.settings.Settings`.

        The speech key is the same ``PEEKO_AI_API_KEY`` the chat uses (one key
        to configure, as long as your provider serves both); the engine,
        model, voice, speed and base URL come from
        ``PEEKO_TTS_ENGINE``, ``PEEKO_TTS_MODEL``, ``PEEKO_TTS_VOICE``,
        ``PEEKO_TTS_SPEED`` and ``PEEKO_TTS_BASE_URL`` — the latter falling
        back to ``PEEKO_AI_BASE_URL`` so an OpenAI-compatible gateway only has
        to be configured once. Playback level comes from
        ``PEEKO_TTS_VOLUME``.

        A bad engine name never raises here: it is recorded and reported
        honestly by :meth:`configuration_problem` when the user asks Peeko to
        speak.
        """
        engine_problem = ""
        built: SpeechProvider | None = provider
        if built is None:
            try:
                built = build_speaker(
                    str(getattr(settings, "tts_engine", "") or ""),
                    api_key=str(getattr(settings, "ai_api_key", "") or ""),
                    model=str(getattr(settings, "tts_model", "") or ""),
                    voice=str(getattr(settings, "tts_voice", "") or ""),
                    speed=float(
                        getattr(settings, "tts_speed", DEFAULT_SPEED)
                        or DEFAULT_SPEED
                    ),
                    base_url=str(
                        getattr(settings, "tts_base_url", "")
                        or getattr(settings, "ai_base_url", "")
                        or ""
                    ),
                    timeout_s=float(
                        getattr(settings, "tts_timeout_s",
                                DEFAULT_TIMEOUT_S)
                        or DEFAULT_TIMEOUT_S
                    ),
                    transport=transport,
                )
            except VoiceError as exc:
                LOG.warning("Speech engine unusable: %s", exc.message)
                engine_problem = exc.message
        return cls(
            provider=built,
            player=player,
            enabled=bool(getattr(settings, "tts_enabled", True)),
            # Note: not ``value or default`` — a volume of 0.0 means
            # "silent", which is a legitimate setting, so only a missing or
            # unreadable value falls back to the default.
            volume=_clamp_volume(
                getattr(settings, "tts_volume", DEFAULT_VOLUME),
                default=DEFAULT_VOLUME,
            ),
            engine_problem=engine_problem,
        )

    # ------------------------------------------------------------------ #
    # Configuration state (honest, never leaking the key)
    # ------------------------------------------------------------------ #
    @property
    def provider(self) -> SpeechProvider | None:
        """The speech service (``None`` when unusable)."""
        return self._provider

    @property
    def player(self) -> AudioPlayer:
        """The audio player this synthesizer speaks through."""
        return self._player

    @property
    def engine(self) -> str:
        """The engine name in use (``"none"`` when nothing can be used)."""
        return getattr(self._provider, "name", "") or "none"

    @property
    def model(self) -> str:
        """The speech model in use (``""`` when nothing can be used)."""
        return getattr(self._provider, "model", "") or ""

    @property
    def voice(self) -> str:
        """The voice in use (``""`` when nothing can be used)."""
        return getattr(self._provider, "voice", "") or ""

    @property
    def speed(self) -> float:
        """The speaking rate in use (``1.0`` when nothing can be used)."""
        return float(getattr(self._provider, "speed", DEFAULT_SPEED)
                     or DEFAULT_SPEED)

    @property
    def api_key_set(self) -> bool:
        """Whether a key is configured — safe to log or display."""
        return bool(getattr(self._provider, "api_key_set", False))

    def configuration_problem(self) -> str:
        """What stops Peeko from speaking (``""`` when fine)."""
        if not self.enabled:
            return DISABLED_TEXT
        if self._engine_problem:
            return self._engine_problem
        if self._provider is None:
            return NO_PROVIDER_TEXT
        return self._provider.configuration_problem()

    def audio_problem(self) -> str:
        """What stops Peeko from playing audio (``""`` when the player works)."""
        try:
            return self._player.availability()
        except Exception as exc:  # noqa: BLE001 - a duck-typed player
            LOG.debug("Audio player availability check failed", exc_info=True)
            return (
                "Peeko cannot speak: the audio output could not be checked "
                f"({_reason_text(exc)})."
            )

    def availability_problem(self) -> str:
        """Everything that would stop Peeko from speaking, joined for display.

        Both halves are checked **before** the speech service is called: with
        no key configured Peeko never sends your reply anywhere.
        """
        parts = [p for p in (self.configuration_problem(),
                             self.audio_problem()) if p]
        return " ".join(parts)

    def is_available(self) -> bool:
        """Could Peeko speak right now?"""
        return self.availability_problem() == ""

    def describe(self) -> str:
        """One safe diagnostic line (never contains the key)."""
        return (
            f"engine={self.engine} model={self.model or '<unset>'} "
            f"voice={self.voice or '<unset>'} speed={self.speed:g} "
            f"volume={self.volume:g} output={self._player.name} "
            f"api_key={'<set>' if self.api_key_set else '<unset>'} "
            f"available={self.is_available()}"
        )

    def __repr__(self) -> str:  # never leak the key in repr
        return (
            f"SpeechSynthesizer(engine={self.engine!r}, "
            f"model={self.model!r}, voice={self.voice!r}, "
            f"player={self._player.name!r}, enabled={self.enabled}, "
            f"api_key={'<set>' if self.api_key_set else '<unset>'})"
        )

    # ------------------------------------------------------------------ #
    # Speaking
    # ------------------------------------------------------------------ #
    def speak(self, text: str, *,
              should_stop: Callable[[], bool] | None = None
              ) -> SpeechClip | None:
        """Synthesize ``text`` and play it out loud.

        Blocking — run it off the UI thread.

        :param text: what Peeko should say. Empty/whitespace means "nothing
            to say": no request is made and ``None`` is returned.
        :param should_stop: polled between audio blocks; when it returns True
            the playback stops and the result is ``None``.
        :returns: the audio that was played, or ``None`` when there is
            nothing to report (nothing to say, no audio came back, or the
            playback was cancelled).
        :raises ~peeko.voice.errors.VoiceConfigError: voice switched off,
            no key, or an unsupported engine.
        :raises ~peeko.voice.errors.VoiceUnavailableError: no audio library or
            no output device.
        :raises ~peeko.voice.errors.VoiceError: a service or playback failure.
        """
        spoken = (text or "").strip()
        if not spoken:
            LOG.debug("Nothing to speak — no request and no audio device.")
            return None

        problem = self.configuration_problem()
        if problem:
            raise VoiceConfigError(problem)
        problem = self.audio_problem()
        if problem:
            raise VoiceUnavailableError(problem)
        if self._provider is None:  # pragma: no cover - covered above
            raise VoiceConfigError(self.configuration_problem())

        self._cancel = False
        clip = self._provider.synthesize(spoken)
        if clip is None or clip.is_empty():
            LOG.info("The speech service returned no audio — nothing played.")
            return None
        if self._stopping(should_stop):
            LOG.info("Speech cancelled before playback started.")
            return None

        played = self._player.play(
            clip, volume=self.volume, should_stop=self._stop_flag(should_stop),
        )
        if not played:
            LOG.info("Speech playback cancelled — nothing was kept.")
            return None
        LOG.info(
            "Peeko spoke %d character(s) as %d byte(s) of %s audio.",
            clip.characters, clip.size_bytes, clip.response_format,
        )
        return clip

    def stop(self) -> None:
        """Stop the playback in progress (safe to call from any thread).

        Called by the worker when the user clicks Stop; :meth:`speak` then
        returns ``None`` instead of reporting a finished utterance.
        """
        self._cancel = True
        stop = getattr(self._player, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:  # noqa: BLE001 - stopping is best-effort
                LOG.debug("Could not stop the audio player", exc_info=True)

    def _stop_flag(self, should_stop: Callable[[], bool] | None
                   ) -> Callable[[], bool]:
        """A stop callback that honours both sides' requests."""
        def flag() -> bool:
            return self._stopping(should_stop)
        return flag

    def _stopping(self, should_stop: Callable[[], bool] | None) -> bool:
        """Whether this synthesizer or the caller asked for a stop."""
        if self._cancel:
            return True
        if should_stop is None:
            return False
        try:
            return bool(should_stop())
        except Exception:  # noqa: BLE001 - a caller callback is never fatal
            LOG.debug("Stop callback failed", exc_info=True)
            return False

    def __enter__(self) -> "SpeechSynthesizer":  # pragma: no cover - symmetry
        return self

    def __exit__(self, *_exc) -> None:  # pragma: no cover - symmetry
        self.close()

    def close(self) -> None:
        """Release the audio player (safe to call repeatedly)."""
        try:
            self._player.close()
        except Exception:  # noqa: BLE001 - closing is best-effort
            LOG.debug("Ignoring an error while closing the audio player",
                      exc_info=True)


def _clamp_volume(volume: object, default: float = DEFAULT_VOLUME) -> float:
    """A playback level inside ``0.0 .. 1.0`` (the default for anything unusable).

    ``0.0`` is a meaningful value ("silent") and is kept as-is; only a
    missing or unreadable setting falls back to ``default``.
    """
    if volume is None:
        return float(default)
    try:
        level = float(volume)
    except (TypeError, ValueError):
        return float(default)
    return max(0.0, min(1.0, level))


def _reason_text(exc: object) -> str:
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text[:200]


def build_synthesizer(settings: object, **kwargs) -> SpeechSynthesizer:
    """Shorthand for :meth:`SpeechSynthesizer.from_settings`."""
    return SpeechSynthesizer.from_settings(settings, **kwargs)


__all__ = [
    "DISABLED_TEXT",
    "NO_PROVIDER_TEXT",
    "SpeechSynthesizer",
    "build_synthesizer",
]
