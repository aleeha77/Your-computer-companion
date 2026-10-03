"""Tests for Stage 5 voice output: synthesis, playback, worker.

Everything here runs with fakes — a recording HTTP transport, a fake
``sounddevice`` module and a fake player — so the suite never opens a speaker,
never touches the network and never needs an API key. What is checked:

* the ``/audio/speech`` request Peeko actually builds (model, voice, input,
  speed, format), and that a bad answer is reported honestly;
* that the key is never logged or printed, even when a transport failure
  carries it in its message;
* playback: block-by-block writing, cancellation, no device, no library;
* the synthesizer's honest failure paths (off, no key, no audio, cancelled);
* the Qt worker's four outcomes, off the UI thread.
"""

from __future__ import annotations

import io
import json
import logging
import sys
import wave
from dataclasses import replace

import pytest

from peeko.settings import Settings, load_settings
from peeko.voice.errors import (
    TTSPlaybackError,
    TTSProviderError,
    TTSResponseError,
    TTSTimeoutError,
    VoiceConfigError,
    VoiceError,
    VoiceUnavailableError,
)
from peeko.voice.output import (
    DISABLED_TEXT,
    SpeechSynthesizer,
)
from peeko.voice.output_providers import (
    DEFAULT_MODEL,
    DEFAULT_VOICE,
    MockSpeechProvider,
    OpenAICompatibleSpeechProvider,
    build_speaker,
    content_type_for,
    resolve_engine,
)
from peeko.voice.player import (
    SoundDevicePlayer,
    UnavailablePlayer,
    apply_volume,
    decode_wav,
    iter_chunks,
)
from peeko.voice.worker import (
    SpeechTask,
    SpeechWorkerSignals,
    submit_speech,
)

TEST_KEY = "sk-tts-secret-key"
TEST_AUDIO = b"RIFFfake-audio-bytes"


