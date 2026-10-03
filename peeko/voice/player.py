"""Playback of synthesised speech: the seam behind Peeko's voice (Stage 5).

This is the mirror of :mod:`peeko.voice.audio`: that module gets audio *in*
from a microphone, this one gets audio *out* to the speakers. Both follow the
same rule — **Peeko has no audio hard dependency**. Nothing here imports an
audio library at import time; the real player
(:class:`SoundDevicePlayer`) loads ``sounddevice`` lazily, when it is actually
asked to play. If the library or the device is missing, the app still starts,
the feature reports an honest reason, and nothing crashes.

The seam the tests use is :class:`AudioPlayer`: a tiny protocol
(``availability``/``play``/``stop``/``close``) that the real implementation, a
hand-written fake or a deliberately unavailable stand-in all satisfy. The test
suite never opens a real speaker — this build machine has none.

Formats

Peeko decodes **WAV** with the standard library alone (:mod:`wave`), which is
why the speech provider asks for WAV by default (see
:mod:`peeko.voice.output_providers`). Anything else — MP3, Opus, … — would
need an extra decoder dependency just to be heard, so it is refused with a
plain-language reason instead of being played as noise.

Privacy

* the audio lives in memory only — :class:`~peeko.voice.output_providers.SpeechClip`
  is handed straight to the sound card and **nothing is ever written to disk**
  (there is no ``open()`` for writing anywhere in this package, and a test
  enforces it);
* a playback is always bounded and cancellable: :meth:`SoundDevicePlayer.play`
  checks its stop flag between chunks (~0.1 s of audio), so ``Stop`` lands
  immediately;
* volume is applied in memory (:func:`apply_volume`) — the level Peeko is
  configured with never changes the audio a service sent.
"""

from __future__ import annotations

import io
import logging
import sys
import threading
import wave
from array import array
from typing import Callable, Protocol, runtime_checkable

from peeko.voice.errors import (
    TTSPlaybackError,
    VoiceError,
    VoiceUnavailableError,
)

LOG = logging.getLogger("peeko.voice")

#: How many bytes of PCM the real player writes per call (~0.1 s at 16 kHz
#: mono 16-bit). Small enough that a stop request is honoured almost at once,
#: large enough that the sound card is not fed one sample at a time.
DEFAULT_BLOCK_BYTES = 3_200

#: Sample width of the PCM Peeko plays (16-bit, like everything else here).
SAMPLE_WIDTH_BYTES = 2

#: Text phrases used in honest failure messages.
NO_SPEAKER_TEXT = (
    "Peeko cannot speak: no audio output device was found on this computer."
)
MISSING_LIBRARY_TEXT = (
    "Peeko cannot speak: the 'sounddevice' audio library is not installed "
    "(install it with: pip install 'peeko[voice]')."
)


class DecodedAudio:
    """Raw PCM ready to hand to the sound card (never a file).

    :param samples: unencoded 16-bit little-endian PCM.
    :param sample_rate: samples per second (Hz).
    :param channels: number of interleaved channels.
    """

    __slots__ = ("samples", "sample_rate", "channels")

    def __init__(self, samples: bytes, sample_rate: int,
                 channels: int) -> None:
        self.samples = samples
        self.sample_rate = int(sample_rate)
        self.channels = max(1, int(channels))

    @property
    def frame_width(self) -> int:
        """Bytes per frame (all channels of one sample instant)."""
        return SAMPLE_WIDTH_BYTES * self.channels

    @property
    def duration_s(self) -> float:
        """Length of the audio in seconds (``0.0`` for an empty payload)."""
        return self.seconds_for(len(self.samples))

    def seconds_for(self, byte_count: int) -> float:
        """How many seconds ``byte_count`` bytes of this audio are worth."""
        if self.sample_rate <= 0:
            return 0.0
        frames = max(0, int(byte_count)) // self.frame_width
        return frames / float(self.sample_rate)

    def __repr__(self) -> str:
        return (
            f"DecodedAudio(bytes={len(self.samples)}, "
            f"rate={self.sample_rate}, channels={self.channels}, "
            f"duration={self.duration_s:.2f}s)"
        )


