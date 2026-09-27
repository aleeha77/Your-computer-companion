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
import time
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
    "PEEKO_AI_BASE_URL",
    "PEEKO_AI_TIMEOUT_S",
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
    """A fresh, valid manifest describing the Stage 1 + Stage 2 states.

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
            # -- Stage 2 reactions ----------------------------------------- #
            "hover": {
                "loop": False,
                "frames": [
                    {"eyes": "layers/eyes_half.svg", "dy": -2, "duration_ms": 100},
                    {"eyes": "layers/eyes_open.svg", "duration_ms": 100},
                ],
            },
            "double_click": {
                "loop": False,
                "frames": [
                    {"eyes": "layers/eyes_happy.svg", "dy": -6, "duration_ms": 100},
                    {"eyes": "layers/eyes_wink.svg", "dy": -3, "duration_ms": 100},
                    {"eyes": "layers/eyes_open.svg", "duration_ms": 100},
                ],
            },
            "confused": {
                "loop": False,
                "frames": [
                    {"eyes": "layers/eyes_half.svg", "dx": -2, "duration_ms": 100},
                    {"eyes": "layers/eyes_half.svg", "dx": 2, "duration_ms": 100},
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


# --------------------------------------------------------------------------- #
# Stage 3: AI chat fakes — the suite never touches the network or needs a key
# --------------------------------------------------------------------------- #
def completion_body(text: str, **extra) -> str:
    """An OpenAI-compatible chat-completions body carrying ``text``."""
    payload: dict = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text},
             "finish_reason": "stop"}
        ],
    }
    payload.update(extra)
    return json.dumps(payload)


class FakeTransport:
    """A recording stand-in for the HTTP transport.

    Records every request so a test can assert what Peeko would really have
    sent, and answers with a canned body (or raises a canned error). Nothing
    here opens a socket.
    """

    def __init__(self, body: str = "", *, error: BaseException | None = None,
                 delay_s: float = 0.0) -> None:
        self.body = body
        self.error = error
        self.delay_s = delay_s
        self.calls: list[dict] = []

    def __call__(self, url, headers, payload, timeout):
        self.calls.append({
            "url": url,
            "headers": dict(headers),
            "payload": payload,
            "timeout": timeout,
        })
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        return self.body

    # -- convenient views of the last request ---------------------------- #
    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last_call(self) -> dict:
        return self.calls[-1]

    @property
    def messages(self) -> list[dict]:
        """The messages of the last request (``[]`` if nothing was sent)."""
        if not self.calls:
            return []
        return list(self.calls[-1]["payload"].get("messages", []))

    @property
    def system_prompt(self) -> str:
        for message in self.messages:
            if message.get("role") == "system":
                return message.get("content", "")
        return ""


#: A key that must never appear in a log line, an error message or a repr.
TEST_API_KEY = "sk-test-not-a-real-key"

#: A base URL nobody can reach: any real request would fail loudly.
TEST_BASE_URL = "https://peeko-test.invalid/v1"


def ai_client(transport: FakeTransport, *, key: str = TEST_API_KEY,
              model: str = "test-model", provider: str = "openai-compatible"):
    """A real :class:`peeko.ai.client.AIClient` wired to a fake transport."""
    from peeko.ai.client import AIClient

    return AIClient(
        provider=provider, model=model, api_key=key,
        base_url=TEST_BASE_URL, transport=transport,
    )


def ai_settings(tmp_path, *, key: str = TEST_API_KEY, model: str = "test-model",
                **overrides):
    """Real settings pointed at a throw-away directory and a test AI config."""
    from peeko.settings import Settings

    values = dict(
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        log_dir=tmp_path / "logs",
        ai_provider="openai-compatible",
        ai_model=model,
        ai_api_key=key,
        ai_base_url=TEST_BASE_URL,
    )
    values.update(overrides)
    return Settings(**values)


def synchronous_submit(*, raise_unexpected: bool = False):
    """A drop-in for ``peeko.ai.worker.submit_reply`` that runs inline.

    Qt delivers the worker's signals straight to the window when both live on
    the same thread, so a UI test can assert the outcome immediately instead
    of waiting for a thread pool. It exercises the whole real path
    (prompt -> provider -> validator) apart from the threading itself, which
    :mod:`tests.test_ai_worker` covers separately.
    """
    from peeko.ai.errors import AIError

    started: list[bool] = []

    def submit(client, message, *, context=None, history=(), signals=None,
               options=None, pool=None):
        started.append(True)
        try:
            reply = client.respond(message, context=context, history=history)
        except AIError as exc:
            signals.failed.emit(exc.message)
        except Exception as exc:  # noqa: BLE001 - mirrors the worker contract
            if raise_unexpected:
                raise
            signals.failed.emit(f"unexpected: {exc}")
        else:
            signals.finished.emit(reply)
        return "inline-task"

    submit.started = started  # type: ignore[attr-defined]
    return submit


def wait_for(predicate, timeout_s: float = 5.0) -> bool:
    """Process Qt events until ``predicate()`` is true (or time runs out).

    Used by the offscreen UI tests that deliberately use the real thread
    pool: the reply is delivered through the event loop, exactly as it is in
    the running app.
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    if app is not None:
        app.processEvents()
    return bool(predicate())
