"""Per-user file/directory paths for Peeko.

Uses `platformdirs` so Peeko stores its data in the correct location on
every OS (Windows: %LOCALAPPDATA%, macOS: ~/Library/Application Support,
Linux: ~/.local/share). No machine-specific paths are hard-coded.

Every lookup honours an environment override first, so users can relocate
Peeko's files (and tests can isolate them):

- ``PEEKO_DATA_DIR``   -> user data directory
- ``PEEKO_CONFIG_DIR`` -> user config directory
- ``PEEKO_LOG_DIR``    -> log directory (defaults to ``data_dir / "logs"``)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from platformdirs import user_config_dir, user_data_dir

_APP_NAME = "Peeko"
_APP_AUTHOR = "Peeko"

#: Environment variables that may override the default locations.
ENV_DATA_DIR = "PEEKO_DATA_DIR"
ENV_CONFIG_DIR = "PEEKO_CONFIG_DIR"
ENV_LOG_DIR = "PEEKO_LOG_DIR"


def get_data_dir(env: Mapping[str, str] | None = None) -> Path:
    """Return the user data directory for Peeko.

    :param env: environment mapping (defaults to ``os.environ``).
    """
    env = os.environ if env is None else env
    override = env.get(ENV_DATA_DIR)
    if override:
        return Path(override).expanduser().resolve()
    return Path(user_data_dir(_APP_NAME, _APP_AUTHOR))


def get_config_dir(env: Mapping[str, str] | None = None) -> Path:
    """Return the user config directory for Peeko."""
    env = os.environ if env is None else env
    override = env.get(ENV_CONFIG_DIR)
    if override:
        return Path(override).expanduser().resolve()
    return Path(user_config_dir(_APP_NAME, _APP_AUTHOR))


def get_log_dir(env: Mapping[str, str] | None = None,
                data_dir: Path | None = None) -> Path:
    """Return the log directory for Peeko (``data_dir / "logs"`` by default)."""
    env = os.environ if env is None else env
    override = env.get(ENV_LOG_DIR)
    if override:
        return Path(override).expanduser().resolve()
    base = data_dir if data_dir is not None else get_data_dir(env)
    return base / "logs"


def ensure_dirs(env: Mapping[str, str] | None = None) -> tuple[Path, Path, Path]:
    """Create (data, config, log) directories if needed and return them."""
    env = os.environ if env is None else env
    data_dir = get_data_dir(env)
    config_dir = get_config_dir(env)
    log_dir = get_log_dir(env, data_dir=data_dir)
    for path in (data_dir, config_dir, log_dir):
        path.mkdir(parents=True, exist_ok=True)
    return data_dir, config_dir, log_dir