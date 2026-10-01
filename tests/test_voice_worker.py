"""The off-thread voice bridge (Stage 4): listening never freezes Peeko.

Capturing audio blocks for as long as the user talks, so it runs on a
:class:`~PySide6.QtCore.QThreadPool` worker that reports back through Qt
signals — exactly like the chat request in :mod:`peeko.ai.worker`. These tests
pin down the contract the chat window relies on:

* real words arrive as ``transcribed``; nothing heard arrives as ``empty``;
  a user stop arrives as ``cancelled``; a problem arrives as ``failed`` with a
  readable message (never an invented transcript);
* an unexpected exception is contained — a worker can never crash the app;
* ``submit_capture`` returns immediately, so the caller is never made to wait,
  and the UI thread keeps running (timers still fire) while a capture is in
  flight;
* a capture started on the pool can be cancelled from the UI thread and the
  cancellation really lands.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QThreadPool, QTimer

from peeko.voice.errors import STTProviderError, VoiceCaptureError
from peeko.voice.input import SpeechRecognizer
from peeko.voice.providers import MockTranscriptionProvider
from peeko.voice.worker import (
    UNEXPECTED_FAILURE,
    VoiceCaptureTask,
    VoiceWorkerSignals,
    submit_capture,
)
from tests.conftest import (
    TEST_API_KEY,
    FakeAudioSource,
    pcm_samples,
    voice_recognizer,
    wait_for,
)

HEARD = "peeko are you there"


def chunk(frames: int = 1_600, *, amplitude: int = 1_200) -> bytes:
    return pcm_samples(frames, amplitude=amplitude)


class Collector:
    """Collects what a worker emitted (plain callables, so no event loop)."""

    def __init__(self) -> None:
        self.transcripts: list[str] = []
        self.empties: list[bool] = []
        self.cancellations: list[bool] = []
        self.failures: list[str] = []
        self.signals = VoiceWorkerSignals()
        self.signals.transcribed.connect(self.transcripts.append)
        self.signals.empty.connect(lambda: self.empties.append(True))
        self.signals.cancelled.connect(lambda: self.cancellations.append(True))
        self.signals.failed.connect(self.failures.append)


class SlowRecognizer:
    """A recognizer that takes ``delay_s`` to "listen" (no microphone)."""

    def __init__(self, text: str = HEARD, *, delay_s: float = 0.0,
                 error: BaseException | None = None) -> None:
        self.text = text
        self.delay_s = delay_s
        self.error = error
        self.should_stop_seen: list[bool] = []

    def listen(self, *, should_stop=None, on_level=None):
        if should_stop is not None:
            self.should_stop_seen.append(should_stop())
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        return self.text


# --------------------------------------------------------------------------- #
# One task, run directly
# --------------------------------------------------------------------------- #
def test_a_transcript_is_reported():
    collector = Collector()
    VoiceCaptureTask(voice_recognizer(text=HEARD),
                     signals=collector.signals).run()

    assert collector.failures == []
    assert collector.transcripts == [HEARD]
    assert collector.empties == []


def test_nothing_heard_is_reported_as_empty_and_never_as_words():
    collector = Collector()
    VoiceCaptureTask(voice_recognizer(text=""),
                     signals=collector.signals).run()

    assert collector.transcripts == []
    assert collector.empties == [True]
    assert collector.failures == []


def test_a_failure_is_reported_as_a_readable_message():
    collector = Collector()
    recognizer = voice_recognizer(provider=MockTranscriptionProvider(
        error=STTProviderError("The speech service answered with HTTP 500.")
    ))
    VoiceCaptureTask(recognizer, signals=collector.signals).run()

    assert collector.transcripts == []
    assert collector.failures == [
        "The speech service answered with HTTP 500."
    ]


def test_an_unconfigured_recognizer_fails_honestly_without_opening_the_mic():
    source = FakeAudioSource([chunk(160)])
    collector = Collector()
    VoiceCaptureTask(SpeechRecognizer(source=source),
                     signals=collector.signals).run()

    assert source.open_calls == 0          # nothing was touched
    assert collector.failures == [
        "Voice input not configured — set PEEKO_AI_API_KEY in .env "
        "(the same key Peeko uses to chat)."
    ]


def test_an_unexpected_exception_is_contained_and_never_leaks_the_key(caplog):
    collector = Collector()
    VoiceCaptureTask(
        SlowRecognizer(error=RuntimeError(f"a wild bug ({TEST_API_KEY})")),
        signals=collector.signals,
    ).run()

    assert collector.transcripts == []
    assert collector.failures == [UNEXPECTED_FAILURE]
    assert TEST_API_KEY not in collector.failures[0]


def test_a_capture_error_reaches_the_ui_as_a_message():
    collector = Collector()
    recognizer = VoiceCaptureTask(
        SpeechRecognizer(
            source=FakeAudioSource(
                [chunk(160)],
                read_error=VoiceCaptureError(
                    "Voice input failed while reading from the microphone."
                ),
            ),
            provider=MockTranscriptionProvider(HEARD),
        ),
        signals=collector.signals,
    )
    recognizer.run()
    assert collector.failures == [
        "Voice input failed while reading from the microphone."
    ]


def test_the_task_passes_its_stop_flag_into_the_capture():
    recognizer = SlowRecognizer()
    task = VoiceCaptureTask(recognizer)
    task.run()
    assert recognizer.should_stop_seen == [False]
    assert task.is_cancelled is False


def test_a_capture_stopped_from_inside_the_microphone_reports_cancelled():
    """The exact production path: cancel → ``listen`` returns None."""
    provider = MockTranscriptionProvider(HEARD)
    source = FakeAudioSource([chunk(160) for _ in range(10)])
    recognizer = SpeechRecognizer(source=source, provider=provider)
    collector = Collector()
    task = VoiceCaptureTask(recognizer, signals=collector.signals)

    # As the chat window does: cancel while the capture is under way.
    source.on_read = lambda _n: task.cancel()
    task.run()

    assert collector.cancellations == [True]
    assert collector.transcripts == []
    assert collector.empties == []
    assert collector.failures == []
    assert provider.call_count == 0         # nothing was transcribed
    assert source.closed == 1


def test_a_pre_cancelled_task_reports_a_cancellation():
    provider = MockTranscriptionProvider(HEARD)
    source = FakeAudioSource([chunk(160)])
    collector = Collector()
    task = VoiceCaptureTask(
        SpeechRecognizer(source=source, provider=provider),
        signals=collector.signals,
    )
    task.cancel()
    task.run()

    assert collector.cancellations == [True]
    assert collector.transcripts == []
    assert collector.empties == []
    assert collector.failures == []
    assert source.reads == 0                # no audio was even read


def test_a_max_duration_override_reaches_the_recognizer():
    recognizer = voice_recognizer(text=HEARD)
    VoiceCaptureTask(recognizer, max_duration_s=3.5)
    assert recognizer.max_duration_s == 3.5


def test_the_signals_object_can_be_shared_and_a_default_is_available():
    assert VoiceCaptureTask(voice_recognizer()).signals is not None


# --------------------------------------------------------------------------- #
# The real thread pool: the UI thread is never made to wait
# --------------------------------------------------------------------------- #
def test_submit_capture_returns_at_once_even_when_listening_is_slow(qapp):
    """The whole point of the worker: the caller is never blocked."""
    collector = Collector()
    recognizer = SlowRecognizer(delay_s=0.5)

    started = time.monotonic()
    task = submit_capture(recognizer, signals=collector.signals)
    elapsed = time.monotonic() - started

    assert task is not None
    assert elapsed < 0.25, f"submit_capture blocked for {elapsed:.3f}s"
    assert collector.transcripts == []      # the words are still on their way

    assert wait_for(lambda: bool(collector.transcripts), timeout_s=5.0)
    assert collector.transcripts == [HEARD]
    QThreadPool.globalInstance().waitForDone(5000)


def test_a_capture_is_delivered_on_the_ui_thread(qapp):
    """Signals from the worker arrive through the event loop, not inline."""
    collector = Collector()
    seen_threads: list[int] = []
    collector.signals.transcribed.connect(
        lambda *_: seen_threads.append(threading.get_ident())
    )

    submit_capture(voice_recognizer(text=HEARD), signals=collector.signals)
    assert wait_for(lambda: bool(seen_threads), timeout_s=5.0)
    assert seen_threads == [threading.get_ident()]
    QThreadPool.globalInstance().waitForDone(5000)


def test_the_ui_thread_keeps_running_while_a_capture_is_in_flight(qapp):
    """No freeze: the animation timer keeps ticking mid-capture."""
    ticks: list[bool] = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()

    collector = Collector()
    try:
        submit_capture(SlowRecognizer(delay_s=0.3), signals=collector.signals)
        assert wait_for(lambda: bool(collector.transcripts), timeout_s=5.0)
    finally:
        timer.stop()
        QThreadPool.globalInstance().waitForDone(5000)

    assert len(ticks) >= 3, (
        "the event loop did not run while the microphone was open"
    )


def test_a_pool_failure_never_raises_into_the_ui_thread(qapp):
    collector = Collector()
    submit_capture(
        SlowRecognizer(error=VoiceCaptureError("no microphone")),
        signals=collector.signals,
    )
    assert wait_for(lambda: bool(collector.failures), timeout_s=5.0)
    assert collector.failures == ["no microphone"]
    QThreadPool.globalInstance().waitForDone(5000)


def test_a_capture_can_be_cancelled_from_the_ui_thread(qapp):
    """Clicking Mic again really stops a capture that is running elsewhere."""
    provider = MockTranscriptionProvider(HEARD)
    source = FakeAudioSource([chunk(1_600) for _ in range(20_000)])
    # The fake microphone behaves like a real one: a little audio arrives
    # every few milliseconds, so the capture is still running when the UI
    # thread asks it to stop (instead of finishing instantly on this CPU).
    source.on_read = lambda _n: time.sleep(0.01)
    recognizer = SpeechRecognizer(
        source=source, provider=provider, max_duration_s=600.0
    )
    collector = Collector()

    task = submit_capture(recognizer, signals=collector.signals)
    # Let the worker really get going, then stop it from the UI thread.
    assert wait_for(lambda: source.reads >= 1, timeout_s=5.0)
    task.cancel()

    assert wait_for(
        lambda: source.closed >= 1, timeout_s=10.0
    ), "the cancelled capture never released the microphone"
    QThreadPool.globalInstance().waitForDone(10000)

    assert provider.call_count == 0         # nothing was transcribed
    assert collector.transcripts == []
    assert source.closed >= 1
    assert task.is_cancelled is True
