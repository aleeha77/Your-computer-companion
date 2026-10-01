"""Speech-to-text providers (Stage 4) — no network, no key, no microphone.

The transcription endpoint is the standard OpenAI
``POST {base_url}/audio/transcriptions`` call, so the whole provider path is
exercised against a fake HTTP transport and a fake response body. What these
tests pin down:

* the request Peeko would really send (URL, ``Authorization`` header,
  ``multipart/form-data`` boundary, the WAV part, the model field);
* a good answer comes back as the transcript, and **an empty answer stays
  empty** — no sentence is ever invented;
* every failure mode (missing key, HTTP error, timeout, a service-reported
  error, an unreadable body, a transport blow-up) becomes a
  :class:`~peeko.voice.errors.VoiceError` with a message the owner can act on;
* the API key is never in a message, an exception or a ``repr``.
"""

from __future__ import annotations

import json
import logging
import socket
import urllib.error

import pytest

from peeko.voice.audio import AudioClip, DEFAULT_SAMPLE_RATE
from peeko.voice.errors import (
    STTProviderError,
    STTResponseError,
    STTTimeoutError,
    VoiceConfigError,
    VoiceError,
)
from peeko.voice.providers import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    SUPPORTED_PROVIDERS,
    TRANSCRIPTIONS_PATH,
    UPLOAD_FILENAME,
    MockTranscriptionProvider,
    OpenAITranscriptionProvider,
    SpeechToTextProvider,
    build_transcriber,
    encode_multipart,
    normalise_provider_name,
    resolve_engine,
    urlopen_post_body,
)
from tests.conftest import (
    TEST_API_KEY,
    TEST_BASE_URL,
    FakeSTTTransport,
    pcm_samples,
    transcription_body,
)

HEARD = "turn the lights on please"


def clip(seconds: float = 0.1, *, amplitude: int = 1_200) -> AudioClip:
    """A short in-memory clip of real (non-silent) samples."""
    frames = int(DEFAULT_SAMPLE_RATE * seconds)
    return AudioClip(pcm_samples(frames, amplitude=amplitude))


