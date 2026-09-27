"""The off-thread bridge: AI work never blocks the UI (Stage 3).

A network request must never freeze the robot. The request therefore runs on
a :class:`~PySide6.QtCore.QThreadPool` worker that reports back through Qt
signals, which Qt delivers on the UI thread. These tests check the contract
the chat window relies on:

* a good answer arrives as a validated :class:`~peeko.ai.schema.AIResponse`;
* every failure arrives as an honest, key-free message;
* an unexpected exception is contained (the worker never crashes the app);
* ``submit_reply`` returns immediately — the caller is never made to wait.
"""

from __future__ import annotations

import json
import time

from PySide6.QtCore import QThreadPool

from peeko.ai.errors import AIProviderError
from peeko.ai.schema import AIResponse
from peeko.ai.worker import (
    AIReplyTask,
    AIWorkerSignals,
    UNEXPECTED_FAILURE,
    submit_reply,
)
from tests.conftest import (
    TEST_API_KEY,
    FakeTransport,
    ai_client,
    completion_body,
    wait_for,
)

GOOD_REPLY = json.dumps({
    "response": "Hello from Peeko!",
    "emotion": "playful",
    "animation": "talking_happy",
    "action": None,
})


class Collector:
    """Collects what a worker emitted (plain callables, so no event loop)."""

    def __init__(self) -> None:
        self.results: list[object] = []
        self.failures: list[str] = []
        self.signals = AIWorkerSignals()
        self.signals.finished.connect(self.results.append)
        self.signals.failed.connect(self.failures.append)


class BrokenClient:
    """A client whose transport blows up in an unexpected way."""

    def respond(self, *_args, **_kwargs):
        raise RuntimeError(f"a wild bug appears ({TEST_API_KEY})")


# --------------------------------------------------------------------------- #
# One task, run directly
# --------------------------------------------------------------------------- #
def test_a_good_answer_is_validated_before_it_is_reported():
    collector = Collector()
    client = ai_client(FakeTransport(completion_body(GOOD_REPLY)))
    AIReplyTask(client, "hi", signals=collector.signals).run()

    assert collector.failures == []
    assert len(collector.results) == 1
    reply = collector.results[0]
    assert isinstance(reply, AIResponse)
    assert reply.response == "Hello from Peeko!"
    assert reply.emotion == "playful"
    assert reply.animation == "talking_happy"
    assert reply.action is None


def test_a_failure_is_reported_as_a_readable_message():
    collector = Collector()
    client = ai_client(FakeTransport(error=TimeoutError("slow")))
    AIReplyTask(client, "hi", signals=collector.signals).run()

    assert collector.results == []
    assert len(collector.failures) == 1
    assert "did not answer within" in collector.failures[0]


def test_an_unconfigured_client_fails_honestly_without_a_request():
    transport = FakeTransport(completion_body(GOOD_REPLY))
    collector = Collector()
    AIReplyTask(ai_client(transport, key=""), "hi",
                signals=collector.signals).run()

    assert transport.call_count == 0
    assert collector.failures == [
        "AI not configured — set PEEKO_AI_API_KEY in .env"
    ]


def test_an_unexpected_exception_is_contained_and_never_leaks_the_key(caplog):
    collector = Collector()
    AIReplyTask(BrokenClient(), "hi", signals=collector.signals).run()

    assert collector.results == []
    assert collector.failures == [UNEXPECTED_FAILURE]
    assert TEST_API_KEY not in collector.failures[0]


def test_a_redacted_provider_error_reaches_the_ui_without_the_key():
    collector = Collector()
    client = ai_client(FakeTransport(error=RuntimeError(f"boom {TEST_API_KEY}")))
    AIReplyTask(client, "hi", signals=collector.signals).run()

    assert len(collector.failures) == 1
    assert TEST_API_KEY not in collector.failures[0]
    assert "[REDACTED]" in collector.failures[0]


def test_the_task_carries_the_context_and_history_it_was_given():
    transport = FakeTransport(completion_body(GOOD_REPLY))
    client = ai_client(transport)
    collector = Collector()
    AIReplyTask(
        client, "and now?",
        context={"emotion": "curious", "hunger": 12.5},
        history=[{"role": "user", "content": "hello"}],
        signals=collector.signals,
    ).run()

    assert [m["role"] for m in transport.messages] == [
        "system", "user", "user"
    ]
    prompt = transport.system_prompt
    assert '"hunger": 12.5' in prompt
    assert '"curious"' in prompt


# --------------------------------------------------------------------------- #
# The real thread pool: the UI thread is never made to wait
# --------------------------------------------------------------------------- #
def test_submit_reply_returns_at_once_even_when_the_service_is_slow(qapp):
    """The whole point of the worker: the caller is never blocked."""
    transport = FakeTransport(completion_body(GOOD_REPLY), delay_s=0.5)
    client = ai_client(transport)
    collector = Collector()
    collector.signals.finished.connect(lambda *_: None)

    started = time.monotonic()
    task = submit_reply(client, "hi", signals=collector.signals)
    elapsed = time.monotonic() - started

    assert task is not None
    assert elapsed < 0.25, f"submit_reply blocked for {elapsed:.3f}s"
    assert collector.results == []  # the answer is still on its way

    assert wait_for(lambda: bool(collector.results), timeout_s=5.0)
    assert isinstance(collector.results[0], AIResponse)
    QThreadPool.globalInstance().waitForDone(5000)


def test_a_reply_sent_through_the_pool_is_delivered_on_the_ui_thread(qapp):
    """Signals from the worker arrive through the event loop, not inline."""
    import threading

    client = ai_client(FakeTransport(completion_body(GOOD_REPLY)))
    collector = Collector()
    seen_threads: list[int] = []
    collector.signals.finished.connect(
        lambda *_: seen_threads.append(threading.get_ident())
    )

    submit_reply(client, "hi", signals=collector.signals)
    assert wait_for(lambda: bool(seen_threads), timeout_s=5.0)
    assert seen_threads == [threading.get_ident()]
    QThreadPool.globalInstance().waitForDone(5000)


def test_a_pool_failure_never_raises_into_the_ui_thread(qapp):
    client = ai_client(FakeTransport(error=AIProviderError("service down")))
    collector = Collector()
    submit_reply(client, "hi", signals=collector.signals)
    assert wait_for(lambda: bool(collector.failures), timeout_s=5.0)
    assert collector.failures == ["service down"]
    QThreadPool.globalInstance().waitForDone(5000)
