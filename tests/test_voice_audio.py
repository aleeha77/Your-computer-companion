"""Microphone capture (Stage 4) — fakes only, on a machine with no microphone.

The audio half of voice input is the only code in Peeko that touches audio
hardware, and it is built so nothing about it is a hard dependency. These
tests run the *real* capture path (``capture_clip``) against scripted audio
sources, and the *real* ``sounddevice`` source against an injected fake
library — so no test ever opens a device and the suite still covers:

* opening, reading, joining and closing a capture, and the hard cap on how
  long a forgotten open microphone can run;
* cancellation within one chunk, and a level callback that can never break a
  capture;
* every honest failure: no microphone, permission refused, a missing audio
  library, a device that will not open or start, and a read that fails;
* the lazy import — importing :mod:`peeko.voice` does not import an audio
  library, and this machine does not even have ``sounddevice`` installed;
* privacy: nothing in the capture path writes audio to disk.
"""

from __future__ import annotations

import builtins
import io
import sys
import wave

import pytest

from peeko.voice.audio import (
    DEFAULT_MAX_SECONDS,
    DEFAULT_SAMPLE_RATE,
    MISSING_LIBRARY_TEXT,
    NO_MICROPHONE_TEXT,
    SAMPLE_WIDTH_BYTES,
    AudioClip,
    SoundDeviceAudioSource,
    UnavailableAudioSource,
    capture_clip,
    default_audio_source,
)
from peeko.voice.errors import (
    VoiceCaptureError,
    VoicePermissionError,
    VoiceUnavailableError,
)
from tests.conftest import (
    FAKE_SAMPLE_RATE,
    FakeAudioSource,
    FakeSoundDeviceModule,
    pcm_samples,
)


def chunk(frames: int = 1_024, *, amplitude: int = 1_200) -> bytes:
    return pcm_samples(frames, amplitude=amplitude)


# --------------------------------------------------------------------------- #
# The clip itself
# --------------------------------------------------------------------------- #
def test_a_clip_knows_its_length_and_is_bytes_only_in_memory():
    clip = AudioClip(chunk(1_600), sample_rate=16_000, channels=1)
    assert clip.frame_width == SAMPLE_WIDTH_BYTES
    assert clip.frames == 1_600
    assert clip.duration_s == pytest.approx(0.1)
    assert clip.is_empty() is False
    assert isinstance(clip.samples, bytes)


def test_an_empty_clip_is_empty_not_an_error():
    clip = AudioClip()
    assert clip.is_empty() is True
    assert clip.frames == 0
    assert clip.duration_s == 0.0
    assert clip.rms_level() == 0.0


def test_loudness_tells_silence_from_speech():
    assert AudioClip(pcm_samples(100, amplitude=0)).rms_level() == 0.0
    loud = AudioClip(pcm_samples(100, amplitude=16_000)).rms_level()
    assert 0.4 < loud <= 1.0