def provider(transport=None, *, key: str = TEST_API_KEY, **kwargs):
    """A real provider wired to a fake transport (never a real request)."""
    return OpenAITranscriptionProvider(
        api_key=key, model="whisper-1", base_url=TEST_BASE_URL,
        transport=transport if transport is not None else FakeSTTTransport(
            transcription_body(HEARD)
        ),
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def test_a_configured_provider_reports_no_problem():
    stt = provider()
    assert stt.is_configured() is True
    assert stt.configuration_problem() == ""


def test_a_missing_key_is_reported_with_the_variable_to_set():
    stt = provider(key="")
    assert stt.is_configured() is False
    problem = stt.configuration_problem()
    assert "PEEKO_AI_API_KEY" in problem
    assert ".env" in problem
    assert stt.api_key_set is False


def test_a_missing_model_falls_back_to_the_documented_default():
    stt = OpenAITranscriptionProvider(api_key=TEST_API_KEY, model="")
    assert stt.model == DEFAULT_MODEL
    assert stt.configuration_problem() == ""


def test_the_key_never_appears_in_the_repr_or_a_message(caplog):
    caplog.set_level(logging.DEBUG, logger="peeko.voice")
    stt = provider(FakeSTTTransport(error=RuntimeError(f"boom {TEST_API_KEY}")))
    assert TEST_API_KEY not in repr(stt)
    assert "<set>" in repr(stt)

    with pytest.raises(STTProviderError) as excinfo:
        stt.transcribe(clip())
    assert TEST_API_KEY not in excinfo.value.message
    assert TEST_API_KEY not in caplog.text


def test_the_provider_satisfies_the_protocol():
    assert isinstance(provider(), SpeechToTextProvider)


# --------------------------------------------------------------------------- #
# Engine selection
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", SUPPORTED_PROVIDERS)
def test_supported_engine_names_are_accepted(name):
    assert resolve_engine(name) in SUPPORTED_PROVIDERS
    assert build_transcriber(name, api_key=TEST_API_KEY).name == name


def test_an_empty_engine_means_the_default_engine():
    assert resolve_engine("") == "openai-compatible"
    assert resolve_engine(None) == "openai-compatible"
    assert resolve_engine("   ") == "openai-compatible"


@pytest.mark.parametrize(
    "alias", ["whisper", "OPENAI", "openai_compatible", "openai_compat",
              "compatible", "  openai  "],
)
def test_engine_aliases_are_normalised(alias):
    assert normalise_provider_name(alias) in SUPPORTED_PROVIDERS


def test_an_unknown_engine_is_refused_with_the_supported_list():
    with pytest.raises(VoiceConfigError) as excinfo:
        build_transcriber("banana", api_key=TEST_API_KEY)
    message = excinfo.value.message
    assert "banana" in message
    assert "openai" in message
    assert "PEEKO_VOICE_INPUT_ENGINE" in message
    assert normalise_provider_name("banana") == ""
    assert normalise_provider_name(None) == ""


# --------------------------------------------------------------------------- #
# The request Peeko would send
# --------------------------------------------------------------------------- #
def test_a_good_answer_comes_back_as_the_transcript():
    transport = FakeSTTTransport(transcription_body(HEARD))
    assert provider(transport).transcribe(clip()) == HEARD
    assert transport.call_count == 1


def test_the_request_targets_the_transcriptions_endpoint():
    transport = FakeSTTTransport(transcription_body(HEARD))
    stt = provider(transport)
    assert stt.transcriptions_url() == (
        f"{TEST_BASE_URL}{TRANSCRIPTIONS_PATH}"
    )
    stt.transcribe(clip())
    assert transport.last_call["url"] == (
        f"{TEST_BASE_URL}{TRANSCRIPTIONS_PATH}"
    )
    assert transport.last_call["timeout"] == stt.timeout_s


def test_a_full_endpoint_url_is_not_appended_to_twice():
    stt = OpenAITranscriptionProvider(
        api_key=TEST_API_KEY, model="whisper-1",
        base_url=f"https://host/v1{TRANSCRIPTIONS_PATH}/",
    )
    assert stt.transcriptions_url() == f"https://host/v1{TRANSCRIPTIONS_PATH}"


def test_a_base_url_defaults_to_the_documented_endpoint():
    stt = OpenAITranscriptionProvider(api_key=TEST_API_KEY, model="whisper-1")
    assert stt.base_url == DEFAULT_BASE_URL
    assert stt.transcriptions_url().startswith(DEFAULT_BASE_URL)


def test_the_request_is_authenticated_and_multipart():
    transport = FakeSTTTransport(transcription_body(HEARD))
    provider(transport).transcribe(clip())
    headers = transport.last_call["headers"]
    assert headers["Authorization"] == f"Bearer {TEST_API_KEY}"
    assert headers["Content-Type"].startswith(
        "multipart/form-data; boundary="
    )
    assert headers["Accept"] == "application/json"


def test_the_upload_carries_a_wav_part_and_the_model_field():
    transport = FakeSTTTransport(transcription_body(HEARD))
    stt = provider(transport)
    stt.transcribe(clip())
    body = transport.sent_body
    assert transport.sent_audio is True
    assert b'name="file"' in body
    assert UPLOAD_FILENAME.encode() in body
    assert b"Content-Type: audio/wav" in body
    assert b'name="model"' in body
    assert stt.model.encode() in body
    assert b'name="response_format"' in body


def test_the_uploaded_audio_is_the_clip_that_was_captured():
    """The samples really travel — byte for byte — into the request body."""
    transport = FakeSTTTransport(transcription_body(HEARD))
    captured = clip()
    provider(transport).transcribe(captured)
    assert b"RIFF" in transport.sent_body
    # The WAV payload is written in memory and embedded unchanged, so the
    # exact sample bytes appear in the multipart body.
    assert captured.samples[:64] in transport.sent_body


def test_the_boundary_is_unique_per_request():
    transport = FakeSTTTransport(transcription_body(HEARD))
    stt = provider(transport)
    stt.transcribe(clip())
    stt.transcribe(clip())
    first = transport.calls[0]["headers"]["Content-Type"]
    second = transport.calls[1]["headers"]["Content-Type"]
    assert first != second
    assert b"--" in transport.sent_body


def test_multipart_encoding_puts_every_field_and_the_file_in_one_body():
    body = encode_multipart(
        {"model": "m", "response_format": "json"},
        name="file", filename="a.wav", content=b"RIFFDATA",
        content_type="audio/wav", boundary="BOUND",
    )
    assert body.count(b"--BOUND") == 4  # two fields, the file, one closing
    assert body.startswith(b"--BOUND\r\n")
    assert body.endswith(b"--BOUND--\r\n")
    assert b"RIFFDATA" in body
    assert b'name="file"; filename="a.wav"' in body


# --------------------------------------------------------------------------- #
# Nothing to transcribe
# --------------------------------------------------------------------------- #
def test_silence_is_not_sent_and_yields_an_empty_transcript():
    """No audio means no request — and never an invented sentence."""
    transport = FakeSTTTransport(transcription_body(HEARD))
    assert provider(transport).transcribe(AudioClip()) == ""
    assert provider(transport).transcribe(None) == ""
    assert transport.call_count == 0


def test_an_empty_service_answer_stays_empty():
    transport = FakeSTTTransport(transcription_body(""))
    assert provider(transport).transcribe(clip()) == ""
    assert transport.call_count == 1  # the request did happen


def test_surrounding_whitespace_is_trimmed_from_a_transcript():
    transport = FakeSTTTransport(transcription_body(f"  {HEARD} \n"))
    assert provider(transport).transcribe(clip()) == HEARD


# --------------------------------------------------------------------------- #
# Failures are honest, and named
# --------------------------------------------------------------------------- #
def test_an_unconfigured_provider_never_makes_a_request():
    transport = FakeSTTTransport(transcription_body(HEARD))
    with pytest.raises(VoiceConfigError) as excinfo:
        provider(transport, key="").transcribe(clip())
    assert "PEEKO_AI_API_KEY" in excinfo.value.message
    assert transport.call_count == 0


def test_an_http_error_is_reported_with_its_status():
    transport = FakeSTTTransport(error=STTProviderError(
        "The speech service answered with HTTP 401."
    ))
    with pytest.raises(STTProviderError) as excinfo:
        provider(transport).transcribe(clip())
    assert "HTTP 401" in excinfo.value.message


def test_a_timeout_is_reported_in_plain_words():
    transport = FakeSTTTransport(error=TimeoutError("slow"))
    with pytest.raises(STTTimeoutError) as excinfo:
        provider(transport, timeout_s=12).transcribe(clip())
    assert "did not answer within 12 seconds" in excinfo.value.message


def test_a_socket_timeout_is_also_a_timeout():
    transport = FakeSTTTransport(error=socket.timeout("timed out"))
    with pytest.raises(STTTimeoutError):
        provider(transport).transcribe(clip())


def test_a_transport_blow_up_is_redacted_and_reported():
    transport = FakeSTTTransport(error=RuntimeError(f"boom {TEST_API_KEY}"))
    with pytest.raises(STTProviderError) as excinfo:
        provider(transport).transcribe(clip())
    message = excinfo.value.message
    assert "[REDACTED]" in message
    assert TEST_API_KEY not in message
    assert "Could not reach the speech service" in message


def test_an_error_reported_by_the_service_is_passed_on():
    transport = FakeSTTTransport(
        json.dumps({"error": {"message": "audio too short"}})
    )
    with pytest.raises(STTProviderError) as excinfo:
        provider(transport).transcribe(clip())
    assert "audio too short" in excinfo.value.message


def test_a_service_error_that_quotes_the_key_is_redacted():
    transport = FakeSTTTransport(
        json.dumps({"error": {"message": f"bad key {TEST_API_KEY}"}})
    )
    with pytest.raises(STTProviderError) as excinfo:
        provider(transport).transcribe(clip())
    assert TEST_API_KEY not in excinfo.value.message
    assert "[REDACTED]" in excinfo.value.message


@pytest.mark.parametrize("body", ["not json at all", "[1, 2, 3]", "null"])
def test_an_unreadable_body_is_reported_honestly(body):
    transport = FakeSTTTransport(body)
    with pytest.raises(VoiceError) as excinfo:
        provider(transport).transcribe(clip())
    assert isinstance(excinfo.value, STTResponseError)
    assert "could not read" in excinfo.value.message.lower() or \
        "unexpected response shape" in excinfo.value.message.lower()


def test_a_reply_without_a_text_field_is_reported_honestly():
    transport = FakeSTTTransport(json.dumps({"confidence": 0.9}))
    with pytest.raises(STTResponseError) as excinfo:
        provider(transport).transcribe(clip())
    assert "text" in excinfo.value.message


def test_a_text_field_of_the_wrong_type_is_reported_honestly():
    transport = FakeSTTTransport(json.dumps({"text": 42}))
    with pytest.raises(STTResponseError):
        provider(transport).transcribe(clip())


# --------------------------------------------------------------------------- #
# The default transport's own error mapping (still no network)
# --------------------------------------------------------------------------- #
def test_the_default_transport_maps_an_http_error(monkeypatch):
    def raise_http(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "https://host/v1/audio/transcriptions", 500, "Server Error",
            {}, fp=__import__("io").BytesIO(b'{"error": "busy"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", raise_http)
    with pytest.raises(STTProviderError) as excinfo:
        urlopen_post_body("https://host/v1/audio/transcriptions", {}, b"", 5.0)
    assert "HTTP 500" in excinfo.value.message
    assert "busy" in excinfo.value.message


def test_the_default_transport_maps_a_connection_failure(monkeypatch):
    def raise_url(*_args, **_kwargs):
        raise urllib.error.URLError("Name or service not known")

    monkeypatch.setattr("urllib.request.urlopen", raise_url)
    with pytest.raises(STTProviderError) as excinfo:
        urlopen_post_body("https://host/v1/audio/transcriptions", {}, b"", 5.0)
    assert "Could not reach" in excinfo.value.message


def test_the_default_transport_maps_a_urlerror_timeout(monkeypatch):
    def raise_timeout(*_args, **_kwargs):
        raise urllib.error.URLError(socket.timeout("timed out"))

    monkeypatch.setattr("urllib.request.urlopen", raise_timeout)
    with pytest.raises(STTTimeoutError) as excinfo:
        urlopen_post_body("https://host/v1/audio/transcriptions", {}, b"", 7.0)
    assert "did not answer within 7 seconds" in excinfo.value.message


def test_the_default_transport_returns_the_response_text(monkeypatch):
    class Response:
        def read(self) -> bytes:
            return b'{"text": "hi"}'

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *_a, **_k: Response()
    )
    assert urlopen_post_body("https://host/x", {}, b"data", 5.0) == \
        '{"text": "hi"}'


# --------------------------------------------------------------------------- #
# The test double is a test double
# --------------------------------------------------------------------------- #
def test_the_mock_provider_records_the_clips_it_was_given():
    mock = MockTranscriptionProvider(texts=["first", "second"])
    assert mock.is_configured() is True
    assert mock.configuration_problem() == ""
    assert mock.transcribe(clip()) == "first"
    assert mock.transcribe(clip()) == "second"
    assert mock.call_count == 2
    assert mock.last_clip.samples
    assert mock.transcribe(clip()) == ""  # the queue is exhausted


def test_the_mock_provider_can_raise_a_queued_error():
    mock = MockTranscriptionProvider(error=STTProviderError("service down"))
    with pytest.raises(STTProviderError, match="service down"):
        mock.transcribe(clip())


def test_build_transcriber_never_returns_the_mock_provider():
    built = build_transcriber("", api_key=TEST_API_KEY)
    assert isinstance(built, OpenAITranscriptionProvider)
    assert not isinstance(built, MockTranscriptionProvider)
    assert built.name == "openai-compatible"
