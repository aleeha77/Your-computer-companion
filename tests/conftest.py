"""Shared pytest fixtures — isolate Peeko from the developer's environment.

Besides hermetic environment handling, this module provides the two
helpers the Stage 1 avatar tests need:

* :func:`qapp` — a single **offscreen** ``QApplication`` for the whole
  session (Qt allows exactly one per process, and ``QPixmap``/``QPainter``
  require a running GUI application). The offscreen platform plugin makes
  this work on a headless machine.
* :func:`avatar_assets` — writes a throw-away asset manifest plus the SVG
  layer files it references into ``tmp_path``, so validation and rendering
  tests never touch the packaged artwork.

The Qt fixtures are only instantiated by tests that ask for them, so the
pure-Python tests stay free of any GUI dependency.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

#: Every PEEKO_* env var we manage, so tests are hermetic.
PEEKO_ENV_VARS = [
    "PEEKO_LOG_LEVEL",
    "PEEKO_SMOKE_TEST",
    "PEEKO_DATA_DIR",
    "PEEKO_CONFIG_DIR",
    "PEEKO_LOG_DIR",
    "PEEKO_AVATAR_ASSETS_DIR",
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


# --------------------------------------------------------------------------- #
# Qt
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def qapp():
    """One offscreen ``QApplication`` shared by every GUI test.

    Session-scoped on purpose: creating a second ``QApplication`` in the
    same process is a hard Qt error, and creating it per test would slow
    the suite down for no benefit.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture()
def qapp_offscreen_env(monkeypatch):
    """Force the offscreen platform for tests that spawn subprocesses."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return "offscreen"


# --------------------------------------------------------------------------- #
# Avatar asset fixtures
# --------------------------------------------------------------------------- #
#: Canvas size the generated test artwork is authored at.
AVATAR_CANVAS = {"width": 160, "height": 180}

#: Distinct fill per generated layer file, so tests can tell layers apart.
_LAYER_COLOURS = (
    "#ff0000", "#00ff00", "#0000ff", "#ffff00",
    "#ff00ff", "#00ffff", "#808080", "#ff8000",
)


def _svg(width: int, height: int, colour: str) -> str:
    """A minimal, valid SVG of exactly ``width`` x ``height`` pixels."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">'
        f'<rect x="10" y="10" width="60" height="60" fill="{colour}"/>'
        f"</svg>"
    )


def _referenced_files(node) -> set[str]:
    """Every ``*.svg`` path mentioned anywhere in a manifest structure."""
    found: set[str] = set()

    def walk(value) -> None:
        if isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str) and value.endswith(".svg"):
            found.add(value)

    walk(node)
    return found


def write_avatar_assets(root: Path, manifest: dict) -> Path:
    """Write ``manifest`` and every SVG it references under ``root``.

    Returns the path of the written ``manifest.json``. Layer files that
    are already present are left alone, so a test can pre-create a broken
    file and have it survive.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    canvas = manifest.get("canvas") or {}
    width = canvas.get("width", AVATAR_CANVAS["width"])
    height = canvas.get("height", AVATAR_CANVAS["height"])

    for index, rel in enumerate(sorted(_referenced_files(manifest))):
        path = root / rel
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _svg(width, height, _LAYER_COLOURS[index % len(_LAYER_COLOURS)]),
            encoding="utf-8",
        )

    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def minimal_manifest() -> dict:
    """A fresh, valid manifest describing the eight Stage 1 states.

    Durations are deliberately short (100 ms) so the timer-driven state
    machine can be tested by feeding it synthetic time instead of waiting.
    """
    return {
        "schema_version": 1,
        "canvas": dict(AVATAR_CANVAS),
        "z_order": ["body", "eyes"],
        "layer_defaults": {
            "body": "layers/body.svg",
            "eyes": "layers/eyes_open.svg",
        },
        "animations": {
            "idle": {
                "loop": True,
                "frames": [
                    {"duration_ms": 100},
                    {"dy": 2, "duration_ms": 100},
                ],
            },
            "blink": {
                "loop": False,
                "frames": [
                    {"eyes": "layers/eyes_closed.svg", "duration_ms": 50},
                    {"eyes": "layers/eyes_open.svg", "duration_ms": 50},
                ],
            },
            "look_left": {
                "loop": False,
                "frames": [{"eyes": "layers/eyes_left.svg", "duration_ms": 100}],
            },
            "look_right": {
                "loop": False,
                "frames": [{"eyes": "layers/eyes_right.svg", "duration_ms": 100}],
            },
            "look_up": {
                "loop": False,
                "frames": [{"eyes": "layers/eyes_up.svg", "duration_ms": 100}],
            },
            "look_down": {
                "loop": False,
                "frames": [{"eyes": "layers/eyes_down.svg", "duration_ms": 100}],
            },
            "click": {
                "loop": False,
                "frames": [
                    {"eyes": "layers/eyes_happy.svg", "dy": -4, "duration_ms": 100},
                    {"eyes": "layers/eyes_open.svg", "duration_ms": 100},
                ],
            },
            "dragging": {
                "loop": True,
                "frames": [
                    {"dy": 2, "duration_ms": 100},
                    {"dy": 4, "duration_ms": 100},
                ],
            },
        },
    }


@pytest.fixture()
def manifest_data() -> dict:
    """A fresh mutable copy of :func:`minimal_manifest` for one test."""
    return minimal_manifest()


@pytest.fixture()
def avatar_assets(tmp_path, manifest_data) -> Path:
    """Path of a throw-away, valid ``manifest.json`` (plus its SVG files)."""
    return write_avatar_assets(tmp_path / "assets", manifest_data)
