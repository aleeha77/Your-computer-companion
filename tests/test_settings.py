"""Tests for peeko.settings — defaults, env overrides, .env loading."""

from __future__ import annotations

import logging

from peeko.settings import (
    ENV_AI_MODEL,
    ENV_AI_PROVIDER,
    ENV_LOG_LEVEL,
    ENV_SMOKE_TEST,
    ENV_TTS_VOICE,
    Settings,
    load_settings,
    secret_env_var_names,
)


def test_defaults(monkeypatch):
    """No env at all -> documented defaults."""
    settings = load_settings({})
    assert settings.app_name == "Peeko"
    assert settings.log_level == "INFO"
    assert settings.smoke_test is False
    assert settings.ai_provider == "openai"
    assert settings.ai_api_key == ""
    assert settings.voice_input_engine == ""
    assert settings.tts_engine == ""


def test_env_overrides(monkeypatch):
    env = {
        ENV_LOG_LEVEL: "DEBUG",
        ENV_SMOKE_TEST: "1",
        ENV_AI_PROVIDER: "anthropic",
        ENV_AI_MODEL: "claude-3.5-sonnet",
        ENV_TTS_VOICE: "en-US-JennyNeural",
    }
    settings = load_settings(env)
    assert settings.log_level == "DEBUG"
    assert settings.log_level_int() == logging.DEBUG
    assert settings.smoke_test is True
    assert settings.ai_provider == "anthropic"
    assert settings.ai_model == "claude-3.5-sonnet"
    assert settings.tts_voice == "en-US-JennyNeural"


def test_bool_parsing_variants():
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        assert load_settings({ENV_SMOKE_TEST: truthy}).smoke_test is True
    for falsy in ("0", "false", "no", "off", ""):
        assert load_settings({ENV_SMOKE_TEST: falsy}).smoke_test is False


def test_invalid_log_level_falls_back_to_info():
    settings = load_settings({ENV_LOG_LEVEL: "super-verbose"})
    assert settings.log_level == "INFO"


def test_dotenv_file_is_loaded(tmp_path, monkeypatch):
    """A .env file supplies values when no real env var is set."""
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text(
        f"{ENV_AI_MODEL}=gpt-4o-mini\n{ENV_TTS_VOICE}=en-US-AriaNeural\n"
    )
    settings = load_settings(env=None, dotenv_path=dotenv_file)
    assert settings.ai_model == "gpt-4o-mini"
    assert settings.tts_voice == "en-US-AriaNeural"


def test_real_env_wins_over_dotenv(tmp_path, monkeypatch):
    """A real environment variable overrides the .env file."""
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text(f"{ENV_LOG_LEVEL}=DEBUG\n")
    monkeypatch.setenv(ENV_LOG_LEVEL, "WARNING")
    settings = load_settings(env=None, dotenv_path=dotenv_file)
    assert settings.log_level == "WARNING"


def test_paths_applied_from_env(isolated_dirs):
    settings = load_settings()  # no explicit env -> os.environ (isolated)
    data_dir, config_dir, log_dir = (
        isolated_dirs / "data",
        isolated_dirs / "config",
        isolated_dirs / "logs",
    )
    assert settings.data_dir == data_dir
    assert settings.config_dir == config_dir
    assert settings.log_dir == log_dir
    # dirs are created eagerly so logging can write immediately
    assert data_dir.is_dir()
    assert config_dir.is_dir()
    assert log_dir.is_dir()


def test_db_path_lives_in_data_dir(isolated_dirs):
    settings = load_settings({})
    assert settings.db_path == settings.data_dir / "peeko.db"


def test_secrets_never_serialized(monkeypatch):
    monkeypatch.setenv("PEEKO_AI_API_KEY", "sk-super-secret-123")
    settings = load_settings(env=None)
    public = settings.public_dict()
    assert "sk-super-secret-123" not in str(public)
    assert "ai_api_key" not in public
    assert "PEEKO_AI_API_KEY" in secret_env_var_names()


def test_settings_are_frozen():
    settings = load_settings({})
    try:
        settings.log_level = "DEBUG"  # type: ignore[misc]
    except Exception as exc:  # noqa: BLE001
        assert isinstance(exc, (AttributeError,))  # frozen dataclass
    else:
        raise AssertionError("Settings should be immutable (frozen)")