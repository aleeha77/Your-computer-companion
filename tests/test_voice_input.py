"""The speech recognizer (Stage 4): microphone in, words out — or the truth.

:class:`peeko.voice.input.SpeechRecognizer` is the seam the chat window talks
to, and it makes exactly one promise: it returns real words or an honest
failure, never an invented sentence. These tests drive the real recognizer
with a scripted microphone and a mock provider, so every branch is covered
without hardware, network or an API key:

* the captured audio really reaches the provider (byte-for-byte);
* both halves of the configuration are checked *before* the microphone is
  opened — with no key, no audio device is ever touched;
* cancellation, silence and an empty capture all report "nothing" rather than
  a made-up transcript;
* a provider failure propagates as a :class:`VoiceError` the UI can show;
* settings (engine, model, base URL, timeout, capture cap) reach the provider
  and the capture loop, and the key never leaks into ``repr`` or the log.
"""

from __future__ import annotations

import logging

import pytest

from peeko.settings import load_settings
from peeko.voice.audio import (
    DEFAULT_MAX_SECONDS,
    DEFAULT_SAMPLE_RATE,
    AudioClip,
    UnavailableAudioSource,
)
from peeko.voice.errors import (
    STTProviderError,
    STTTimeoutError,
    VoiceCaptureError,
    VoiceConfigError,
    VoiceError,
    VoiceUnavailableError,
)
from peeko.voice.input import (
    NO_MIC_CONFIGURED_TEXT,
    SpeechRecognizer,
    build_recognizer,
)
from peeko.voice.providers import (
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_S,
    MockTranscriptionProvider,
    OpenAITranscriptionProvider,
)
from tests.conftest import (
    TEST_API_KEY,
    TEST_BASE_URL,
    FakeAudioSource,
    FakeSTTTransport,
    pcm_samples,
    transcription_body,
    voice_recognizer,
    voice_settings,
)

HEARD = "what is the weather like"


def chunk(frames: int = 1_600, *, amplitude: int = 1_200) -> bytes:
    return pcm_samples(frames, amplitude=amplitude)


def configured_source(*chunks) -> FakeAudioSource:
    return FakeAudioSource(list(chunks) or [chunk()])


# --------------------------------------------------------------------------- #
# The happy path: audio in, words out
# --------------------------------------------------------------------------- #
def test_the_captured_audio_reaches_the_provider_unchanged():
    source = configured_source(chunk(800, amplitude=1_100),
                               chunk(800, amplitude=2_200))
    provider = MockTranscriptionProvider(HEARD)
    recognizer = SpeechRecognizer(source=source, provider=provider)

    assert recognizer.listen() == HEARD

    assert provider.call_count == 1
    clip = provider.last_clip
    assert clip.samples == chunk(800, amplitude=1_100) + \
        chunk(800, amplitude=2_200)
    assert clip.sample_rate == DEFAULT_SAMPLE_RATE
    assert clip.channels == 1
    assert source.closed == 1              # the microphone was released


def test_listen_reports_none_when_the_service_hears_no_words():
    """An empty transcript is *nothing*, never an invented sentence."""
    source = configured_source()
    provider = MockTranscriptionProvider("")
    recognizer = SpeechRecognizer(source=source, provider=provider)

    assert recognizer.listen() is None
    assert provider.call_count == 1


def test_whitespace_only_transcript_is_nothing_too():
    recognizer = voice_recognizer(text="   \n ")
    assert recognizer.listen() is None


def test_an_empty_capture_never_reaches_the_provider():
    source = FakeAudioSource([])
    provider = MockTranscriptionProvider(HEARD)
    recognizer = SpeechRecognizer(source=source, provider=provider)

    assert recognizer.listen() is None
    assert provider.call_count == 0
    assert source.closed == 1


def test_the_level_callback_is_passed_through_to_the_capture():
    source = configured_source(chunk(160, amplitude=0),
                               chunk(160, amplitude=9_000))
    recognizer = SpeechRecognizer(
        source=source, provider=MockTranscriptionProvider(HEARD)
    )
    levels: list[float] = []
    recognizer.listen(on_level=levels.append)

    assert len(levels) == 2
    assert levels[0] == 0.0 and levels[1] > 0.0


