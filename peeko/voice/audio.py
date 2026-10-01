"""Microphone capture for Peeko's voice input (Stage 4).

This module is the *only* place that touches audio hardware, and it is built
around one rule: **Peeko has no audio hard dependency**. Nothing here imports
an audio library at import time — the real source (:class:`SoundDeviceAudioSource`)
loads ``sounddevice`` lazily, when it is actually asked to capture. If the
library is missing (a Linux box with no PortAudio, a minimal install) the app
still starts, the feature reports an honest reason, and nothing crashes.

The seam the tests use is :class:`AudioSource`: a tiny protocol
(``availability``/``open``/``read``/``close``) that the real implementation,
a hand-written fake or a deliberately unavailable stand-in all satisfy. The
test suite never opens a real microphone — this build machine has none.

Privacy

* audio lives in memory only — :func:`capture_clip` returns an
  :class:`AudioClip` and **nothing is ever written to disk** (there is no
  ``open()`` for writing anywhere in this package, and a test enforces it);
* a capture is always bounded: :data:`DEFAULT_MAX_SECONDS` caps how long a
  forgotten open microphone can run;
* the captured audio is handed to the configured speech-to-text provider and
  nowhere else (see :mod:`peeko.voice.providers`).
"""

from __future__ import annotations

import io
import logging
import wave
from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

from peeko.voice.errors import (
    VoiceCaptureError,
    VoiceError,
    VoicePermissionError,
    VoiceUnavailableError,
)

LOG = logging.getLogger("peeko.voice")

#: Format Peeko captures in: 16-bit mono PCM — the format every mainstream
#: speech-to-text endpoint accepts.
DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2  # int16

#: How many samples the real source reads per chunk (~64 ms at 16 kHz).
DEFAULT_BLOCKSIZE = 1024

#: How long a single capture may run before it stops on its own (seconds).
DEFAULT_MAX_SECONDS = 30.0

#: Text phrases used in honest failure messages.
NO_MICROPHONE_TEXT = (
    "Voice input unavailable: no microphone was found on this computer."
)
MISSING_LIBRARY_TEXT = (
    "Voice input unavailable: the 'sounddevice' audio library is not "
    "installed (install it with: pip install 'peeko[voice]')."
)


@dataclass(frozen=True)
class AudioClip:
    """A chunk of captured audio, held in memory.

    :param samples: raw little-endian signed 16-bit PCM samples.
    :param sample_rate: samples per second (Hz).
    :param channels: number of interleaved channels (Peeko captures mono).

    The clip renders itself as a WAV payload with :meth:`to_wav_bytes`, so the
    speech-to-text provider can upload it without anything ever touching the
    filesystem.
    """

    samples: bytes = b""
    sample_rate: int = DEFAULT_SAMPLE_RATE
    channels: int = DEFAULT_CHANNELS

    @property
    def frame_width(self) -> int:
        """Bytes per frame (all channels of one sample instant)."""
        return SAMPLE_WIDTH_BYTES * max(1, int(self.channels))

    @property
    def frames(self) -> int:
        """Number of sample frames captured."""
        return len(self.samples) // self.frame_width

    @property
    def duration_s(self) -> float:
        """Length of the clip in seconds (``0.0`` for an empty clip)."""
        if self.frames <= 0 or self.sample_rate <= 0:
            return 0.0
        return self.frames / float(self.sample_rate)

    def is_empty(self) -> bool:
        """True when no audio at all was captured."""
        return self.frames <= 0

    def rms_level(self) -> float:
        """Loudness of the clip as ``0.0 .. 1.0`` (0.0 for silence/empty).

        Used for a future level meter, and by the tests to prove that the
        samples really travel from the source to the provider unchanged.
        This is cheap arithmetic on the bytes — it does not need numpy.
        """
        if self.is_empty():
            return 0.0
        usable = self.frames * self.frame_width
        raw = self.samples[:usable]
        total = 0
        count = 0
        for index in range(0, len(raw), SAMPLE_WIDTH_BYTES):
            value = int.from_bytes(
                raw[index:index + SAMPLE_WIDTH_BYTES], "little", signed=True
            )
            total += value * value
            count += 1
        if not count:
            return 0.0
        mean_square = total / count
        return min(1.0, (mean_square ** 0.5) / 32768.0)

    def to_wav_bytes(self) -> bytes:
        """The clip as an in-memory RIFF/WAVE payload (never a file)."""
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(max(1, int(self.channels)))
            handle.setsampwidth(SAMPLE_WIDTH_BYTES)
            handle.setframerate(max(1, int(self.sample_rate)))
            handle.writeframes(self.samples[: self.frames * self.frame_width])
        return buffer.getvalue()


