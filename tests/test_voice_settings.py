"""Voice-input settings (Stage 4) — defaults, env overrides, and the wiring.

The ``voice_*`` fields stopped being placeholders at Stage 4: they now
configure the Mic button in the chat window. These tests pin the surface the
owner reads in ``README.md`` / ``.env.example`` against the code:

* the documented defaults, and the fact that they are the *same* constants the
  voice package uses (so the settings view and the recognizer cannot drift
  apart);
* ``PEEKO_VOICE_ENABLED`` switching the microphone off, in every spelling the
  project documents (``1/0``, ``true/false``, ``yes/no``, ``on/off``);
* every variable reaching the recognizer that actually performs the capture;
* the AI API key staying out of anything that is logged or displayed.
"""

from __future__ import annotations

import logging

import pytest

from peeko.settings import (
    DEFAULT_VOICE_MAX_SECONDS,
    DEFAULT_VOICE_TIMEOUT_S,
    ENV_AI_API_KEY,
    ENV_AI_BASE_URL,
    ENV_STT_BASE_URL,
    ENV_STT_MODEL,
    ENV_VOICE_ENABLED,
    ENV_VOICE_INPUT_ENGINE,
    ENV_VOICE_MAX_SECONDS,
    ENV_VOICE_TIMEOUT_S,
    Settings,
    load_settings,
    secret_env_var_names,
)
from peeko.voice.audio import DEFAULT_MAX_SECONDS
from peeko.voice.input import SpeechRecognizer
from peeko.voice.providers import DEFAULT_TIMEOUT_S
from tests.conftest import TEST_API_KEY, TEST_BASE_URL, voice_settings

#: The environment variables Stage 4 introduces.
VOICE_ENV_VARS = (
    ENV_VOICE_ENABLED,
    ENV_VOICE_INPUT_ENGINE,
    ENV_STT_MODEL,
    ENV_STT_BASE_URL,
    ENV_VOICE_TIMEOUT_S,
    ENV_VOICE_MAX_SECONDS,
)


# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #
def test_the_documented_defaults_are_what_the_code_uses():
    settings = load_settings({})

    assert settings.voice_enabled is True          # on, but never listening
    assert settings.voice_input_engine == ""       # engine default
    assert settings.voice_stt_model == ""
    assert settings.voice_stt_base_url == ""
    assert settings.voice_timeout_s == 60.0
    assert settings.voice_max_seconds == 30.0


def test_the_defaults_are_the_same_constants_the_voice_package_uses():
    """One source of truth: settings and voice cannot drift apart."""
    assert DEFAULT_VOICE_TIMEOUT_S == DEFAULT_TIMEOUT_S == 60.0
    assert DEFAULT_VOICE_MAX_SECONDS == DEFAULT_MAX_SECONDS == 30.0

    settings = Settings()
    assert settings.voice_timeout_s == DEFAULT_TIMEOUT_S
    assert settings.voice_max_seconds == DEFAULT_MAX_SECONDS


def test_the_microphone_is_only_off_when_it_is_switched_off():
    """Default on is deliberate: nothing is captured until Mic is pressed."""
    assert Settings().voice_enabled is True
    assert load_settings({ENV_VOICE_ENABLED: "1"}).voice_enabled is True


# --------------------------------------------------------------------------- #
# Environment overrides
# --------------------------------------------------------------------------- #
def test_every_voice_variable_is_read_from_the_environment():
    settings = load_settings({
        ENV_VOICE_ENABLED: "1",
        ENV_VOICE_INPUT_ENGINE: "openai-compatible",
        ENV_STT_MODEL: "whisper-1",
        ENV_STT_BASE_URL: "https://gateway.invalid/v1",
        ENV_VOICE_TIMEOUT_S: "45",
        ENV_VOICE_MAX_SECONDS: "12.5",
    })

    assert settings.voice_enabled is True
    assert settings.voice_input_engine == "openai-compatible"
    assert settings.voice_stt_model == "whisper-1"
    assert settings.voice_stt_base_url == "https://gateway.invalid/v1"
    assert settings.voice_timeout_s == 45.0
    assert settings.voice_max_seconds == 12.5


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", ""])
def test_voice_can_be_switched_off_with_every_documented_spelling(value):
    assert load_settings({ENV_VOICE_ENABLED: value}).voice_enabled is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_voice_can_be_switched_on_with_every_documented_spelling(value):
    assert load_settings({ENV_VOICE_ENABLED: value}).voice_enabled is True


