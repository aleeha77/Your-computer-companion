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
    """Future-stage interfaces must fail loudly, never fake success.

    The stage numbers are the owner's roadmap: 1 avatar → 2 interaction → 3 AI
    chat → 4 voice input → 5 TTS → 6 emotions → 7 needs → 8 memory → 9 app
    awareness. A stub that names a stage already behind us (or someone else's
    stage) is exactly the kind of white lie this test exists to catch.
    """
    from peeko.memory.store import MemoryStore

    store = MemoryStore(db_path=Path("/nonexistent/peeko.db"))
    with pytest.raises(NotImplementedError, match="Stage 8"):
        store.remember("fact", "owner", "Ada")
    with pytest.raises(NotImplementedError, match="Stage 8"):
        store.recall("fact", "owner")

    from peeko.awareness.active_window import ActiveWindowTracker

    tracker = ActiveWindowTracker()
    with pytest.raises(NotImplementedError, match="Stage 9"):
        tracker.get_active_window_title()

    # Nothing claims to be finished that is not: the two remaining stubs name
    # *future* stages, never the stages Peeko has already shipped.
    for method, args in ((store.remember, ("fact", "owner", "Ada")),
                         (store.recall, ("fact", "owner")),
                         (tracker.get_active_window_title, ())):
        with pytest.raises(NotImplementedError) as excinfo:
            method(*args)
        message = str(excinfo.value)
        assert "Stage 8" in message or "Stage 9" in message
        assert "Stage 4" not in message and "Stage 5" not in message


def test_the_speech_synthesizer_is_no_longer_a_stub():
    """Stage 5 replaced the TTS stub with a real, honest synthesizer.

    No request and no speaker happen here: the point is that the synthesizer
    reports its own configuration truthfully instead of pretending (see
    :mod:`tests.test_voice_output` for synthesis, playback and the worker, and
    :mod:`tests.test_ui_chat_window` for the Speak button end to end).
    """
    from peeko.voice.errors import VoiceConfigError
    from peeko.voice.output import SpeechSynthesizer

    unconfigured = SpeechSynthesizer()          # no key, no provider
    assert unconfigured.configuration_problem() != ""
    assert "PEEKO_AI_API_KEY" in unconfigured.configuration_problem()
    with pytest.raises(VoiceConfigError):
        unconfigured.speak("hello")

    # A configured synthesizer really speaks — the mock provider and the fake
    # player are test doubles, so this stays offline and needs no speaker.
    from peeko.voice.output_providers import MockSpeechProvider

    from conftest import FakeSpeechPlayer, speech_wav

    provider = MockSpeechProvider(audio=speech_wav())
    player = FakeSpeechPlayer()
    speaker = SpeechSynthesizer(provider=provider, player=player)
    clip = speaker.speak("hello")
    assert clip is not None
    assert provider.texts == ["hello"]
    assert len(player.plays) == 1
    assert player.plays[0][0].audio.startswith(b"RIFF")

    # …and a switched-off voice refuses instead of pretending to speak.
    quiet = SpeechSynthesizer(provider=provider, player=player, enabled=False)
    with pytest.raises(VoiceConfigError) as excinfo:
        quiet.speak("hello")
    assert "PEEKO_TTS_ENABLED=1" in excinfo.value.message


def test_ai_client_never_leaks_key():
    from peeko.ai.client import AIClient

    client = AIClient(provider="openai", model="m", api_key="super-secret")
    # repr must never reveal the key, even though the attribute exists.
    assert "super-secret" not in repr(client)
    assert client.is_configured() is True