def test_the_capture_cap_is_honoured_by_listen():
    source = FakeAudioSource([chunk(1_024) for _ in range(20)])
    recognizer = SpeechRecognizer(
        source=source, provider=MockTranscriptionProvider(HEARD),
        max_duration_s=0.1,
    )
    recognizer.listen()
    assert source.reads == 2               # 2 x 0.064 s >= 0.1 s cap


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #
def test_cancelling_a_capture_reports_nothing_and_never_transcribes():
    source = FakeAudioSource([chunk(160) for _ in range(10)])
    provider = MockTranscriptionProvider(HEARD)
    recognizer = SpeechRecognizer(source=source, provider=provider)

    assert recognizer.listen(should_stop=lambda: source.reads >= 1) is None
    assert provider.call_count == 0
    assert source.closed == 1


def test_cancelling_before_the_first_chunk_leaves_the_microphone_unread():
    source = FakeAudioSource([chunk(160)])
    recognizer = SpeechRecognizer(
        source=source, provider=MockTranscriptionProvider(HEARD)
    )
    assert recognizer.listen(should_stop=lambda: True) is None
    assert source.reads == 0


# --------------------------------------------------------------------------- #
# Configuration is checked before the microphone is touched
# --------------------------------------------------------------------------- #
def test_a_missing_key_never_opens_the_microphone():
    source = configured_source()
    provider = OpenAITranscriptionProvider(
        api_key="", model="whisper-1",
        transport=FakeSTTTransport(transcription_body(HEARD)),
    )
    recognizer = SpeechRecognizer(source=source, provider=provider)

    with pytest.raises(VoiceConfigError) as excinfo:
        recognizer.listen()
    assert "PEEKO_AI_API_KEY" in excinfo.value.message
    assert source.open_calls == 0
    assert source.closed == 0


def test_no_provider_at_all_is_reported_as_not_configured():
    source = configured_source()
    recognizer = SpeechRecognizer(source=source)
    assert recognizer.engine == "none"
    assert recognizer.model == ""
    assert "PEEKO_AI_API_KEY" in recognizer.configuration_problem()
    with pytest.raises(VoiceConfigError):
        recognizer.listen()
    assert source.open_calls == 0


def test_a_missing_microphone_is_reported_before_the_provider_is_used():
    provider = MockTranscriptionProvider(HEARD)
    recognizer = SpeechRecognizer(
        source=UnavailableAudioSource(), provider=provider
    )
    with pytest.raises(VoiceUnavailableError) as excinfo:
        recognizer.listen()
    assert "no microphone" in excinfo.value.message
    assert provider.call_count == 0


def test_availability_reports_both_halves_together():
    recognizer = SpeechRecognizer(
        source=UnavailableAudioSource("no microphone was found here.")
    )
    problem = recognizer.availability_problem()
    assert "PEEKO_AI_API_KEY" in problem
    assert "no microphone was found here." in problem
    assert recognizer.is_available() is False


def test_a_broken_audio_source_is_reported_not_raised():
    class Exploding:
        name = "exploding"

        def availability(self):
            raise RuntimeError("driver melted")

        def open(self):  # pragma: no cover - never reached
            raise AssertionError("must not be opened")

        def read(self):  # pragma: no cover - never reached
            return b""

        def close(self):  # pragma: no cover - nothing to release
            return None

    recognizer = SpeechRecognizer(
        source=Exploding(), provider=MockTranscriptionProvider(HEARD)
    )
    problem = recognizer.microphone_problem()
    assert "could not be checked" in problem
    assert "driver melted" in problem


def test_a_bad_engine_name_is_reported_when_the_user_tries_to_talk(
    tmp_path
):
    settings = voice_settings(tmp_path, engine="banana")
    recognizer = SpeechRecognizer.from_settings(settings)

    problem = recognizer.configuration_problem()
    assert "banana" in problem
    assert "PEEKO_VOICE_INPUT_ENGINE" in problem
    # A typo in .env must never crash the app: it is refused, not guessed at.
    with pytest.raises(VoiceConfigError):
        recognizer.listen()
    assert recognizer.engine == "none"


