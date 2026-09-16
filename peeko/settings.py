"""Application configuration for Peeko.

Settings are read from, in increasing priority order:

1. Built-in defaults (the dataclass defaults below).
2. An optional ``.env`` file next to the working directory (loaded with
   python-dotenv, never overriding real environment variables).
3. Real environment variables (``PEEKO_*``).

The result is a plain ``Settings`` dataclass the rest of the app consumes.

.. note::
   Several fields describe features that arrive in later stages
   (AI chat, voice input, TTS). They are read from the environment today
   so the configuration surface is stable, but they have **no effect**
   until those stages land. They are never logged.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

from peeko import __app_name__, __version__
from peeko.paths import ensure_dirs, get_config_dir, get_data_dir, get_log_dir

# ---------------------------------------------------------------------------
# Env var names (single source of truth for the .env.example writers)
# ---------------------------------------------------------------------------
ENV_LOG_LEVEL = "PEEKO_LOG_LEVEL"
ENV_SMOKE_TEST = "PEEKO_SMOKE_TEST"
ENV_AI_PROVIDER = "PEEKO_AI_PROVIDER"
ENV_AI_MODEL = "PEEKO_AI_MODEL"
ENV_AI_API_KEY = "PEEKO_AI_API_KEY"
ENV_VOICE_INPUT_ENGINE = "PEEKO_VOICE_INPUT_ENGINE"
ENV_TTS_ENGINE = "PEEKO_TTS_ENGINE"
ENV_TTS_VOICE = "PEEKO_TTS_VOICE"

_VALID_LOG_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}

#: Environment variables that would leak secrets if ever printed/logged.
_SECRET_ENV_VARS = (ENV_AI_API_KEY,)


def _as_bool(value: str) -> bool:
    """Parse a boolean-ish env var value the way shell users expect."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Immutable application settings.

    Attributes with "stage" notes are configuration surface for future
    stages; they are stored but have no effect until implemented.
    """

    # -- identity ---------------------------------------------------------
    app_name: str = __app_name__
    version: str = __version__

    # -- paths ------------------------------------------------------------
    data_dir: Path = field(default_factory=lambda: get_data_dir())
    config_dir: Path = field(default_factory=lambda: get_config_dir())
    log_dir: Path = field(default_factory=lambda: get_log_dir())

    # -- behaviour ---------------------------------------------------------
    log_level: str = "INFO"
    smoke_test: bool = False  # auto-quit ~2 s after startup (headless CI)

    # -- Stage 3: AI chat (not implemented yet; config only) ---------------
    ai_provider: str = "openai"
    ai_model: str = ""
    ai_api_key: str = ""  # SECRET — never log it

    # -- Stage 4: voice input (not implemented yet; config only) -----------
    voice_input_engine: str = ""

    # -- Stage 5: text-to-speech (not implemented yet; config only) --------
    tts_engine: str = ""
    tts_voice: str = ""

    # ------------------------------------------------------------------
    def log_level_int(self) -> int:
        """Return the configured log level as a logging level constant."""
        return _VALID_LOG_LEVELS.get(self.log_level, logging.INFO)

    @property
    def db_path(self) -> Path:
        """Path of the SQLite database file (used from Stage 4 onward)."""
        return self.data_dir / "peeko.db"

    def public_dict(self) -> dict:
        """Settings with secret values removed — safe for logging/reporting."""
        base = {
            "app_name": self.app_name,
            "version": self.version,
            "data_dir": str(self.data_dir),
            "config_dir": str(self.config_dir),
            "log_dir": str(self.log_dir),
            "log_level": self.log_level,
            "smoke_test": self.smoke_test,
            "ai_provider": self.ai_provider,
            "ai_model": self.ai_model,
            # ai_api_key intentionally omitted
            "voice_input_engine": self.voice_input_engine,
            "tts_engine": self.tts_engine,
            "tts_voice": self.tts_voice,
        }
        return base


def load_settings(env: Mapping[str, str] | None = None,
                  dotenv_path: str | Path | None = None) -> Settings:
    """Build :class:`Settings` from defaults + ``.env`` + environment.

    :param env: environment mapping (defaults to ``os.environ``).
    :param dotenv_path: explicit path to a ``.env`` file. When ``env`` is
        not provided, ``load_dotenv()`` is called first (searches from the
        working directory upward), so a developer-local ``.env`` is picked
        up without overriding real environment variables.
    """
    if env is None:
        load_dotenv(dotenv_path=dotenv_path, override=False)
        env = os.environ

    raw_level = env.get(ENV_LOG_LEVEL, "INFO")
    if raw_level.upper() not in _VALID_LOG_LEVELS:
        # Sane fallback instead of crashing on a typo.
        logging.getLogger(__name__).warning(
            "Unknown %s=%r — falling back to INFO", ENV_LOG_LEVEL, raw_level
        )
        raw_level = "INFO"

    data_dir = get_data_dir(env)
    config_dir = get_config_dir(env)
    log_dir = get_log_dir(env, data_dir=data_dir)
    ensure_dirs(env)

    return Settings(
        data_dir=data_dir,
        config_dir=config_dir,
        log_dir=log_dir,
        log_level=raw_level.upper(),
        smoke_test=_as_bool(env.get(ENV_SMOKE_TEST, "0")),
        ai_provider=env.get(ENV_AI_PROVIDER, "openai"),
        ai_model=env.get(ENV_AI_MODEL, ""),
        ai_api_key=env.get(ENV_AI_API_KEY, ""),
        voice_input_engine=env.get(ENV_VOICE_INPUT_ENGINE, ""),
        tts_engine=env.get(ENV_TTS_ENGINE, ""),
        tts_voice=env.get(ENV_TTS_VOICE, ""),
    )


def secret_env_var_names() -> tuple[str, ...]:
    """Names of env vars whose values must never be logged."""
    return _SECRET_ENV_VARS