def decode_wav(payload: bytes, *, content_type: str = "") -> DecodedAudio:
    """Decode a WAV payload into raw PCM, in memory.

    :raises ~peeko.voice.errors.TTSPlaybackError: the payload is not a WAV
        file Peeko can read (a different container, a truncated body, …).
        The message names the problem and what to ask the service for.
    """
    if not payload:
        raise TTSPlaybackError(
            "Peeko cannot play this audio: the speech service sent no data."
        )
    try:
        with wave.open(io.BytesIO(payload), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            samples = handle.readframes(handle.getnframes())
    except wave.Error as exc:
        kind = f"it is not a WAV payload ({content_type})" if content_type \
            else "it is not a WAV payload"
        raise TTSPlaybackError(
            f"Peeko cannot play this audio: {kind}. Peeko plays WAV without "
            "extra packages — ask your speech service for "
            "response_format=wav."
        ) from exc
    except Exception as exc:  # noqa: BLE001 - a corrupt body varies
        raise TTSPlaybackError(
            "Peeko cannot play this audio: the payload could not be read "
            f"({_reason_text(exc)})."
        ) from exc
    if width != SAMPLE_WIDTH_BYTES:
        raise TTSPlaybackError(
            "Peeko cannot play this audio: it is "
            f"{width * 8}-bit, and Peeko plays 16-bit PCM."
        )
    if not samples:
        raise TTSPlaybackError(
            "Peeko cannot play this audio: it contains no samples."
        )
    return DecodedAudio(samples, rate, channels)


def apply_volume(samples: bytes, volume: float) -> bytes:
    """Scale 16-bit PCM ``samples`` by ``volume`` (``1.0`` = untouched).

    Pure standard library (:mod:`array`), so raising or lowering the volume
    does not add a dependency. Values are clamped to the 16-bit range, so a
    loud clip at a high volume distorts rather than wrapping into noise.
    """
    if not samples:
        return b""
    try:
        level = float(volume)
    except (TypeError, ValueError):
        level = 1.0
    if level == 1.0:
        return samples
    level = max(0.0, min(1.0, level))
    usable = len(samples) - (len(samples) % SAMPLE_WIDTH_BYTES)
    values = array("h")
    values.frombytes(samples[:usable])
    if sys.byteorder == "big":  # WAV PCM is little-endian
        values.byteswap()
    for index, value in enumerate(values):
        scaled = int(round(value * level))
        values[index] = -32_768 if scaled < -32_768 else (
            32_767 if scaled > 32_767 else scaled
        )
    if sys.byteorder == "big":
        values.byteswap()
    return values.tobytes()


def iter_chunks(payload: bytes, size: int = DEFAULT_BLOCK_BYTES):
    """Yield ``payload`` in blocks of ``size`` bytes.

    The playback loop walks these, which is what makes a stop request land
    within one block instead of after a whole utterance.
    """
    step = max(1, int(size))
    for start in range(0, len(payload), step):
        yield payload[start:start + step]


@runtime_checkable
class AudioPlayer(Protocol):
    """Anything Peeko can play synthesised speech on.

    The real implementation wraps ``sounddevice``; the tests use a fake and a
    deliberately unavailable stand-in, so no test ever needs a speaker.
    Implementations must raise :class:`~peeko.voice.errors.VoiceError`
    subclasses (never let a raw library exception escape) and must be safe to
    ``close()`` more than once.
    """

    name: str

    def availability(self) -> str:
        """``""`` when this player can play, else an honest reason."""
        ...

    def play(self, clip, *, volume: float = 1.0,
             should_stop: Callable[[], bool] | None = None) -> bool:
        """Play ``clip``; return True when it reached the end.

        ``False`` means the playback was cancelled (via ``should_stop`` or
        :meth:`stop`) — never that it silently failed.
        """
        ...

    def stop(self) -> None:
        """Stop any playback in progress (safe from any thread)."""
        ...

    def close(self) -> None:
        """Release the device (safe to call repeatedly)."""
        ...


class UnavailablePlayer:
    """An honest stand-in for a machine that cannot play audio.

    Used when nothing else can be built (and by tests). It never opens
    anything: :meth:`play` raises with the reason it was created with.
    """

    name = "unavailable"

    def __init__(self, reason: str = NO_SPEAKER_TEXT, *,
                 error: type[VoiceError] = VoiceUnavailableError) -> None:
        self.reason = reason or NO_SPEAKER_TEXT
        self._error = error

    def availability(self) -> str:
        """Always the reason this player exists."""
        return self.reason

    def play(self, clip, *, volume: float = 1.0,
             should_stop: Callable[[], bool] | None = None) -> bool:
        """Always fails — honestly, with the stored reason."""
        raise self._error(self.reason)

    def stop(self) -> None:
        """Nothing to stop."""

    def close(self) -> None:
        """Nothing to release."""

    def __repr__(self) -> str:
        return f"UnavailablePlayer(reason={self.reason!r})"


def _reason_text(exc: object) -> str:
    """A short, single-line description of a library exception."""
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text[:200]


class SoundDevicePlayer:
    """Real playback through the ``sounddevice`` library.

    ``sounddevice`` is imported **lazily** (never at module import time) and
    is an *optional* dependency: without it, :meth:`availability` returns an
    honest sentence and :meth:`play` raises
    :class:`~peeko.voice.errors.VoiceUnavailableError` — the rest of Peeko is
    unaffected.

    :param block_bytes: PCM bytes written per call (small = quick to cancel).
    :param device: optional device name/index from ``sounddevice``.
    :param module: a ready-made module object — the tests inject a fake
        ``sounddevice`` here so the whole playback path is exercised without
        any audio hardware.
    :param loader: callable returning the module (alternative to ``module``).
    """

    name = "sounddevice"

    def __init__(self, *, block_bytes: int = DEFAULT_BLOCK_BYTES,
                 device: object | None = None,
                 module: object | None = None,
                 loader: Callable[[], object] | None = None) -> None:
        self.block_bytes = max(2, int(block_bytes))
        self.device = device
        self._module = module
        self._loader = loader
        self._stream = None
        self._lock = threading.Lock()
        self._cancel = threading.Event()

    # ------------------------------------------------------------------ #
    # Lazy import
    # ------------------------------------------------------------------ #
    def _module_or_raise(self) -> object:
        """The ``sounddevice`` module, imported on first use."""
        if self._module is None:
            loader = self._loader or self._import_sounddevice
            try:
                self._module = loader()
            except Exception as exc:  # noqa: BLE001 - import problems vary
                LOG.debug("sounddevice is unavailable for playback",
                          exc_info=True)
                raise VoiceUnavailableError(
                    MISSING_LIBRARY_TEXT + f" ({_reason_text(exc)})"
                ) from exc
        return self._module

    @staticmethod
    def _import_sounddevice() -> object:
        """Import ``sounddevice`` only when a playback is asked for."""
        import sounddevice  # noqa: PLC0415 - deliberately lazy
        return sounddevice

    def availability(self) -> str:
        """``""`` when playback is possible, else the honest reason."""
        try:
            module = self._module_or_raise()
        except VoiceUnavailableError as exc:
            return exc.message
        return self._device_problem(module)

    def _device_problem(self, module: object) -> str:
        """``""`` when an output device exists, else the honest reason."""
        query = getattr(module, "query_devices", None)
        if not callable(query):
            return ""
        try:
            query(kind="output")
        except Exception as exc:  # noqa: BLE001 - no device / driver problem
            LOG.info("No output device available: %s", _reason_text(exc))
            return NO_SPEAKER_TEXT + f" ({_reason_text(exc)})"
        return ""

    # ------------------------------------------------------------------ #
    # Playback
    # ------------------------------------------------------------------ #
    def play(self, clip, *, volume: float = 1.0,
             should_stop: Callable[[], bool] | None = None) -> bool:
        """Play one :class:`~peeko.voice.output_providers.SpeechClip`.

        Blocking by design: the caller runs it off the UI thread (see
        :mod:`peeko.voice.worker`).

        :param clip: the synthesised audio (WAV bytes, held in memory).
        :param volume: ``0.0 .. 1.0`` level applied in memory.
        :param should_stop: polled between blocks; when it returns True the
            playback stops and ``False`` is returned.
        :returns: ``True`` when the audio was played to the end, ``False``
            when it was cancelled or there was nothing to play.
        :raises ~peeko.voice.errors.TTSPlaybackError: the audio is not
            something Peeko can play.
        :raises ~peeko.voice.errors.VoiceUnavailableError: no library or no
            output device.
        """
        if clip is None or getattr(clip, "is_empty", lambda: True)():
            LOG.debug("Nothing to play — no audio device is opened.")
            return False

        decoded = decode_wav(
            clip.audio, content_type=getattr(clip, "content_type", "")
        )
        samples = apply_volume(decoded.samples, volume)
        module = self._module_or_raise()
        problem = self._device_problem(module)
        if problem:
            raise VoiceUnavailableError(problem)

        self._cancel.clear()
        stream = self._open_stream(module, decoded)
        finished = True
        played_bytes = 0
        try:
            for block in iter_chunks(samples, self.block_bytes):
                if self._cancelled(should_stop):
                    finished = False
                    break
                stream.write(block)
                played_bytes += len(block)
        except Exception as exc:  # noqa: BLE001 - library exceptions vary
            LOG.warning("Speech playback failed: %s", _reason_text(exc))
            raise TTSPlaybackError(
                "Peeko could not play the speech through your speakers "
                f"({_reason_text(exc)})."
            ) from exc
        finally:
            self._release(stream)
        if finished:
            LOG.info("Speech played: %.2fs of audio.", decoded.duration_s)
        else:
            LOG.info("Speech playback stopped after %.2fs.",
                     decoded.seconds_for(played_bytes))
        return finished

    def _open_stream(self, module, decoded: DecodedAudio):
        """Create and start the output stream (raising honest errors)."""
        try:
            stream = module.RawOutputStream(
                samplerate=decoded.sample_rate,
                channels=decoded.channels,
                dtype="int16",
                device=self.device,
            )
        except Exception as exc:  # noqa: BLE001 - library exceptions vary
            LOG.warning("Could not open the speakers: %s", _reason_text(exc))
            raise VoiceUnavailableError(
                NO_SPEAKER_TEXT + f" ({_reason_text(exc)})"
            ) from exc
        with self._lock:
            self._stream = stream
        try:
            stream.start()
        except Exception as exc:  # noqa: BLE001
            self._release(stream)
            raise TTSPlaybackError(
                "Peeko could not start the audio output "
                f"({_reason_text(exc)})."
            ) from exc
        return stream

    def _cancelled(self, should_stop: Callable[[], bool] | None) -> bool:
        """Whether a stop was requested from either side."""
        if self._cancel.is_set():
            return True
        if should_stop is None:
            return False
        try:
            return bool(should_stop())
        except Exception:  # noqa: BLE001 - a UI callback is never fatal
            LOG.debug("Stop callback failed", exc_info=True)
            return False

    def stop(self) -> None:
        """Ask a running playback to stop (safe to call from any thread)."""
        self._cancel.set()
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            self._abort(stream)

    def close(self) -> None:
        """Stop and release the device (never raises)."""
        self._cancel.set()
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            self._release(stream)

    def _release(self, stream) -> None:
        """Stop and close a stream (best-effort, never raises)."""
        with self._lock:
            if self._stream is stream:
                self._stream = None
        for method in ("stop", "close"):
            action = getattr(stream, method, None)
            if not callable(action):
                continue
            try:
                action()
            except Exception:  # noqa: BLE001 - closing is best-effort
                LOG.debug("Ignoring an error while closing the output stream",
                          exc_info=True)

    @staticmethod
    def _abort(stream) -> None:
        """Unblock a stream that is mid-write (best-effort)."""
        for method in ("abort", "stop"):
            action = getattr(stream, method, None)
            if not callable(action):
                continue
            try:
                action()
                return
            except Exception:  # noqa: BLE001 - stopping is best-effort
                LOG.debug("Ignoring an error while stopping the stream",
                          exc_info=True)

    def __repr__(self) -> str:
        return (
            f"SoundDevicePlayer(block_bytes={self.block_bytes}, "
            f"device={self.device!r})"
        )


def default_player(**kwargs) -> AudioPlayer:
    """The player Peeko uses outside the tests."""
    return SoundDevicePlayer(**kwargs)


__all__ = [
    "AudioPlayer",
    "DEFAULT_BLOCK_BYTES",
    "DecodedAudio",
    "MISSING_LIBRARY_TEXT",
    "NO_SPEAKER_TEXT",
    "SAMPLE_WIDTH_BYTES",
    "SoundDevicePlayer",
    "UnavailablePlayer",
    "apply_volume",
    "decode_wav",
    "default_player",
    "iter_chunks",
]