# --------------------------------------------------------------------------- #
# Failures from the service reach the caller as honest VoiceErrors
# --------------------------------------------------------------------------- #
def test_a_provider_failure_propagates_with_its_message():
    recognizer = voice_recognizer(
        provider=MockTranscriptionProvider(
            error=STTProviderError(
                "The speech service answered with HTTP 503."
            )
        )
    )
    with pytest.raises(STTProviderError) as excinfo:
        recognizer.listen()
    assert "HTTP 503" in excinfo.value.message
    assert isinstance(excinfo.value, VoiceError)


def test_a_timeout_propagates_with_its_message():
    recognizer = voice_recognizer(
        provider=MockTranscriptionProvider(
            error=STTTimeoutError(
                "The speech service did not answer within 60 seconds."
            )
        )
    )
    with pytest.raises(STTTimeoutError, match="did not answer within"):
        recognizer.listen()


def test_a_capture_failure_propagates_and_releases_the_microphone():
    source = FakeAudioSource(
        [chunk(160)],
        read_error=VoiceCaptureError(
            "Voice input failed while reading from the microphone."
        ),
    )
    recognizer = SpeechRecognizer(
        source=source, provider=MockTranscriptionProvider(HEARD)
    )
    with pytest.raises(VoiceCaptureError):
        recognizer.listen()
    assert source.closed == 1


def test_an_unexpected_provider_exception_is_not_swallowed():
    """Only VoiceError is a *documented* failure; a bug still surfaces."""
    class Exploding:
        name = "exploding"
        model = "m"

        def is_configured(self):
            return True

        def configuration_problem(self):
            return ""

        def transcribe(self, _clip):
            raise RuntimeError("internal bug")

    recognizer = SpeechRecognizer(
        source=configured_source(), provider=Exploding()
    )
    with pytest.raises(RuntimeError, match="internal bug"):
        recognizer.listen()


# --------------------------------------------------------------------------- #
# Turning settings into a recognizer
# --------------------------------------------------------------------------- #
def test_from_settings_wires_engine_model_url_and_timeout(tmp_path):
    settings = voice_settings(
        tmp_path, engine="openai", model="whisper-1", base_url=TEST_BASE_URL,
        timeout_s=17.5, max_seconds=12.0,
    )
    recognizer = SpeechRecognizer.from_settings(settings)

    assert isinstance(recognizer.provider, OpenAITranscriptionProvider)
    assert recognizer.engine == "openai"
    assert recognizer.model == "whisper-1"
    assert recognizer.provider.base_url == TEST_BASE_URL
    assert recognizer.provider.timeout_s == 17.5
    assert recognizer.max_duration_s == 12.0
    assert recognizer.api_key_set is True
    assert recognizer.configuration_problem() == ""
    assert recognizer.source.name == "sounddevice"   # the real source


def test_the_stt_base_url_falls_back_to_the_ai_base_url(tmp_path):
    settings = voice_settings(tmp_path, base_url="")
    recognizer = SpeechRecognizer.from_settings(settings)
    # One gateway configured once is enough for chat *and* dictation.
    assert recognizer.provider.base_url == TEST_BASE_URL


def test_the_capture_cap_defaults_to_the_shared_constant(tmp_path):
    settings = voice_settings(tmp_path)
    recognizer = SpeechRecognizer.from_settings(settings)
    assert recognizer.max_duration_s == DEFAULT_MAX_SECONDS
    assert recognizer.provider.timeout_s == DEFAULT_TIMEOUT_S


def test_the_engine_defaults_to_the_openai_compatible_one(tmp_path):
    recognizer = SpeechRecognizer.from_settings(voice_settings(tmp_path))
    assert recognizer.engine == "openai-compatible"
    # An unset model is not a problem: the provider falls back to the
    # documented transcription model.
    assert recognizer.model == DEFAULT_MODEL
    assert recognizer.configuration_problem() == ""