def test_a_clip_renders_itself_as_a_real_wav_payload():
    clip = AudioClip(chunk(320), sample_rate=16_000, channels=1)
    payload = clip.to_wav_bytes()
    assert payload[:4] == b"RIFF"
    assert payload[8:12] == b"WAVE"
    with wave.open(io.BytesIO(payload), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == SAMPLE_WIDTH_BYTES
        assert handle.getframerate() == 16_000
        assert handle.getnframes() == 320
        assert handle.readframes(320) == clip.samples


# --------------------------------------------------------------------------- #
# The capture loop
# --------------------------------------------------------------------------- #
def test_capture_joins_every_chunk_into_one_clip():
    chunks = [chunk(160) for _ in range(3)]
    source = FakeAudioSource(chunks)

    clip = capture_clip(source, max_duration_s=5.0)

    assert clip is not None
    assert clip.samples == b"".join(chunks)
    assert clip.frames == 480
    assert clip.sample_rate == FAKE_SAMPLE_RATE
    assert clip.channels == 1
    assert source.opened is False          # it was closed again
    assert source.closed == 1


def test_a_microphone_that_runs_dry_ends_the_capture():
    source = FakeAudioSource([])           # every read returns b""
    clip = capture_clip(source, max_duration_s=5.0)
    assert clip is not None and clip.is_empty() is True
    assert source.reads == 1               # it stopped at the first empty read


def test_capture_stops_at_the_configured_max_duration():
    """A forgotten open microphone can never run forever."""
    source = FakeAudioSource([chunk(1_024) for _ in range(50)])
    # Each 1024-frame chunk is 0.064 s of audio (1024 frames at 16 kHz).
    clip = capture_clip(source, max_duration_s=0.1)

    assert clip is not None
    assert source.reads == 2               # 2 x 0.064 s >= 0.1 s
    assert clip.frames == 2 * 1_024
    assert clip.duration_s == pytest.approx(0.128, abs=0.001)


def test_a_zero_or_missing_duration_limit_falls_back_to_the_default():
    source = FakeAudioSource([chunk(160)])
    clip = capture_clip(source, max_duration_s=0)
    assert clip is not None and clip.frames == 160
    assert DEFAULT_MAX_SECONDS == 30.0     # the documented cap


def test_capture_can_be_cancelled_between_chunks():
    """Stopping is honoured within one chunk, so nothing ever hangs."""
    source = FakeAudioSource([chunk(160) for _ in range(10)])
    clip = capture_clip(
        source, max_duration_s=5.0, should_stop=lambda: source.reads >= 2
    )
    assert clip is None
    assert source.reads == 2
    assert source.closed == 1              # the microphone is released


def test_cancelling_before_any_audio_keeps_the_microphone_closed():
    source = FakeAudioSource([chunk(160)])
    assert capture_clip(source, should_stop=lambda: True) is None
    assert source.reads == 0
    assert source.closed == 1


def test_levels_are_reported_for_every_chunk():
    source = FakeAudioSource([
        pcm_samples(160, amplitude=0), pcm_samples(160, amplitude=8_000),
    ])
    levels: list[float] = []
    capture_clip(source, max_duration_s=5.0, on_level=levels.append)

    assert len(levels) == 2
    assert levels[0] == 0.0
    assert levels[1] > 0.0


def test_a_level_callback_that_blows_up_never_breaks_the_capture():
    source = FakeAudioSource([chunk(160)])

    def boom(_level: float) -> None:
        raise RuntimeError("the level meter is broken")

    clip = capture_clip(source, max_duration_s=5.0, on_level=boom)
    assert clip is not None and clip.frames == 160


def test_the_microphone_is_released_even_when_reading_fails():
    source = FakeAudioSource([chunk(160)], read_error=VoiceCaptureError(
        "Voice input failed while reading from the microphone."
    ))
    with pytest.raises(VoiceCaptureError):
        capture_clip(source, max_duration_s=5.0)
    assert source.closed == 1


def test_a_source_that_will_not_open_stops_the_capture_immediately():
    source = FakeAudioSource(
        [], open_error=VoiceUnavailableError(NO_MICROPHONE_TEXT)
    )
    with pytest.raises(VoiceUnavailableError, match="no microphone"):
        capture_clip(source, max_duration_s=5.0)
    assert source.reads == 0
    assert source.closed == 0              # nothing was opened, so nothing to close


def test_the_capture_loop_never_sleeps_and_never_waits():
    """The loop reads until the source runs dry — no timers involved."""
    import time

    source = FakeAudioSource([chunk(160)])
    started = time.monotonic()
    capture_clip(source, max_duration_s=30.0)
    assert time.monotonic() - started < 0.5


# --------------------------------------------------------------------------- #
# An honest stand-in for a machine with no microphone
# --------------------------------------------------------------------------- #
def test_an_unavailable_source_says_why():
    source = UnavailableAudioSource()
    assert source.availability() == NO_MICROPHONE_TEXT
    assert "no microphone" in NO_MICROPHONE_TEXT
    with pytest.raises(VoiceUnavailableError) as excinfo:
        source.open()
    assert excinfo.value.message == NO_MICROPHONE_TEXT
    source.close()                          # never raises


def test_an_unavailable_source_can_carry_a_permission_error():
    source = UnavailableAudioSource(
        "Voice input unavailable: this computer refused microphone access.",
        error=VoicePermissionError,
    )
    with pytest.raises(VoicePermissionError):
        source.open()
    # A refused permission is still a kind of "cannot run here".
    assert issubclass(VoicePermissionError, VoiceUnavailableError)


def test_capture_from_an_unavailable_source_propagates_the_reason():
    source = UnavailableAudioSource("no microphone was found")
    with pytest.raises(VoiceUnavailableError, match="no microphone was found"):
        capture_clip(source)


# --------------------------------------------------------------------------- #
# The real source — through an injected fake sounddevice library
# --------------------------------------------------------------------------- #
def test_the_audio_library_is_not_imported_before_it_is_needed():
    """``import peeko.voice`` must not require an audio library at all."""
    import peeko.voice  # noqa: F401 - importing is the test

    assert "sounddevice" not in sys.modules, (
        "peeko.voice imported sounddevice at import time — the dependency "
        "must stay lazy so the app starts on a machine without it"
    )


def test_the_loaders_are_only_called_on_demand():
    calls: list[bool] = []

    def loader():
        calls.append(True)
        return FakeSoundDeviceModule()

    source = SoundDeviceAudioSource(loader=loader)
    assert calls == []                     # building it imports nothing
    assert "SoundDeviceAudioSource" in repr(source)
    assert calls == []
    assert source.availability() == ""     # …asking for it does
    assert calls == [True]


def test_a_missing_audio_library_is_an_honest_reason_not_a_crash():
    def loader():
        raise ImportError("No module named 'sounddevice'")

    source = SoundDeviceAudioSource(loader=loader)
    reason = source.availability()
    assert reason.startswith(MISSING_LIBRARY_TEXT.split(" (")[0])
    assert "sounddevice" in reason
    assert "peeko[voice]" in reason
    with pytest.raises(VoiceUnavailableError):
        source.open()


def test_this_test_machine_really_has_no_sounddevice():
    """The suite must pass without the optional extra — say so out loud."""
    with pytest.raises(ImportError):
        import sounddevice  # noqa: F401

    assert default_audio_source().name == "sounddevice"


def test_the_real_source_captures_through_the_library():
    module = FakeSoundDeviceModule(chunks=[chunk(1_024)])
    source = SoundDeviceAudioSource(module=module, blocksize=1_024)

    assert source.availability() == ""
    assert module.device_queries == []      # nothing touched just by asking

    source.open()
    stream = module.instances[0]
    assert module.device_queries == [{"kind": "input"}]
    assert stream.started is True
    assert stream.kwargs["dtype"] == "int16"
    assert stream.kwargs["samplerate"] == DEFAULT_SAMPLE_RATE
    assert stream.kwargs["channels"] == 1

    assert source.read() == chunk(1_024)

    source.close()
    assert stream.stopped == 1 and stream.closed == 1
    assert source.availability() == ""
    assert module.instances[0] is stream


def test_the_real_source_runs_the_whole_capture_loop():
    module = FakeSoundDeviceModule(
        chunks=[chunk(1_024) for _ in range(3)]
    )
    source = SoundDeviceAudioSource(module=module, blocksize=1_024)
    clip = capture_clip(source, max_duration_s=0.1)

    assert clip is not None
    assert clip.frames == 2 * 1_024        # 2 x 0.064 s, then the cap
    assert module.instances[0].stopped == 1


def test_opening_twice_reuses_the_same_stream():
    module = FakeSoundDeviceModule()
    source = SoundDeviceAudioSource(module=module)
    source.open()
    source.open()
    assert len(module.instances) == 1


def test_an_overflowed_block_is_still_used():
    module = FakeSoundDeviceModule(chunks=[chunk(1_024)])
    source = SoundDeviceAudioSource(module=module)
    source.open()
    module.instances[0].overflowed = True
    assert source.read() == chunk(1_024)


def test_closing_twice_is_safe():
    module = FakeSoundDeviceModule()
    source = SoundDeviceAudioSource(module=module)
    source.open()
    source.close()
    source.close()
    assert module.instances[0].stopped == 1


def test_no_input_device_is_reported_as_no_microphone():
    module = FakeSoundDeviceModule(
        query_error=RuntimeError("No default input device available")
    )
    source = SoundDeviceAudioSource(module=module)
    with pytest.raises(VoiceUnavailableError) as excinfo:
        source.open()
    assert NO_MICROPHONE_TEXT in excinfo.value.message
    assert "No default input device available" in excinfo.value.message


def test_a_refused_permission_is_classified_as_a_permission_error():
    module = FakeSoundDeviceModule(
        open_error=RuntimeError("Permission denied (microphone)")
    )
    with pytest.raises(VoicePermissionError) as excinfo:
        SoundDeviceAudioSource(module=module).open()
    message = excinfo.value.message
    assert "refused microphone access" in message
    assert "Allow microphone access" in message


def test_an_unopenable_device_is_reported_with_its_reason():
    module = FakeSoundDeviceModule(
        open_error=RuntimeError("device is busy")
    )
    with pytest.raises(VoiceUnavailableError) as excinfo:
        SoundDeviceAudioSource(module=module).open()
    assert "could not be opened" in excinfo.value.message
    assert "device is busy" in excinfo.value.message


def test_a_stream_that_will_not_start_is_a_capture_error():
    module = FakeSoundDeviceModule(start_error=RuntimeError("device busy"))
    source = SoundDeviceAudioSource(module=module)
    with pytest.raises(VoiceCaptureError) as excinfo:
        source.open()
    assert "could not be started" in excinfo.value.message
    # The half-open stream was released rather than leaked.
    assert module.instances[0].closed == 1


def test_reading_without_an_open_microphone_is_a_capture_error():
    source = SoundDeviceAudioSource(module=FakeSoundDeviceModule())
    with pytest.raises(VoiceCaptureError) as excinfo:
        source.read()
    assert "not open" in excinfo.value.message


def test_a_failed_read_is_a_capture_error():
    module = FakeSoundDeviceModule(read_error=RuntimeError("device lost"))
    source = SoundDeviceAudioSource(module=module)
    source.open()
    with pytest.raises(VoiceCaptureError) as excinfo:
        source.read()
    assert "while reading from the microphone" in excinfo.value.message
    assert "device lost" in excinfo.value.message


# --------------------------------------------------------------------------- #
# Privacy
# --------------------------------------------------------------------------- #
def test_captured_audio_never_touches_the_disk(tmp_path, monkeypatch):
    """The whole point of capturing in memory: no audio file, ever."""
    writes: list[str] = []
    real_open = builtins.open

    def guarded(file, mode="r", *args, **kwargs):
        text_mode = str(mode)
        if any(flag in text_mode for flag in ("w", "a", "x", "+")):
            writes.append(str(file))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded)
    source = FakeAudioSource([chunk(320)])
    clip = capture_clip(source, max_duration_s=1.0)
    assert clip is not None
    clip.to_wav_bytes()                    # encoded in memory, not on disk

    assert writes == []
    assert list(tmp_path.rglob("*")) == []
    assert not hasattr(clip, "path")
