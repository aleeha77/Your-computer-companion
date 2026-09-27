"""Off-thread chat work: the UI never waits on the network (Stage 3).

The provider call is a blocking HTTP request, so it runs on a
:class:`~PySide6.QtCore.QThreadPool` worker and reports back through Qt
signals. The window stays completely responsive while Peeko is thinking
(the animation keeps ticking, you can keep typing, you can close the
window), and a failure arrives as an honest message rather than a freeze or
a made-up reply.

Nothing here inspects the *content* of an AI reply: the task only carries
the already-validated :class:`peeko.ai.schema.AIResponse` back to the UI
thread.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping, Sequence

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from peeko.ai.client import AIClient
from peeko.ai.context import ChatContext
from peeko.ai.errors import AIError
from peeko.ai.schema import AIResponse

LOG = logging.getLogger("peeko.ai")

#: Shown when something unexpected (not an :class:`AIError`) breaks.
UNEXPECTED_FAILURE = (
    "Peeko hit an unexpected problem talking to the AI service. "
    "Check the log for details."
)


class AIWorkerSignals(QObject):
    """Signals emitted from the worker thread, delivered on the UI thread.

    Keep one instance alive for as long as tasks using it are running (the
    window holds it); a task only emits, never owns.
    """

    #: ``AIResponse`` — a validated reply.
    finished = Signal(object)
    #: ``str`` — a user-facing failure message (never contains the key).
    failed = Signal(str)


class AIReplyTask(QRunnable):
    """One question for the AI, executed off the UI thread.

    :param client: the configured :class:`~peeko.ai.client.AIClient`.
    :param user_message: what the user typed.
    :param context: structured context (:class:`peeko.ai.context.ChatContext`
        or an equivalent mapping).
    :param history: previous turns (``{"role": ..., "content": ...}``).
    :param signals: emitter shared with the owning window.
    """

    def __init__(self, client: AIClient, user_message: str, *,
                 context: ChatContext | Mapping[str, Any] | None = None,
                 history: Iterable[Mapping[str, Any]] = (),
                 signals: AIWorkerSignals | None = None,
                 options: Mapping[str, Any] | None = None) -> None:
        super().__init__()
        self._client = client
        self._user_message = user_message
        self._context = context
        self._history = tuple(history or ())
        self.signals = signals or AIWorkerSignals()
        self._options = dict(options or {})
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D102 - QRunnable entry point
        try:
            reply: AIResponse = self._client.respond(
                self._user_message, context=self._context,
                history=self._history, **self._options,
            )
        except AIError as exc:
            LOG.warning("AI chat failed: %s", exc.message)
            self.signals.failed.emit(exc.message)
        except Exception:  # noqa: BLE001 - never let a worker crash the app
            LOG.exception("Unexpected AI chat failure")
            self.signals.failed.emit(UNEXPECTED_FAILURE)
        else:
            self.signals.finished.emit(reply)


def submit_reply(client: AIClient, user_message: str, *,
                 context: ChatContext | Mapping[str, Any] | None = None,
                 history: Sequence[Mapping[str, Any]] = (),
                 signals: AIWorkerSignals | None = None,
                 pool: QThreadPool | None = None,
                 options: Mapping[str, Any] | None = None) -> AIReplyTask:
    """Queue one AI reply on a thread pool and return the task.

    Defaults to Qt's global pool, so nothing extra has to be created or torn
    down. Returns the task so the caller can keep a reference to it.
    """
    task = AIReplyTask(
        client, user_message, context=context, history=history,
        signals=signals, options=options,
    )
    (pool or QThreadPool.globalInstance()).start(task)
    return task


__all__ = [
    "AIReplyTask",
    "AIWorkerSignals",
    "UNEXPECTED_FAILURE",
    "submit_reply",
]