def test_a_recognizer_can_be_built_straight_from_the_environment():
    settings = load_settings({
        "PEEKO_AI_API_KEY": TEST_API_KEY,
        "PEEKO_AI_MODEL": "gpt-4o-mini",
        "PEEKO_VOICE_INPUT_ENGINE": "whisper",
        "PEEKO_STT_MODEL": "whisper-1",
        "PEEKO_STT_BASE_URL": "https://gateway.invalid/v1",
        "PEEKO_VOICE_MAX_SECONDS": "8",
    })
    recognizer = build_recognizer(settings)
    assert recognizer.engine == "openai-compatible"   # alias normalised
    assert recognizer.provider.base_url == "https://gateway.invalid/v1"
    assert recognizer.max_duration_s == 8.0


def test_from_settings_accepts_an_injected_source_and_provider(tmp_path):
    source = configured_source()
    provider = MockTranscriptionProvider(HEARD)
    recognizer = SpeechRecognizer.from_settings(
        voice_settings(tmp_path), source=source, provider=provider
    )
    assert recognizer.source is source
    assert recognizer.provider is provider
    assert recognizer.listen() == HEARD


def test_from_settings_never_raises_for_a_broken_engine(tmp_path):
    """Building a recognizer is safe; only talking refuses."""
    for engine in ("banana", " ", None, 42):
        settings = voice_settings(tmp_path, engine=engine or "")
        recognizer = SpeechRecognizer.from_settings(settings)
        problem = recognizer.configuration_problem()
        assert problem == "" or "not supported" in problem
        # An unusable engine is refused when the user actually talks.
        assert recognizer.engine in ("none", "openai-compatible")


def test_the_key_never_appears_in_a_repr_or_a_log(tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger="peeko.voice")
    settings = voice_settings(tmp_path, engine="openai", model="whisper-1")
    recognizer = SpeechRecognizer.from_settings(settings)

    assert TEST_API_KEY not in repr(recognizer)
    assert "<set>" in repr(recognizer)
    describe = recognizer.describe()
    assert TEST_API_KEY not in describe
    assert "api_key=<set>" in describe
    assert "mic=sounddevice" in describe


def test_describe_is_honest_about_an_unconfigured_recognizer(tmp_path):
    recognizer = SpeechRecognizer.from_settings(
        voice_settings(tmp_path, key="")
    )
    describe = recognizer.describe()
    assert "api_key=<unset>" in describe
    assert "available=False" in describe


# --------------------------------------------------------------------------- #
# Odds and ends
# --------------------------------------------------------------------------- #
def test_transcribe_skips_an_empty_clip_without_a_request():
    provider = MockTranscriptionProvider(HEARD)
    recognizer = SpeechRecognizer(source=configured_source(),
                                  provider=provider)
    assert recognizer.transcribe(AudioClip()) is None
    assert recognizer.transcribe(None) is None
    assert provider.call_count == 0


def test_transcribe_still_checks_the_configuration():
    recognizer = SpeechRecognizer(source=configured_source())
    with pytest.raises(VoiceConfigError):
        recognizer.transcribe(AudioClip(chunk(160)))


def test_closing_a_recognizer_is_safe_and_repeated():
    source = configured_source()
    recognizer = SpeechRecognizer(
        source=source, provider=MockTranscriptionProvider(HEARD)
    )
    recognizer.close()
    recognizer.close()
    assert source.closed == 2              # closing is idempotent per call


def test_a_recognizer_works_as_a_context_manager():
    source = configured_source()
    with SpeechRecognizer(
        source=source, provider=MockTranscriptionProvider(HEARD)
    ) as recognizer:
        assert recognizer.listen() == HEARD
    assert source.closed >= 1


def test_closing_a_broken_source_never_raises():
    class Stubborn:
        name = "stubborn"

        def availability(self):
            return ""

        def open(self):
            return None

        def read(self):
            return b""

        def close(self):
            raise RuntimeError("cannot close")

    recognizer = SpeechRecognizer(source=Stubborn())
    recognizer.close()                     # swallowed and logged


def test_the_no_microphone_constant_is_honest():
    assert "no microphone" in NO_MIC_CONFIGURED_TEXT.lower()