@runtime_checkable
class AudioSource(Protocol):
    """Anything Peeko can capture audio from.

    The real implementation wraps ``sounddevice``; the tests use fakes and a
    deliberately unavailable stand-in, so no test ever needs a microphone.
    Implementations must raise :class:`~peeko.voice.errors.VoiceError`
    subclasses (never let a raw library exception escape) and must be safe to
    ``close()`` more than once.
    """

    name: str
    sample_rate: int
    channels: int

    def availability(self) -> str:
        """``""`` when this source can capture, else an honest reason."""
        ...

    def open(self) -> None:
        """Open the stream; raise a :class:`VoiceError` when that fails."""
        ...

    def read(self) -> bytes:
        """Read one block of int16 PCM samples (``b""`` at end of stream)."""
        ...

    def close(self) -> None:
        """Release the stream (safe to call repeatedly)."""
        ...


class UnavailableAudioSource:
    """An honest stand-in for a machine with no usable microphone.

    Used when nothing else can be built (and by tests). It never opens
    anything: :meth:`open` raises with the reason it was created with.
    """

    name = "unavailable"

    def __init__(self, reason: str = NO_MICROPHONE_TEXT,
                 *, sample_rate: int = DEFAULT_SAMPLE_RATE,
                 channels: int = DEFAULT_CHANNELS,
                 error: type[VoiceError] = VoiceUnavailableError) -> None:
        self.reason = reason or NO_MICROPHONE_TEXT
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self._error = error

    def availability(self) -> str:
        """Always the reason this source exists."""
        return self.reason

    def open(self) -> None:
        """Always fails — honestly, with the stored reason."""
        raise self._error(self.reason)

    def read(self) -> bytes:  # pragma: no cover - open() never succeeds
        raise self._error(self.reason)

    def close(self) -> None:
        """Nothing to release."""

    def __repr__(self) -> str:
        return f"UnavailableAudioSource(reason={self.reason!r})"


#: Substrings that mean "the OS refused microphone access" rather than
#: "there is no microphone / it is busy". Checked case-insensitively.
_PERMISSION_HINTS = (
    "permission", "not allowed", "denied", "unauthorized", "access",
)


def _reason_text(exc: object) -> str:
    """A short, single-line description of a library exception."""
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text[:200]


def _looks_like_permission(exc: object) -> bool:
    text = str(exc).lower()
    return any(hint in text for hint in _PERMISSION_HINTS)


