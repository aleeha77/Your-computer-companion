"""Tests for peeko.paths — user directory resolution and overrides."""

from __future__ import annotations

from peeko.paths import (
    ENV_DATA_DIR,
    ENV_LOG_DIR,
    get_config_dir,
    get_data_dir,
    get_log_dir,
)


def test_default_data_dir_looks_like_platformdirs():
    """With no overrides, the data dir points into the platformdirs tree."""
    path = get_data_dir({})
    assert path.is_absolute()
    assert "Peeko" in str(path)


def test_config_dir_defaults_into_platformdirs():
    path = get_config_dir({})
    assert path.is_absolute()
    assert "Peeko" in str(path)


def test_log_dir_defaults_under_data_dir():
    log_dir = get_log_dir({}, data_dir=get_data_dir({}))
    assert log_dir == get_data_dir({}) / "logs"


def test_env_override_data_dir():
    env = {ENV_DATA_DIR: "/tmp/peeko-custom"}
    assert get_data_dir(env) == __import__("pathlib").Path(
        "/tmp/peeko-custom"
    ).resolve()


def test_env_override_log_dir():
    env = {ENV_LOG_DIR: "/tmp/peeko-my-logs"}
    assert get_log_dir(env) == __import__("pathlib").Path(
        "/tmp/peeko-my-logs"
    ).resolve()


def test_log_dir_wins_over_data_dir_default(monkeypatch):
    assert get_log_dir({}) != get_data_dir({})