@pytest.mark.parametrize("value", ["soon", "12s", "-4", ""])
def test_an_unusable_number_falls_back_to_the_default_instead_of_crashing(
    value
):
    settings = load_settings({
        ENV_VOICE_TIMEOUT_S: value, ENV_VOICE_MAX_SECONDS: value,
    })
    assert settings.voice_timeout_s == DEFAULT_VOICE_TIMEOUT_S
    assert settings.voice_max_seconds == DEFAULT_VOICE_MAX_SECONDS


def test_the_voice_variables_are_documented_in_the_env_example():
    """Every variable the code reads is shown in .env.example."""
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / ".env.example"
    text = example.read_text(encoding="utf-8")
    for name in VOICE_ENV_VARS:
        assert name in text, f"{name} is missing from .env.example"


def test_a_dotenv_file_can_configure_voice(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join([
            f"{ENV_AI_API_KEY}={TEST_API_KEY}",
            f"{ENV_VOICE_ENABLED}=0",
            f"{ENV_VOICE_INPUT_ENGINE}=whisper",
            f"{ENV_STT_MODEL}=whisper-1",
            f"{ENV_VOICE_MAX_SECONDS}=20",
        ]),
        encoding="utf-8",
    )
    # The test environment is wiped by the hermetic conftest fixture; make
    # sure nothing in the real environment shadows the file either.
    for name in (ENV_VOICE_ENABLED, ENV_VOICE_INPUT_ENGINE, ENV_STT_MODEL,
                 ENV_VOICE_MAX_SECONDS, ENV_AI_API_KEY):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings(env=None, dotenv_path=env_file)

    assert settings.voice_enabled is False
    assert settings.voice_input_engine == "whisper"
    assert settings.voice_stt_model == "whisper-1"
    assert settings.voice_max_seconds == 20.0
    assert settings.ai_api_key == TEST_API_KEY


# --------------------------------------------------------------------------- #
# What is safe to show and log
# --------------------------------------------------------------------------- #
def test_the_key_is_never_in_the_displayed_configuration(tmp_path):
    settings = voice_settings(
        tmp_path, engine="openai", model="whisper-1", timeout_s=45.0,
        max_seconds=12.0,
    )
    shown = settings.public_dict()

    assert TEST_API_KEY not in repr(shown)
    assert shown["voice_enabled"] is True
    assert shown["voice_input_engine"] == "openai"
    assert shown["voice_stt_model"] == "whisper-1"
    assert shown["voice_timeout_s"] == 45.0
    assert shown["voice_max_seconds"] == 12.0


def test_an_unset_value_is_shown_as_the_documented_default(tmp_path):
    shown = voice_settings(tmp_path).public_dict()
    assert shown["voice_input_engine"] == "(default)"
    assert shown["voice_stt_model"] == "(engine default)"
    # Falls back to the AI base URL, which is what the recognizer will use.
    assert shown["voice_stt_base_url"] == TEST_BASE_URL


def test_the_secret_list_covers_the_ai_key_and_nothing_else():
    assert ENV_AI_API_KEY in secret_env_var_names()
    assert not set(VOICE_ENV_VARS) & set(secret_env_var_names())


def test_the_key_is_not_logged_when_settings_are_logged(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    settings = voice_settings(tmp_path, engine="openai")
    logging.getLogger("peeko").info("settings: %s", settings.public_dict())
    assert TEST_API_KEY not in caplog.text


# --------------------------------------------------------------------------- #
# The settings really reach the capture
# --------------------------------------------------------------------------- #
def test_voice_settings_drive_the_recognizer(tmp_path):
    settings = load_settings({
        ENV_AI_API_KEY: TEST_API_KEY,
        ENV_VOICE_INPUT_ENGINE: "openai-compatible",
        ENV_STT_MODEL: "whisper-1",
        ENV_STT_BASE_URL: "https://gateway.invalid/v1",
        ENV_AI_BASE_URL: "https://ignored.invalid/v1",
        ENV_VOICE_TIMEOUT_S: "9",
        ENV_VOICE_MAX_SECONDS: "7",
    })
    recognizer = SpeechRecognizer.from_settings(settings)

    assert recognizer.engine == "openai-compatible"
    assert recognizer.model == "whisper-1"
    assert recognizer.provider.base_url == "https://gateway.invalid/v1"
    assert recognizer.provider.timeout_s == 9.0
    assert recognizer.max_duration_s == 7.0


def test_switching_voice_off_is_visible_to_the_window(tmp_path):
    """The chat window reads this flag before it touches any audio device."""
    on = voice_settings(tmp_path, enabled=True)
    off = voice_settings(tmp_path, enabled=False)
    assert on.voice_enabled is True
    assert off.voice_enabled is False
