"""Tests for peeko.db and the planned-interface stubs of other subsystems."""

from __future__ import annotations

from pathlib import Path

import pytest

from peeko.db import Database, SCHEMA_VERSION


def test_database_initialize_creates_schema(tmp_path):
    db = Database(tmp_path / "peeko.db")
    with db:
        assert db.meta("schema_version") == str(SCHEMA_VERSION)

    # Reopening works and keeps the version.
    db2 = Database(tmp_path / "peeko.db")
    with db2:
        assert db2.meta("schema_version") == str(SCHEMA_VERSION)


def test_database_requires_connect(tmp_path):
    db = Database(tmp_path / "nope.db")
    with pytest.raises(RuntimeError, match="connect"):
        db.meta("schema_version")
    with pytest.raises(RuntimeError, match="connect"):
        db.initialize()


def test_the_ai_chat_client_is_no_longer_a_stub():
    """Stage 3 replaced the AI stub with a real, validated client.

    No request is made here: the point is that the client reports its own
    configuration honestly instead of pretending (see
    :mod:`tests.test_ai_client_provider` for the whole chat path).
    """
    from peeko.ai.client import AIClient

    configured = AIClient(provider="openai", model="gpt-4o-mini",
                          api_key="sk-test")
    assert configured.is_configured() is True
    assert configured.configuration_problem() == ""

    unconfigured = AIClient(provider="openai", model="gpt-4o-mini")
    assert unconfigured.is_configured() is False
    assert unconfigured.configuration_problem() == (
        "AI not configured — set PEEKO_AI_API_KEY in .env"
    )


def test_the_voice_recognizer_is_no_longer_a_stub():
    """Stage 4 replaced the voice-input stub with a real recognizer.

    No microphone and no request happen here: the point is that it reports its
    own configuration honestly instead of pretending (see
    :mod:`tests.test_voice_input` for the whole capture path, and
    :mod:`tests.test_voice_worker` for the off-thread bridge).
    """
    from peeko.voice.errors import VoiceConfigError, VoiceUnavailableError
    from peeko.voice.input import SpeechRecognizer
    from peeko.voice.providers import MockTranscriptionProvider

    unconfigured = SpeechRecognizer()          # no key, no provider
    assert unconfigured.configuration_problem() != ""
    assert "PEEKO_AI_API_KEY" in unconfigured.configuration_problem()
    with pytest.raises(VoiceConfigError):
        unconfigured.transcribe(b"audio")
    with pytest.raises(VoiceConfigError):
        unconfigured.listen()

    # A configured recognizer really transcribes — the mock provider is a
    # test double, so this stays offline and needs no microphone.
    from peeko.voice.audio import AudioClip

    configured = SpeechRecognizer(provider=MockTranscriptionProvider("hello"))
    assert configured.transcribe(AudioClip(b"\x00\x10" * 800)) == "hello"

    # …and a machine with no microphone is reported, not faked.
    from peeko.voice.audio import UnavailableAudioSource

    deaf = SpeechRecognizer(
        source=UnavailableAudioSource(),
        provider=MockTranscriptionProvider("hello"),
    )
    with pytest.raises(VoiceUnavailableError):
        deaf.listen()


def test_unimplemented_subsystems_raise_honestly():
    """Future-stage interfaces must fail loudly, never fake success."""

    from peeko.voice.output import SpeechSynthesizer

    with pytest.raises(NotImplementedError, match="Stage 5"):
        SpeechSynthesizer().speak("hello")

    from peeko.memory.store import MemoryStore

    store = MemoryStore(db_path=Path("/nonexistent/peeko.db"))
    with pytest.raises(NotImplementedError, match="Stage 4"):
        store.remember("fact", "owner", "Ada")

    from peeko.awareness.active_window import ActiveWindowTracker

    with pytest.raises(NotImplementedError, match="Stage 8"):
        ActiveWindowTracker().get_active_window_title()


def test_ai_client_never_leaks_key():
    from peeko.ai.client import AIClient

    client = AIClient(provider="openai", model="m", api_key="super-secret")
    # repr must never reveal the key, even though the attribute exists.
    assert "super-secret" not in repr(client)
    assert client.is_configured() is True