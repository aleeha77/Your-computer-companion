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
    "PEEKO_VOICE_ENABLED",
    "PEEKO_VOICE_INPUT_ENGINE",
    "PEEKO_STT_MODEL",
    "PEEKO_STT_BASE_URL",
    "PEEKO_VOICE_TIMEOUT_S",
    "PEEKO_VOICE_MAX_SECONDS",
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
            # -- Stage 4 sustained state ----------------------------------- #
            "listening": {
                "loop": True,
                "frames": [
                    {"eyes": "layers/eyes_up.svg", "dy": -2, "duration_ms": 100},
                    {"eyes": "layers/eyes_open.svg", "duration_ms": 100},
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


# --------------------------------------------------------------------------- #
# Stage 4: voice-input fakes — no microphone, no socket, no sounddevice
# --------------------------------------------------------------------------- #
#: Samples per second the fakes produce (Peeko's real capture rate).
FAKE_SAMPLE_RATE = 16_000


def pcm_samples(frames: int = 1_600, *, amplitude: int = 0) -> bytes:
    """``frames`` frames of 16-bit mono PCM at ``amplitude``.

    ``amplitude=0`` is digital silence; anything above ~100 raises
    :meth:`peeko.voice.audio.AudioClip.rms_level` above zero, which is how
    the level-callback tests tell "quiet" from "talking".
    """
    sample = int(amplitude).to_bytes(2, "little", signed=True)
    return sample * int(frames)


class FakeAudioSource:
    """A scripted microphone: hands out queued chunks and records the calls.

    It satisfies :class:`peeko.voice.audio.AudioSource`, so the *whole* real
    capture path (``open`` → ``read`` → ``close``, cancellation, level
    callbacks, duration capping) runs exactly as it does through
    ``sounddevice`` — on a machine with no microphone at all.
    """

    name = "fake"

    def __init__(self, chunks=None, *,
                 sample_rate: int = FAKE_SAMPLE_RATE,
                 channels: int = 1,
                 reason: str = "",
                 open_error: BaseException | None = None,
                 read_error: BaseException | None = None,
                 on_read=None) -> None:
        self.chunks = (
            [pcm_samples(1_600, amplitude=1_200)]
            if chunks is None else list(chunks)
        )
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        #: Non-empty makes this source unavailable — exactly as an honest
        #: source reports why it cannot capture.
        self.reason = reason
        self.open_error = open_error
        self.read_error = read_error
        #: Optional ``callable(read_number)`` — used to cancel mid-capture.
        self.on_read = on_read
        self.opened = False
        self.open_calls = 0
        self.closed = 0
        self.reads = 0

    def availability(self) -> str:
        return self.reason

    def open(self) -> None:
        self.open_calls += 1
        if self.open_error is not None:
            raise self.open_error
        self.opened = True

    def read(self) -> bytes:
        self.reads += 1
        if self.on_read is not None:
            self.on_read(self.reads)
        if self.read_error is not None:
            raise self.read_error
        return self.chunks.pop(0) if self.chunks else b""

    def close(self) -> None:
        self.opened = False
        self.closed += 1

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"FakeAudioSource(chunks={len(self.chunks)}, "
            f"reads={self.reads}, closed={self.closed})"
        )


class _FakeArray:
    """The smallest thing ``sounddevice`` returns that Peeko can use.

    The real library hands back a numpy array; Peeko only ever calls
    ``tobytes()`` on it, so that (plus the buffer protocol through
    ``bytes()``) is all this stand-in implements.
    """

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def tobytes(self) -> bytes:
        return self._payload

    def __bytes__(self) -> bytes:
        return self._payload


class FakeInputStream:
    """A stand-in for ``sounddevice.InputStream`` (records its lifecycle)."""

    def __init__(self, *, chunks=None, **kwargs) -> None:
        self.kwargs = dict(kwargs)
        self.chunks = [pcm_samples(1_024, amplitude=1_200)] if chunks is None \
            else list(chunks)
        self.started = False
        self.stopped = 0
        self.closed = 0
        self.overflowed = False
        self.start_error: BaseException | None = None
        self.read_error: BaseException | None = None

    def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    def read(self, _frames: int):
        """Return ``(array, overflowed)`` — the shape sounddevice uses."""
        if self.read_error is not None:
            raise self.read_error
        payload = self.chunks.pop(0) if self.chunks else b""
        return _FakeArray(payload), self.overflowed

    def stop(self) -> None:
        self.stopped += 1

    def close(self) -> None:
        self.closed += 1


class FakeSoundDeviceModule:
    """A minimal stand-in for the optional ``sounddevice`` library.

    Injected into :class:`peeko.voice.audio.SoundDeviceAudioSource` so the
    real source's logic (device check, lazy import, error classification,
    stream lifecycle) is tested without PortAudio and without the library
    being installed — it deliberately is **not** a dependency of this
    project's test environment.
    """

    def __init__(self, *, chunks=None, query_error: BaseException | None = None,
                 open_error: BaseException | None = None,
                 start_error: BaseException | None = None,
                 read_error: BaseException | None = None) -> None:
        self.chunks = chunks
        self.query_error = query_error
        self.open_error = open_error
        self.start_error = start_error
        self.read_error = read_error
        self.device_queries: list[dict] = []
        self.instances: list[FakeInputStream] = []

    def query_devices(self, **kwargs):
        self.device_queries.append(dict(kwargs))
        if self.query_error is not None:
            raise self.query_error
        return [{"name": "Fake microphone", "max_input_channels": 1}]

    def InputStream(self, **kwargs) -> FakeInputStream:  # noqa: N802 - library API
        if self.open_error is not None:
            raise self.open_error
        stream = FakeInputStream(chunks=self.chunks, **kwargs)
        stream.start_error = self.start_error
        stream.read_error = self.read_error
        self.instances.append(stream)
        return stream


class FakeSTTTransport:
    """A recording stand-in for the transcription HTTP transport.

    Nothing here opens a socket: it records the request Peeko would really
    have sent and answers with a canned body (or raises a canned error).
    """

    def __init__(self, body: str = "", *, error: BaseException | None = None,
                 delay_s: float = 0.0) -> None:
        self.body = body
        self.error = error
        self.delay_s = float(delay_s)
        self.calls: list[dict] = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({
            "url": url,
            "headers": dict(headers),
            "body": body,
            "timeout": timeout,
        })
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        return self.body

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last_call(self) -> dict:
        return self.calls[-1]

    @property
    def sent_body(self) -> bytes:
        """The multipart body of the last request (``b""`` if none)."""
        return self.calls[-1]["body"] if self.calls else b""

    @property
    def sent_audio(self) -> bool:
        """Whether the upload really carried a WAV payload."""
        return b"RIFF" in self.sent_body


def transcription_body(text: str, **extra) -> str:
    """An OpenAI-compatible transcription body carrying ``text``."""
    payload: dict = {"text": text}
    payload.update(extra)
    return json.dumps(payload)


def voice_settings(tmp_path, *, key: str = TEST_API_KEY, engine: str = "",
                   model: str = "", base_url: str = "", enabled: bool = True,
                   timeout_s: float | None = None,
                   max_seconds: float | None = None, **overrides):
    """Real settings pointed at a throw-away directory and a test voice config.

    ``key`` is deliberately the *same* ``PEEKO_AI_API_KEY`` the chat uses,
    because that is the design: one key configures both features (see
    :meth:`peeko.voice.input.SpeechRecognizer.from_settings`).
    """
    from peeko.settings import Settings

    values = dict(
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        log_dir=tmp_path / "logs",
        ai_provider="openai-compatible",
        ai_model="test-model",
        ai_api_key=key,
        ai_base_url=TEST_BASE_URL,
        voice_enabled=enabled,
        voice_input_engine=engine,
        voice_stt_model=model,
        voice_stt_base_url=base_url,
    )
    if timeout_s is not None:
        values["voice_timeout_s"] = timeout_s
    if max_seconds is not None:
        values["voice_max_seconds"] = max_seconds
    values.update(overrides)
    return Settings(**values)


def voice_recognizer(*, chunks=None, text: str = "hello there",
                     provider=None, source=None, **kwargs):
    """A real :class:`peeko.voice.input.SpeechRecognizer`, fully faked.

    A :class:`FakeAudioSource` stands in for the microphone and a
    :class:`peeko.voice.providers.MockTranscriptionProvider` for the service,
    so the complete capture → transcribe path runs with no hardware, no
    network and no API key.
    """
    from peeko.voice.input import SpeechRecognizer
    from peeko.voice.providers import MockTranscriptionProvider

    source = FakeAudioSource(chunks) if source is None else source
    provider = (MockTranscriptionProvider(text) if provider is None
                else provider)
    return SpeechRecognizer(source=source, provider=provider, **kwargs)


def synchronous_submit_capture(*, raise_unexpected: bool = False):
    """A drop-in for ``peeko.voice.worker.submit_capture`` that runs inline.

    The capture itself runs on the caller's thread (so there is no thread pool
    and no real microphone in a UI test), but the worker's signals are
    delivered through the Qt event loop on a zero-delay timer — the same
    *ordering* the real worker's queued signal delivery produces, where the
    window has already entered its listening state before a result arrives.

    Tests therefore assert the outcome after ``wait_for(...)``/
    ``app.processEvents()``.
    """
    from PySide6.QtCore import QTimer

    from peeko.voice.errors import VoiceError

    started: list[bool] = []

    def submit(recognizer, *, signals=None, max_duration_s=None, pool=None):
        started.append(True)

        def deliver() -> None:
            try:
                text = recognizer.listen()
            except VoiceError as exc:
                signals.failed.emit(exc.message)
            except Exception as exc:  # noqa: BLE001 - mirrors the worker
                if raise_unexpected:
                    raise
                signals.failed.emit(f"unexpected: {exc}")
            else:
                if text:
                    signals.transcribed.emit(text)
                else:
                    signals.empty.emit()

        QTimer.singleShot(0, deliver)
        return "inline-task"

    submit.started = started  # type: ignore[attr-defined]
    return submit
