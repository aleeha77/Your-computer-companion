"""Off-thread voice work: the UI never waits on audio (Stages 4 and 5).

Listening means blocking — the microphone has to be open for as long as you
talk. Speaking blocks too: a synthesis is an HTTP round trip and playback
lasts as long as the sentence does. Both therefore run on a
:class:`~PySide6.QtCore.QThreadPool` worker and report back through Qt
signals, exactly like the chat request in :mod:`peeko.ai.worker`.

The window stays completely responsive while Peeko listens or talks (the
animation keeps ticking, you can keep typing, you can close the window), both
directions are cancellable, and every failure arrives as an honest message
rather than a freeze, a made-up transcript or a pretended utterance.

**Voice input** (:class:`VoiceWorkerSignals`) — signals, and what the UI is
expected to do with each:

* :attr:`VoiceWorkerSignals.transcribed` — real words: put them in the input
  box;
* :attr:`VoiceWorkerSignals.empty` — nothing was heard (silence, or the
  service had no words): do nothing, quietly;
* :attr:`VoiceWorkerSignals.cancelled` — the user stopped the capture: do
  nothing, quietly;
* :attr:`VoiceWorkerSignals.failed` — a real problem: show the message and log
  a warning.

**Voice output** (:class:`SpeechWorkerSignals`) — the same four outcomes:

* :attr:`SpeechWorkerSignals.finished` — the reply was played to the end;
* :attr:`SpeechWorkerSignals.empty` — nothing to say (no text, or the service
  returned no audio): do nothing, quietly;
* :attr:`SpeechWorkerSignals.cancelled` — the user stopped the playback;
* :attr:`SpeechWorkerSignals.failed` — a real problem: show the message and log
  a warning.

Nothing in this module inspects the *content* of a transcript or of a reply:
the text to speak is handed straight to the synthesizer and never logged.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from peeko.voice.errors import VoiceError
from peeko.voice.input import SpeechRecognizer
from peeko.voice.output import SpeechSynthesizer

LOG = logging.getLogger("peeko.voice")

#: Shown when something unexpected (not a :class:`VoiceError`) breaks.
UNEXPECTED_FAILURE = (
    "Peeko hit an unexpected problem with voice input. "
    "Check the log for details."
)

#: The same, for the speaking direction.
UNEXPECTED_SPEECH_FAILURE = (
    "Peeko hit an unexpected problem while speaking. "
    "Check the log for details."
)


class VoiceWorkerSignals(QObject):
    """Signals emitted from the worker thread, delivered on the UI thread.

    Keep one instance alive for as long as tasks using it are running (the
    window holds it); a task only emits, never owns.
    """

    #: ``str`` — recognised text (never empty; emptiness uses :attr:`empty`).
    transcribed = Signal(str)
    #: No words were heard — nothing to report, nothing to show.
    empty = Signal()
    #: The user stopped the capture before it produced anything.
    cancelled = Signal()
    #: ``str`` — a user-facing failure message (never contains the key).
    failed = Signal(str)


class VoiceCaptureTask(QRunnable):
    """One listening session, executed off the UI thread.

    :param recognizer: the configured
        :class:`~peeko.voice.input.SpeechRecognizer`.
    :param signals: emitter shared with the owning window.
    :param max_duration_s: optional override for how long to listen.
    """

    def __init__(self, recognizer: SpeechRecognizer, *,
                 signals: VoiceWorkerSignals | None = None,
                 max_duration_s: float | None = None) -> None:
        super().__init__()
        self._recognizer = recognizer
        self.signals = signals or VoiceWorkerSignals()
        self._cancel = threading.Event()
        if max_duration_s is not None:
            try:
                recognizer.max_duration_s = float(max_duration_s)
            except Exception:  # noqa: BLE001 - best-effort override
                LOG.debug("Could not apply the capture-length override",
                          exc_info=True)
        self.setAutoDelete(True)

    # ------------------------------------------------------------------ #
    # Cancellation (called from the UI thread while the worker runs)
    # ------------------------------------------------------------------ #
    def cancel(self) -> None:
        """Ask the capture to stop; safe to call from any thread."""
        self._cancel.set()

    @property
    def is_cancelled(self) -> bool:
        """Whether a stop was requested."""
        return self._cancel.is_set()

    def _should_stop(self) -> bool:
        return self._cancel.is_set()

    # ------------------------------------------------------------------ #
    # QRunnable entry point
    # ------------------------------------------------------------------ #
    def run(self) -> None:  # noqa: D102 - QRunnable entry point
        try:
            text = self._recognizer.listen(should_stop=self._should_stop)
        except VoiceError as exc:
            LOG.warning("Voice input failed: %s", exc.message)
            self.signals.failed.emit(exc.message)
        except Exception:  # noqa: BLE001 - never let a worker crash the app
            LOG.exception("Unexpected voice input failure")
            self.signals.failed.emit(UNEXPECTED_FAILURE)
        else:
            if text:
                self.signals.transcribed.emit(text)
            elif self._cancel.is_set():
                self.signals.cancelled.emit()
            else:
                self.signals.empty.emit()


def submit_capture(recognizer: SpeechRecognizer, *,
                   signals: VoiceWorkerSignals | None = None,
                   max_duration_s: float | None = None,
                   pool: QThreadPool | None = None) -> VoiceCaptureTask:
    """Queue one listening session on a thread pool and return the task.

    Defaults to Qt's global pool, so nothing extra has to be created or torn
    down. Returns the task so the caller can keep a reference to it (and
    cancel it).
    """
    task = VoiceCaptureTask(
        recognizer, signals=signals, max_duration_s=max_duration_s,
    )
    (pool or QThreadPool.globalInstance()).start(task)
    return task


# --------------------------------------------------------------------------- #
# Stage 5: speaking, off the UI thread
# --------------------------------------------------------------------------- #
class SpeechWorkerSignals(QObject):
    """Signals emitted from the speaking worker, delivered on the UI thread.

    Keep one instance alive for as long as tasks using it are running (the
    window holds it); a task only emits, never owns.
    """

    #: The reply was synthesized and played to the end.
    finished = Signal()
    #: There was nothing to say (no text, or the service returned no audio).
    empty = Signal()
    #: The user stopped the playback before it finished.
    cancelled = Signal()
    #: ``str`` — a user-facing failure message (never contains the key).
    failed = Signal(str)


class SpeechTask(QRunnable):
    """One utterance, synthesized and played off the UI thread.

    :param synthesizer: the configured
        :class:`~peeko.voice.output.SpeechSynthesizer`.
    :param text: exactly what Peeko should say (Peeko's own reply text — it
        is never logged).
    :param signals: emitter shared with the owning window.
    """

    def __init__(self, synthesizer: SpeechSynthesizer, text: str, *,
                 signals: SpeechWorkerSignals | None = None) -> None:
        super().__init__()
        self._synthesizer = synthesizer
        self._text = text or ""
        self.signals = signals or SpeechWorkerSignals()
        self._cancel = threading.Event()
        self.setAutoDelete(True)

    # ------------------------------------------------------------------ #
    # Cancellation (called from the UI thread while the worker runs)
    # ------------------------------------------------------------------ #
    def cancel(self) -> None:
        """Ask the playback to stop; safe to call from any thread."""
        self._cancel.set()
        stop = getattr(self._synthesizer, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:  # noqa: BLE001 - stopping is best-effort
                LOG.debug("Could not stop the speech playback", exc_info=True)

    @property
    def is_cancelled(self) -> bool:
        """Whether a stop was requested."""
        return self._cancel.is_set()

    def _should_stop(self) -> bool:
        return self._cancel.is_set()

    @property
    def text(self) -> str:
        """What this task was asked to say (kept, never logged)."""
        return self._text

    # ------------------------------------------------------------------ #
    # QRunnable entry point
    # ------------------------------------------------------------------ #
    def run(self) -> None:  # noqa: D102 - QRunnable entry point
        try:
            clip = self._synthesizer.speak(
                self._text, should_stop=self._should_stop
            )
        except VoiceError as exc:
            LOG.warning("Speaking failed: %s", exc.message)
            self.signals.failed.emit(exc.message)
        except Exception:  # noqa: BLE001 - never let a worker crash the app
            LOG.exception("Unexpected speaking failure")
            self.signals.failed.emit(UNEXPECTED_SPEECH_FAILURE)
        else:
            if clip is not None:
                self.signals.finished.emit()
            elif self._cancel.is_set():
                self.signals.cancelled.emit()
            else:
                self.signals.empty.emit()


def submit_speech(synthesizer: SpeechSynthesizer, text: str, *,
                  signals: SpeechWorkerSignals | None = None,
                  pool: QThreadPool | None = None) -> SpeechTask:
    """Queue one utterance on a thread pool and return the task.

    Defaults to Qt's global pool. Returns the task so the caller can keep a
    reference to it (and cancel it).
    """
    task = SpeechTask(synthesizer, text, signals=signals)
    (pool or QThreadPool.globalInstance()).start(task)
    return task


__all__ = [
    "UNEXPECTED_FAILURE",
    "UNEXPECTED_SPEECH_FAILURE",
    "SpeechTask",
    "SpeechWorkerSignals",
    "VoiceCaptureTask",
    "VoiceWorkerSignals",
    "submit_capture",
    "submit_speech",
]