class SoundDeviceAudioSource:
    """Real microphone capture through the ``sounddevice`` library.

    ``sounddevice`` is imported **lazily** (never at module import time) and
    is an *optional* dependency: without it, :meth:`availability` returns an
    honest sentence and :meth:`open` raises
    :class:`~peeko.voice.errors.VoiceUnavailableError` — the rest of Peeko is
    unaffected.

    :param sample_rate: capture rate in Hz (16 kHz is enough for speech).
    :param channels: number of channels (1 = mono).
    :param blocksize: samples per read.
    :param device: optional device name/index from ``sounddevice``.
    :param module: a ready-made module object — the tests inject a fake
        ``sounddevice`` here so the whole capture path is exercised without
        any audio hardware.
    :param loader: callable returning the module (alternative to ``module``).
    """

    name = "sounddevice"

    def __init__(self, *, sample_rate: int = DEFAULT_SAMPLE_RATE,
                 channels: int = DEFAULT_CHANNELS,
                 blocksize: int = DEFAULT_BLOCKSIZE,
                 device: object | None = None,
                 module: object | None = None,
                 loader: Callable[[], object] | None = None) -> None:
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.blocksize = int(blocksize)
        self.device = device
        self._module = module
        self._loader = loader
        self._stream = None

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
                LOG.debug("sounddevice is unavailable", exc_info=True)
                raise VoiceUnavailableError(
                    MISSING_LIBRARY_TEXT + f" ({_reason_text(exc)})"
                ) from exc
        return self._module

    @staticmethod
    def _import_sounddevice() -> object:
        """Import ``sounddevice`` only when a capture is actually asked for."""
        import sounddevice  # noqa: PLC0415 - deliberately lazy
        return sounddevice

    def availability(self) -> str:
        """``""`` when sounddevice is importable, else the honest reason."""
        try:
            self._module_or_raise()
        except VoiceUnavailableError as exc:
            return exc.message
        return ""

    # ------------------------------------------------------------------ #
    # Stream lifecycle
    # ------------------------------------------------------------------ #
    def open(self) -> None:
        """Open and start the input stream.

        :raises ~peeko.voice.errors.VoiceUnavailableError: no library, no
            input device, or the device could not be opened.
        :raises ~peeko.voice.errors.VoicePermissionError: the OS refused
            microphone access.
        :raises ~peeko.voice.errors.VoiceCaptureError: the stream failed to
            start.
        """
        if self._stream is not None:
            return
        module = self._module_or_raise()
        self._check_input_device(module)
        try:
            stream = module.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                blocksize=self.blocksize,
                device=self.device,
            )
        except Exception as exc:  # noqa: BLE001 - library exceptions vary
            LOG.warning("Could not open the microphone: %s", _reason_text(exc))
            raise self._open_error(exc) from exc
        try:
            stream.start()
        except Exception as exc:  # noqa: BLE001
            self._safe_close(stream)
            LOG.warning("Could not start the microphone stream: %s",
                        _reason_text(exc))
            raise VoiceCaptureError(
                "Voice input failed: the microphone stream could not be "
                f"started ({_reason_text(exc)})."
            ) from exc
        self._stream = stream
        LOG.info(
            "Microphone open: %d Hz, %d channel(s), block %d.",
            self.sample_rate, self.channels, self.blocksize,
        )

    def _check_input_device(self, module: object) -> None:
        """Fail early and clearly when the machine has no input device."""
        query = getattr(module, "query_devices", None)
        if not callable(query):
            return
        try:
            query(kind="input")
        except Exception as exc:  # noqa: BLE001 - no device / driver problem
            LOG.info("No input device available: %s", _reason_text(exc))
            raise VoiceUnavailableError(
                NO_MICROPHONE_TEXT + f" ({_reason_text(exc)})"
            ) from exc

    def _open_error(self, exc: Exception) -> VoiceError:
        """Classify an open failure as missing device vs. permission denied."""
        if _looks_like_permission(exc):
            return VoicePermissionError(
                "Voice input unavailable: this computer refused microphone "
                f"access ({_reason_text(exc)}). Allow microphone access for "
                "your terminal or for Peeko, then try again."
            )
        return VoiceUnavailableError(
            "Voice input unavailable: the microphone could not be opened "
            f"({_reason_text(exc)})."
        )

    def read(self) -> bytes:
        """Read one block of int16 PCM samples.

        :raises ~peeko.voice.errors.VoiceCaptureError: the read failed.
        """
        stream = self._stream
        if stream is None:
            raise VoiceCaptureError(
                "Voice input failed: the microphone was not open."
            )
        try:
            data, overflowed = stream.read(self.blocksize)
        except Exception as exc:  # noqa: BLE001 - library exceptions vary
            LOG.warning("Microphone read failed: %s", _reason_text(exc))
            raise VoiceCaptureError(
                "Voice input failed while reading from the microphone "
                f"({_reason_text(exc)})."
            ) from exc
        if overflowed:  # harmless, but worth a line in the log
            LOG.debug("Microphone input overflowed — a block was dropped.")
        raw = data.tobytes() if hasattr(data, "tobytes") else bytes(data)
        return raw

    def close(self) -> None:
        """Stop and release the stream (never raises)."""
        stream, self._stream = self._stream, None
        if stream is None:
            return
        self._safe_close(stream)
        LOG.info("Microphone closed.")

    @staticmethod
    def _safe_close(stream) -> None:
        for method in ("stop", "close"):
            action = getattr(stream, method, None)
            if not callable(action):
                continue
            try:
                action()
            except Exception:  # noqa: BLE001 - closing is best-effort
                LOG.debug("Ignoring an error while closing the stream",
                          exc_info=True)

    def __repr__(self) -> str:
        return (
            f"SoundDeviceAudioSource(sample_rate={self.sample_rate}, "
            f"channels={self.channels}, device={self.device!r})"
        )


