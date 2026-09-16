"""Shared pytest fixtures — isolate Peeko from the developer's environment."""

from __future__ import annotations

import os

import pytest

#: Every PEEKO_* env var we manage, so tests are hermetic.
PEEKO_ENV_VARS = [
    "PEEKO_LOG_LEVEL",
    "PEEKO_SMOKE_TEST",
    "PEEKO_DATA_DIR",
    "PEEKO_CONFIG_DIR",
    "PEEKO_LOG_DIR",
    "PEEKO_AI_PROVIDER",
    "PEEKO_AI_MODEL",
    "PEEKO_AI_API_KEY",
    "PEEKO_VOICE_INPUT_ENGINE",
    "PEEKO_TTS_ENGINE",
    "PEEKO_TTS_VOICE",
]


@pytest.fixture(autouse=True)
def _clean_peeko_env(monkeypatch):
    """Remove Peeko env vars before each test; restore afterwards."""
    for name in PEEKO_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture()
def isolated_dirs(tmp_path, monkeypatch):
    """Point all Peeko directories at a tmp dir for the duration of a test.

    Returns the tmp_path so tests can inspect what was created.
    """
    monkeypatch.setenv("PEEKO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PEEKO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("PEEKO_LOG_DIR", str(tmp_path / "logs"))
    return tmp_path