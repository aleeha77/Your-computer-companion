"""Application configuration for Peeko.

Settings are read from, in increasing priority order:

1. Built-in defaults (the dataclass defaults below).
2. An optional ``.env`` file next to the working directory (loaded with
   python-dotenv, never overriding real environment variables).
3. Real environment variables (``PEEKO_*``).

The result is a plain ``Settings`` dataclass the rest of the app consumes.

.. note::
   The ``ai_*`` fields are live as of Stage 3 (they configure the chat window)
   and the ``voice_*`` fields are live as of Stage 4 (they configure the Mic
   button in that window). Fields for features that arrive in later stages
   (TTS) are read from the environment today so the configuration surface is
   stable, but they have **no effect** until those stages land. Secret values
   are never logged (see :func:`secret_env_var_names`).
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
ENV_AVATAR_ASSETS_DIR = "PEEKO_AVATAR_ASSETS_DIR"
ENV_AI_PROVIDER = "PEEKO_AI_PROVIDER"
ENV_AI_MODEL = "PEEKO_AI_MODEL"
ENV_AI_API_KEY = "PEEKO_AI_API_KEY"
ENV_AI_BASE_URL = "PEEKO_AI_BASE_URL"
ENV_AI_TIMEOUT_S = "PEEKO_AI_TIMEOUT_S"
ENV_VOICE_ENABLED = "PEEKO_VOICE_ENABLED"
ENV_VOICE_INPUT_ENGINE = "PEEKO_VOICE_INPUT_ENGINE"
ENV_STT_MODEL = "PEEKO_STT_MODEL"
ENV_STT_BASE_URL = "PEEKO_STT_BASE_URL"
ENV_VOICE_TIMEOUT_S = "PEEKO_VOICE_TIMEOUT_S"
ENV_VOICE_MAX_SECONDS = "PEEKO_VOICE_MAX_SECONDS"
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

#: Defaults for the voice-input settings (kept here so the settings view and
#: the voice package can never drift apart).
DEFAULT_VOICE_MAX_SECONDS = 30.0
DEFAULT_VOICE_TIMEOUT_S = 60.0


def _as_bool(value: str) -> bool:
    """Parse a boolean-ish env var value the way shell users expect."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_optional_path(value: str) -> Path | None:
    """An empty/absent setting means "use the built-in default"."""
    value = value.strip()
    return Path(value).expanduser() if value else None


def _as_float(value: str, default: float) -> float:
    """Parse a float setting, falling back to a sane default on a typo.

    A non-numeric value must not stop Peeko from starting; the fallback is
    logged by the caller (see :func:`load_settings`).
    """
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


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

    # -- Stage 1: avatar artwork -------------------------------------------
    #: Folder holding ``manifest.json`` + ``layers/*.svg``. ``None`` means
    #: "use the artwork packaged inside ``peeko/avatar/assets``", so the
    #: owner can point Peeko at their own art folder instead of editing
    #: the installed package.
    avatar_assets_dir: Path | None = None

    # -- Stage 3: AI chat --------------------------------------------------
    #: Provider the chat window talks to (``openai`` or
    #: ``openai-compatible`` — see :mod:`peeko.ai.providers`).
    ai_provider: str = "openai"
    #: Chat model name, e.g. ``gpt-4o-mini``.
    ai_model: str = ""
    ai_api_key: str = ""  # SECRET — never log it
    #: API root; empty means the provider's documented default (any
    #: OpenAI-compatible endpoint can be pointed at instead).
    ai_base_url: str = ""
    #: How long to wait for an answer before giving up, in seconds.
    ai_timeout_s: float = 30.0

    # -- Stage 4: voice input (microphone -> text) -------------------------
    #: Master switch for the microphone. **Off** means Peeko never opens an
    #: audio device at all; the chat window says so instead of showing a dead
    #: button. Default on: capturing only ever starts when you click Mic.
    voice_enabled: bool = True
    #: Speech-to-text engine: ``openai``/``openai-compatible`` (or empty for
    #: the default). An unknown value is reported honestly, never guessed at.
    voice_input_engine: str = ""
    #: Transcription model, e.g. ``whisper-1`` (empty = the engine default).
    voice_stt_model: str = ""
    #: Speech-to-text API root; empty falls back to ``ai_base_url`` and then to
    #: the engine's documented default, so one gateway can be set just once.
    voice_stt_base_url: str = ""
    #: How long to wait for a transcription before giving up, in seconds.
    voice_timeout_s: float = DEFAULT_VOICE_TIMEOUT_S
    #: Hard cap on a single recording, in seconds (a forgotten open
    #: microphone stops itself).
    voice_max_seconds: float = DEFAULT_VOICE_MAX_SECONDS

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
            "avatar_assets_dir": (
                str(self.avatar_assets_dir) if self.avatar_assets_dir else None
            ),
            "ai_provider": self.ai_provider,
            "ai_model": self.ai_model,
            "ai_base_url": self.ai_base_url or "(provider default)",
            "ai_timeout_s": self.ai_timeout_s,
            # ai_api_key intentionally omitted
            "voice_enabled": self.voice_enabled,
            "voice_input_engine": self.voice_input_engine or "(default)",
            "voice_stt_model": self.voice_stt_model or "(engine default)",
            "voice_stt_base_url": (
                self.voice_stt_base_url
                or self.ai_base_url
                or "(engine default)"
            ),
            "voice_timeout_s": self.voice_timeout_s,
            "voice_max_seconds": self.voice_max_seconds,
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
        avatar_assets_dir=_as_optional_path(env.get(ENV_AVATAR_ASSETS_DIR, "")),
        ai_provider=env.get(ENV_AI_PROVIDER, "openai"),
        ai_model=env.get(ENV_AI_MODEL, ""),
        ai_api_key=env.get(ENV_AI_API_KEY, ""),
        ai_base_url=env.get(ENV_AI_BASE_URL, ""),
        ai_timeout_s=_as_float(env.get(ENV_AI_TIMEOUT_S, ""), 30.0),
        voice_enabled=_as_bool(env.get(ENV_VOICE_ENABLED, "1")),
        voice_input_engine=env.get(ENV_VOICE_INPUT_ENGINE, ""),
        voice_stt_model=env.get(ENV_STT_MODEL, ""),
        voice_stt_base_url=env.get(ENV_STT_BASE_URL, ""),
        voice_timeout_s=_as_float(
            env.get(ENV_VOICE_TIMEOUT_S, ""), DEFAULT_VOICE_TIMEOUT_S
        ),
        voice_max_seconds=_as_float(
            env.get(ENV_VOICE_MAX_SECONDS, ""), DEFAULT_VOICE_MAX_SECONDS
        ),
        tts_engine=env.get(ENV_TTS_ENGINE, ""),
        tts_voice=env.get(ENV_TTS_VOICE, ""),
    )


def secret_env_var_names() -> tuple[str, ...]:
    """Names of env vars whose values must never be logged."""
    return _SECRET_ENV_VARS