def default_audio_source(**kwargs) -> AudioSource:
    """The audio source Peeko uses outside the tests."""
    return SoundDeviceAudioSource(**kwargs)


def capture_clip(source: AudioSource, *,
                 max_duration_s: float = DEFAULT_MAX_SECONDS,
                 should_stop: Callable[[], bool] | None = None,
                 on_level: Callable[[float], None] | None = None
                 ) -> AudioClip | None:
    """Capture audio from ``source`` until it is stopped or time runs out.

    Blocking by design: the caller runs this off the UI thread (see
    :mod:`peeko.voice.worker`). Never sleeps in a way that ignores
    cancellation — each ``read()`` is a short block, so a stop request is
    honoured within one chunk (~64 ms at the default settings).

    :param source: the :class:`AudioSource` to capture from.
    :param max_duration_s: hard cap on the capture length, in seconds. A
        microphone left open can never run forever.
    :param should_stop: optional callable polled between chunks; when it
        returns True the capture stops and ``None`` is returned (cancelled).
    :param on_level: optional callable receiving the loudness (``0.0 .. 1.0``)
        of every chunk. Best-effort: an exception in it is logged and ignored,
        so a UI callback can never break a capture.
    :returns: the captured :class:`AudioClip`, or ``None`` when the capture
        was cancelled before any audio was kept.
    """
    limit = float(max_duration_s) if max_duration_s else DEFAULT_MAX_SECONDS
    if limit <= 0:
        limit = DEFAULT_MAX_SECONDS

    source.open()
    frames: list[bytes] = []
    elapsed_s = 0.0
    try:
        while elapsed_s < limit:
            if should_stop is not None and should_stop():
                LOG.info("Voice capture cancelled after %.1fs.", elapsed_s)
                return None
            chunk = source.read()
            if not chunk:
                LOG.debug("Microphone stream ended after %.1fs.", elapsed_s)
                break
            frames.append(chunk)
            elapsed_s += (
                len(chunk) / max(1, source.channels) / SAMPLE_WIDTH_BYTES
                / max(1, source.sample_rate)
            )
            if on_level is not None:
                _report_level(on_level, chunk, source)
    finally:
        source.close()

    clip = AudioClip(
        b"".join(frames),
        sample_rate=source.sample_rate,
        channels=source.channels,
    )
    LOG.info("Voice capture finished: %.2fs of audio.", clip.duration_s)
    return clip


def _report_level(on_level: Callable[[float], None], chunk: bytes,
                  source: AudioSource) -> None:
    """Best-effort loudness reporting; never let the callback break capture."""
    try:
        on_level(
            AudioClip(chunk, sample_rate=source.sample_rate,
                      channels=source.channels).rms_level()
        )
    except Exception:  # noqa: BLE001 - a UI callback is never fatal
        LOG.debug("Level callback failed", exc_info=True)


__all__ = [
    "AudioClip",
    "AudioSource",
    "DEFAULT_BLOCKSIZE",
    "DEFAULT_CHANNELS",
    "DEFAULT_MAX_SECONDS",
    "DEFAULT_SAMPLE_RATE",
    "MISSING_LIBRARY_TEXT",
    "NO_MICROPHONE_TEXT",
    "SAMPLE_WIDTH_BYTES",
    "SoundDeviceAudioSource",
    "UnavailableAudioSource",
    "capture_clip",
    "default_audio_source",
]