# --------------------------------------------------------------------------- #
# Helpers: a real WAV payload and fakes for the two seams
# --------------------------------------------------------------------------- #
def wav_bytes(frames: int = 800, *, channels: int = 1,
              rate: int = 16_000, sample: int = 1_000) -> bytes:
    """A small, valid 16-bit WAV payload (built with the standard library)."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(
            b"".join(int(sample).to_bytes(2, "little", signed=True)
                     for _ in range(frames * channels))
        )
    return buffer.getvalue()


class RecordingTransport:
    """A stand-in for the HTTP transport (records every call)."""

    def __init__(self, body: bytes = TEST_AUDIO,
                 error: BaseException | None = None) -> None:
        self.body = body
        self.error = error
        self.calls: list[dict] = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({
            "url": url, "headers": dict(headers),
            "payload": json.loads(body.decode("utf-8")),
            "timeout": timeout,
        })
        if self.error is not None:
            raise self.error
        return self.body

    @property
    def count(self) -> int:
        return len(self.calls)

    @property
    def last(self) -> dict:
        return self.calls[-1]


class FakePlayer:
    """A player double: records what it was asked to play, never opens a device."""

    name = "fake"

    def __init__(self, *, availability: str = "", played: bool = True,
                 error: BaseException | None = None) -> None:
        self.reason = availability
        self.will_play = played
        self.error = error
        self.plays: list[tuple[object, float]] = []
        self.stops = 0
        self.closes = 0

    def availability(self) -> str:
        return self.reason

    def play(self, clip, *, volume: float = 1.0, should_stop=None) -> bool:
        self.plays.append((clip, volume))
        if self.error is not None:
            raise self.error
        return self.will_play

    def stop(self) -> None:
        self.stops += 1

    def close(self) -> None:
        self.closes += 1


class FakeStream:
    """A ``sounddevice`` output stream that only remembers its blocks."""

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.blocks: list[bytes] = []
        self.started = False
        self.stops = 0
        self.aborts = 0
        self.closed = False

    def start(self) -> None:
        self.started = True

    def write(self, block) -> None:
        self.blocks.append(bytes(block))

    def stop(self) -> None:
        self.stops += 1

    def abort(self) -> None:
        self.aborts += 1

    def close(self) -> None:
        self.closed = True


class FakeSoundDevice:
    """A stand-in for the ``sounddevice`` module (no hardware involved)."""

    def __init__(self, *, device_error: BaseException | None = None) -> None:
        self.device_error = device_error
        self.streams: list[FakeStream] = []

    def query_devices(self, kind=None):  # noqa: D102 - library-shaped
        if self.device_error is not None:
            raise self.device_error
        return [{"name": "fake output", "kind": kind}]

    def RawOutputStream(self, **kwargs) -> FakeStream:  # noqa: N802 - lib name
        stream = FakeStream(**kwargs)
        self.streams.append(stream)
        return stream


def tts_settings(**overrides) -> Settings:
    """Settings with TTS on and a key set, ready to be tweaked per test."""
    env = {
        "PEEKO_AI_API_KEY": TEST_KEY,
        "PEEKO_TTS_ENABLED": "1",
        "PEEKO_TTS_VOICE": "verse",
        "PEEKO_TTS_MODEL": "tts-1-hd",
        "PEEKO_TTS_BASE_URL": "https://speech.test/v1",
    }
    env.update(overrides)
    return load_settings(env)


def clip_of(payload: bytes = TEST_AUDIO):
    """A clip as the provider would return it (content type irrelevant here)."""
    return MockSpeechProvider(audio=payload).synthesize("hello")


# --------------------------------------------------------------------------- #
# The speech provider
# --------------------------------------------------------------------------- #
def test_speech_request_is_built_from_the_settings():
    transport = RecordingTransport()
    provider = build_speaker(
        "openai", api_key=TEST_KEY, model="tts-1-hd", voice="verse",
        speed=1.25, base_url="https://speech.test/v1/", transport=transport,
    )
    clip = provider.synthesize("Hello there.")

    assert transport.count == 1
    call = transport.last
    assert call["url"] == "https://speech.test/v1/audio/speech"
    assert call["payload"] == {
        "model": "tts-1-hd",
        "input": "Hello there.",
        "voice": "verse",
        "speed": 1.25,
        "response_format": "wav",
    }
    assert call["headers"]["Authorization"] == f"Bearer {TEST_KEY}"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["timeout"] > 0
    assert clip.audio == TEST_AUDIO
    assert clip.characters == len("Hello there.")
    assert clip.response_format == "wav"
    assert clip.content_type == "audio/wav"


def test_audio_is_returned_in_memory_and_never_written_to_disk(tmp_path):
    """The clip keeps only a character count — never the spoken text."""
    transport = RecordingTransport(body=wav_bytes())
    provider = build_speaker("openai", api_key=TEST_KEY,
                             transport=transport)
    clip = provider.synthesize("a private sentence")

    assert clip.audio.startswith(b"RIFF")
    assert "private" not in repr(clip)
    assert clip.characters == len("a private sentence")
    assert list(tmp_path.iterdir()) == []


def test_empty_text_makes_no_request_and_returns_no_audio():
    transport = RecordingTransport()
    provider = build_speaker("openai", api_key=TEST_KEY,
                             transport=transport)
    clip = provider.synthesize("   ")
    assert clip.is_empty()
    assert transport.count == 0


def test_a_missing_key_is_refused_before_any_request():
    transport = RecordingTransport()
    provider = build_speaker("openai", api_key="", transport=transport)
    with pytest.raises(VoiceConfigError) as excinfo:
        provider.synthesize("hello")
    assert "PEEKO_AI_API_KEY" in excinfo.value.message
    assert transport.count == 0
    assert provider.api_key_set is False


def test_an_http_error_is_reported_in_plain_words():
    transport = RecordingTransport(
        error=TTSProviderError("The speech service answered with HTTP 400. "
                               "It said: voice not found")
    )
    provider = build_speaker("openai", api_key=TEST_KEY,
                             transport=transport)
    with pytest.raises(TTSProviderError) as excinfo:
        provider.synthesize("hello")
    assert "HTTP 400" in excinfo.value.message
    assert "voice not found" in excinfo.value.message


def test_a_json_error_body_is_not_played_as_if_it_were_audio():
    body = json.dumps({"error": {"message": "voice 'nope' does not exist"}})
    provider = build_speaker(
        "openai", api_key=TEST_KEY,
        transport=RecordingTransport(body=body.encode("utf-8")),
    )
    with pytest.raises(TTSResponseError) as excinfo:
        provider.synthesize("hello")
    assert "nope" in excinfo.value.message


def test_a_timeout_is_its_own_honest_error():
    transport = RecordingTransport(error=TimeoutError("timed out"))
    provider = build_speaker("openai", api_key=TEST_KEY,
                             transport=transport)
    with pytest.raises(TTSTimeoutError) as excinfo:
        provider.synthesize("hello")
    assert "did not answer within" in excinfo.value.message


def test_an_unknown_engine_name_is_refused_honestly():
    with pytest.raises(VoiceConfigError) as excinfo:
        resolve_engine("piper")
    assert "piper" in excinfo.value.message
    assert "openai-compatible" in excinfo.value.message


def test_an_empty_engine_name_means_the_default_engine():
    assert resolve_engine("") == "openai-compatible"
    assert resolve_engine("OpenAI") == "openai"


def test_the_voice_and_model_fall_back_to_documented_defaults():
    provider = build_speaker("openai", api_key=TEST_KEY)
    assert provider.voice == DEFAULT_VOICE
    assert provider.model == DEFAULT_MODEL
    assert content_type_for("mp3") == "audio/mpeg"
    assert content_type_for("unknown") == "application/octet-stream"


def test_a_trailing_speech_path_is_not_appended_twice():
    provider = build_speaker(
        "openai", api_key=TEST_KEY,
        base_url="https://speech.test/v1/audio/speech",
    )
    assert provider.speech_url() == "https://speech.test/v1/audio/speech"


def test_the_api_key_never_reaches_the_log(caplog):
    """A transport failure that quotes the key must not leak it."""
    leaky = RecordingTransport(
        error=VoiceError(f"connection refused for Bearer {TEST_KEY}")
    )
    provider = build_speaker("openai", api_key=TEST_KEY, transport=leaky)
    with caplog.at_level(logging.DEBUG, logger="peeko.voice"):
        with pytest.raises(VoiceError) as excinfo:
            provider.synthesize("hello")
    # Nothing logged, and nothing raised, carries the key.
    assert TEST_KEY not in caplog.text
    assert TEST_KEY not in excinfo.value.message
    assert "[REDACTED]" in excinfo.value.message


def test_an_unexpected_transport_error_is_redacted_too(caplog):
    """A library exception can quote anything — it is scrubbed first."""
    leaky = RecordingTransport(
        error=RuntimeError(f"proxy said no for token {TEST_KEY}")
    )
    provider = build_speaker("openai", api_key=TEST_KEY, transport=leaky)
    with caplog.at_level(logging.DEBUG, logger="peeko.voice"):
        with pytest.raises(VoiceError) as excinfo:
            provider.synthesize("hello")
    assert TEST_KEY not in caplog.text
    assert TEST_KEY not in excinfo.value.message
    assert "[REDACTED]" in excinfo.value.message


def test_the_key_is_never_in_the_repr_or_the_diagnostics():
    provider = build_speaker("openai", api_key=TEST_KEY)
    assert TEST_KEY not in repr(provider)
    assert "<set>" in repr(provider)


# --------------------------------------------------------------------------- #
# Playback
# --------------------------------------------------------------------------- #
def test_wav_is_decoded_in_memory():
    decoded = decode_wav(wav_bytes(frames=400))
    assert decoded.sample_rate == 16_000
    assert decoded.channels == 1
    assert len(decoded.samples) == 400 * 2
    assert decoded.duration_s == pytest.approx(0.025, abs=0.001)


def test_audio_that_is_not_wav_is_refused_with_a_reason():
    with pytest.raises(TTSPlaybackError) as excinfo:
        decode_wav(b"\xff\xfbnot-a-wav", content_type="audio/mpeg")
    assert "not a WAV payload" in excinfo.value.message
    assert "response_format=wav" in excinfo.value.message


def test_empty_audio_is_refused_with_a_reason():
    with pytest.raises(TTSPlaybackError):
        decode_wav(b"")


def test_volume_is_applied_in_memory_and_clamped():
    quiet = apply_volume(b"\x10\x27" * 4, 0.5)  # 10000 -> 5000
    assert int.from_bytes(quiet[:2], "little", signed=True) == 5_000
    assert apply_volume(b"\x00\x7f" * 2, 4.0) == b"\x00\x7f\x00\x7f"
    assert apply_volume(b"abcd", 1.0) == b"abcd"


def test_playback_writes_the_audio_in_blocks():
    module = FakeSoundDevice()
    player = SoundDevicePlayer(module=module, block_bytes=200)
    payload = wav_bytes(frames=500)  # 1000 bytes of PCM
    assert player.availability() == ""
    assert player.play(clip_of(payload)) is True

    stream = module.streams[0]
    assert stream.started is True
    assert stream.kwargs["samplerate"] == 16_000
    assert stream.kwargs["channels"] == 1
    assert stream.kwargs["dtype"] == "int16"
    assert sum(len(block) for block in stream.blocks) == 1_000
    assert len(stream.blocks) == 5
    assert stream.closed is True


def test_playback_stops_between_blocks_when_asked():
    module = FakeSoundDevice()
    player = SoundDevicePlayer(module=module, block_bytes=200)
    played = player.play(clip_of(wav_bytes(frames=500)),
                         should_stop=lambda: True)
    assert played is False
    assert module.streams[0].blocks == []
    assert module.streams[0].closed is True


def test_stopping_from_another_thread_ends_the_playback():
    module = FakeSoundDevice()
    player = SoundDevicePlayer(module=module, block_bytes=100)
    seen = {"n": 0}

    def should_stop() -> bool:
        seen["n"] += 1
        return seen["n"] > 2

    assert player.play(clip_of(wav_bytes(frames=500)),
                       should_stop=should_stop) is False
    # Two blocks were played before the third check asked for a stop.
    assert len(module.streams[0].blocks) == 2
    assert module.streams[0].closed is True


def test_nothing_to_play_opens_no_device():
    module = FakeSoundDevice()
    player = SoundDevicePlayer(module=module)
    assert player.play(clip_of(b"")) is False
    assert module.streams == []


def test_a_missing_audio_library_is_an_honest_unavailable_error():
    def loader():
        raise ImportError("No module named 'sounddevice'")

    player = SoundDevicePlayer(loader=loader)
    assert "sounddevice" in player.availability()
    with pytest.raises(VoiceUnavailableError):
        player.play(clip_of(wav_bytes()))


def test_no_output_device_is_an_honest_unavailable_error():
    module = FakeSoundDevice(device_error=RuntimeError("no default device"))
    player = SoundDevicePlayer(module=module)
    assert "no audio output device" in player.availability()
    with pytest.raises(VoiceUnavailableError):
        player.play(clip_of(wav_bytes()))


def test_playback_volume_is_applied_on_the_way_out():
    module = FakeSoundDevice()
    player = SoundDevicePlayer(module=module, block_bytes=4_000)
    player.play(clip_of(wav_bytes(frames=10, sample=1_000)), volume=0.5)
    first = int.from_bytes(module.streams[0].blocks[0][:2], "little",
                           signed=True)
    assert first == 500


def test_a_stream_that_fails_to_open_is_reported_honestly():
    class Broken(FakeSoundDevice):
        def RawOutputStream(self, **kwargs):  # noqa: N802
            raise RuntimeError("device busy")

    player = SoundDevicePlayer(module=Broken())
    with pytest.raises(VoiceUnavailableError) as excinfo:
        player.play(clip_of(wav_bytes()))
    assert "no audio output device" in excinfo.value.message


def test_the_unavailable_player_always_explains_itself():
    player = UnavailablePlayer("Peeko cannot speak: no device.")
    assert player.availability() == "Peeko cannot speak: no device."
    with pytest.raises(VoiceUnavailableError):
        player.play(clip_of(wav_bytes()))
    player.stop()   # must not raise
    player.close()


def test_sounddevice_stays_lazy():
    """Importing the voice package must not import sounddevice."""
    assert "sounddevice" not in sys.modules


def test_chunks_are_bounded():
    assert list(iter_chunks(b"abcdef", 4)) == [b"abcd", b"ef"]
    assert list(iter_chunks(b"", 4)) == []


# --------------------------------------------------------------------------- #
# The synthesizer
# --------------------------------------------------------------------------- #
def test_speaking_sends_the_text_and_plays_the_audio():
    provider = MockSpeechProvider(audio=wav_bytes())
    player = FakePlayer()
    speaker = SpeechSynthesizer(provider=provider, player=player, volume=0.5)

    clip = speaker.speak("Hi, I am Peeko.")
    assert clip is not None
    assert provider.texts == ["Hi, I am Peeko."]
    assert len(player.plays) == 1
    assert player.plays[0][0].audio.startswith(b"RIFF")
    assert player.plays[0][1] == 0.5


def test_a_switched_off_voice_refuses_to_speak():
    speaker = SpeechSynthesizer(provider=MockSpeechProvider(),
                                player=FakePlayer(), enabled=False)
    assert speaker.configuration_problem() == DISABLED_TEXT
    assert "PEEKO_TTS_ENABLED=1" in DISABLED_TEXT
    with pytest.raises(VoiceConfigError) as excinfo:
        speaker.speak("hello")
    assert excinfo.value.message == DISABLED_TEXT


def test_no_key_means_nothing_is_played_and_nothing_is_sent():
    speaker = SpeechSynthesizer.from_settings(
        tts_settings(PEEKO_AI_API_KEY=""), player=FakePlayer()
    )
    problem = speaker.configuration_problem()
    assert "PEEKO_AI_API_KEY" in problem
    with pytest.raises(VoiceConfigError):
        speaker.speak("hello")


def test_no_audio_device_is_checked_before_the_service_is_called():
    provider = MockSpeechProvider(audio=wav_bytes())
    player = FakePlayer(availability="Peeko cannot speak: no device.")
    speaker = SpeechSynthesizer(provider=provider, player=player)
    assert "no device" in speaker.availability_problem()
    with pytest.raises(VoiceUnavailableError):
        speaker.speak("hello")
    assert provider.calls == 0   # the reply never left the machine


def test_empty_text_says_nothing_and_touches_nothing():
    provider = MockSpeechProvider(audio=wav_bytes())
    player = FakePlayer()
    speaker = SpeechSynthesizer(provider=provider, player=player)
    assert speaker.speak("   ") is None
    assert provider.calls == 0
    assert player.plays == []


def test_audio_that_never_arrives_is_not_reported_as_spoken():
    provider = MockSpeechProvider(audio=b"")
    player = FakePlayer()
    speaker = SpeechSynthesizer(provider=provider, player=player)
    assert speaker.speak("hello") is None
    assert player.plays == []


def test_a_cancelled_playback_reports_nothing():
    provider = MockSpeechProvider(audio=wav_bytes())
    player = FakePlayer(played=False)
    speaker = SpeechSynthesizer(provider=provider, player=player)
    assert speaker.speak("hello", should_stop=lambda: False) is None


def test_stopping_the_speaker_stops_the_player():
    player = FakePlayer()
    speaker = SpeechSynthesizer(provider=MockSpeechProvider(), player=player)
    speaker.stop()
    assert player.stops == 1
    assert speaker.speak("hello") is None


def test_a_broken_engine_name_is_recorded_not_raised():
    speaker = SpeechSynthesizer.from_settings(
        tts_settings(PEEKO_TTS_ENGINE="piper"), player=FakePlayer()
    )
    assert speaker.engine == "none"
    assert "piper" in speaker.configuration_problem()
    assert speaker.is_available() is False


def test_close_releases_the_player_and_is_idempotent():
    player = FakePlayer()
    speaker = SpeechSynthesizer(provider=MockSpeechProvider(), player=player)
    speaker.close()
    speaker.close()
    assert player.closes == 2


def test_describe_never_contains_the_key():
    speaker = SpeechSynthesizer.from_settings(
        tts_settings(), player=FakePlayer()
    )
    assert TEST_KEY not in speaker.describe()
    assert TEST_KEY not in repr(speaker)
    assert "api_key=<set>" in speaker.describe()


def test_the_default_volume_is_one_and_settings_can_lower_it():
    assert Settings().tts_volume == 1.0
    assert Settings().tts_enabled is False
    speaker = SpeechSynthesizer.from_settings(
        tts_settings(PEEKO_TTS_VOLUME="0.25"), player=FakePlayer()
    )
    assert speaker.volume == 0.25
    assert SpeechSynthesizer.from_settings(
        tts_settings(PEEKO_TTS_VOLUME="7"), player=FakePlayer()
    ).volume == 1.0
    assert SpeechSynthesizer.from_settings(
        replace(tts_settings(), tts_volume=0.0), player=FakePlayer()
    ).volume == 0.0


# --------------------------------------------------------------------------- #
# The worker
# --------------------------------------------------------------------------- #
def test_the_worker_reports_a_finished_utterance(qapp):
    speaker = SpeechSynthesizer(provider=MockSpeechProvider(audio=wav_bytes()),
                                player=FakePlayer())
    signals = SpeechWorkerSignals()
    seen: list[str] = []
    signals.finished.connect(lambda: seen.append("finished"))
    signals.empty.connect(lambda: seen.append("empty"))
    signals.failed.connect(seen.append)

    task = SpeechTask(speaker, "hello", signals=signals)
    task.run()
    assert seen == ["finished"]


def test_the_worker_reports_nothing_to_say_quietly(qapp):
    speaker = SpeechSynthesizer(provider=MockSpeechProvider(audio=b""),
                                player=FakePlayer())
    signals = SpeechWorkerSignals()
    seen: list[str] = []
    signals.empty.connect(lambda: seen.append("empty"))
    signals.failed.connect(seen.append)
    SpeechTask(speaker, "hello", signals=signals).run()
    assert seen == ["empty"]


def test_the_worker_reports_a_cancelled_playback(qapp):
    speaker = SpeechSynthesizer(provider=MockSpeechProvider(audio=wav_bytes()),
                                player=FakePlayer(played=False))
    signals = SpeechWorkerSignals()
    seen: list[str] = []
    signals.cancelled.connect(lambda: seen.append("cancelled"))
    signals.finished.connect(lambda: seen.append("finished"))
    task = SpeechTask(speaker, "hello", signals=signals)
    task.cancel()
    task.run()
    assert "cancelled" in seen


def test_the_worker_reports_a_failure_in_plain_words(qapp):
    speaker = SpeechSynthesizer(provider=None, player=FakePlayer())
    signals = SpeechWorkerSignals()
    seen: list[str] = []
    signals.failed.connect(seen.append)
    SpeechTask(speaker, "hello", signals=signals).run()
    assert len(seen) == 1
    assert "PEEKO_AI_API_KEY" in seen[0]


def test_an_unexpected_worker_failure_is_still_reported(qapp):
    class Exploding:
        def speak(self, *_args, **_kwargs):
            raise RuntimeError("boom")

        def stop(self):
            pass

    signals = SpeechWorkerSignals()
    seen: list[str] = []
    signals.failed.connect(seen.append)
    task = SpeechTask(Exploding(), "hello", signals=signals)
    task.cancel()   # also exercises stop()
    task.run()
    assert len(seen) == 1
    assert "unexpected" in seen[0].lower()


def test_submit_speech_queues_the_task_on_the_pool(qapp):
    class Pool:
        def __init__(self) -> None:
            self.started: list[object] = []

        def start(self, task) -> None:
            self.started.append(task)

    pool = Pool()
    speaker = SpeechSynthesizer(provider=MockSpeechProvider(audio=wav_bytes()),
                                player=FakePlayer())
    task = submit_speech(speaker, "hello", pool=pool)
    assert pool.started == [task]
    assert task.text == "hello"
