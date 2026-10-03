"""The chat window: typed conversation with Peeko in its own window (Stage 3).

Stage 3 gives Peeko something to talk with. The conversation is a **separate
window** — the floating robot keeps floating, always on top, and the chat
opens from the right-click menu's *Talk* entry (or comes back to the front
if it is already open).

What this window is responsible for:

* showing the transcript — what the user typed and what Peeko really said;
* a "thinking…" state while Peeko waits for the AI (obviously: nothing here
  blocks, the request runs on :mod:`peeko.ai.worker`);
* an honest banner when the AI is not configured yet, and an honest message
  instead of a made-up reply whenever a request fails;
* handing Peeko's expression to the avatar: the validated ``animation`` from
  the reply is re-emitted as :attr:`ChatWindow.expressionRequested`, which
  the avatar window turns into one of its existing animations.

Stage 4 adds **voice input** to the same window:

* a **Mic** button that starts and stops a capture (click to talk, click
  again to stop). The microphone, the HTTP round trip and the waiting all run
  off the UI thread (see :mod:`peeko.voice.worker`);
* a visible **"listening…"** state, plus a
  :attr:`ChatWindow.listeningChanged` signal the avatar turns into its
  listening animation;
* transcribed words land in the message box — nothing is sent to Peeko until
  the user presses Send, so they can fix a misheard word first;
* every voice failure (microphone missing, permission refused, capture error,
  service error, timeout, no key, or voice input switched off in settings) is
  reported in plain words, and a cancelled or empty capture says nothing at
  all — never an invented transcript.

Stage 5 adds **voice output** to the same window:

* Peeko speaks each reply out loud when the feature is switched on
  (``PEEKO_TTS_ENABLED=1`` — off by default, so a fresh install is quiet), and
  a **Speak** button replays the last reply on demand (it turns into **Stop**
  while a playback is running);
* synthesis and playback both run off the UI thread (see
  :mod:`peeko.voice.worker`), so the robot never freezes waiting for audio;
* a visible **"Peeko is speaking…"** state, plus a
  :attr:`ChatWindow.speakingChanged` signal the avatar turns into its
  sustained talking animation;
* every speaking failure (voice switched off, no key, no audio output device,
  no audio library, a bad voice name, a service error, a timeout, unplayable
  audio) is reported in plain words, and a stopped playback says nothing at
  all;
* with the feature switched off there is **no Speak button at all** and the
  hint line says exactly which variable turns it on — never a dead control.

The window owns no AI or voice logic of its own: prompts, validation and the
controlled vocabulary live in :mod:`peeko.ai`; capture, transcription,
synthesis, playback and their failure modes live in :mod:`peeko.voice`. Here
we only talk to :class:`peeko.ai.client.AIClient`,
:class:`peeko.voice.input.SpeechRecognizer` and
:class:`peeko.voice.output.SpeechSynthesizer`, which is why the whole chat path
can be tested with a fake provider, a fake microphone and a fake player — and
no network at all.

Nothing in this module executes anything an AI reply says. An answer can
change exactly three things: the text shown here, which allowed animation the
robot plays, and whether that text is read aloud.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from peeko import __app_name__, __stage__, __total_stages__
from peeko.ai.client import AIClient
from peeko.ai.context import ChatContext, InteractionLog
from peeko.ai.schema import AIResponse
from peeko.ai.worker import AIWorkerSignals, submit_reply
from peeko.ui.context_menu import TALK_ID
from peeko.voice.errors import VoiceError
from peeko.voice.input import SpeechRecognizer
from peeko.voice.output import DISABLED_TEXT as TTS_DISABLED_TEXT
from peeko.voice.output import SpeechSynthesizer
from peeko.voice.worker import (
    SpeechWorkerSignals,
    VoiceWorkerSignals,
    submit_capture,
)
from peeko.voice.worker import submit_speech as submit_speech_worker

LOG = logging.getLogger("peeko.ui")

#: Shown while a request is in flight (the user must never be left guessing).
THINKING_TEXT = "Peeko is thinking…"

#: Shown while the microphone is open.
LISTENING_TEXT = "Listening… speak now, then click Stop (or press Mic again)."

#: Shown while Peeko's reply is being played out loud.
SPEAKING_TEXT = "Peeko is speaking… click Stop to cut it short."

#: Button labels for the Mic control.
MIC_LABEL = "Mic"
MIC_STOP_LABEL = "Stop"

#: Button labels for the Speak control.
SPEAK_LABEL = "Speak"
SPEAK_STOP_LABEL = "Stop"

#: Tooltips for the Mic control (honest about what a click will do).
MIC_TOOLTIP = (
    "Voice input: click to listen through your microphone. What Peeko hears "
    "lands in the message box — nothing is sent until you press Send. "
    "Click again to stop."
)
MIC_ENABLED_TOOLTIP = MIC_TOOLTIP
MIC_DISABLED_TOOLTIP = (
    "Voice input is switched off (PEEKO_VOICE_ENABLED=0). Click to see how "
    "to turn the microphone back on."
)

#: Shown when the user clicks Mic while voice input is switched off.
VOICE_DISABLED_TEXT = (
    "Voice input is switched off. Set PEEKO_VOICE_ENABLED=1 in .env (next to "
    "the app) and restart Peeko to use the microphone."
)

#: Tooltips for the Speak control (honest about what a click will do).
SPEAK_TOOLTIP = (
    "Peeko's voice: click to speak the last reply out loud through your "
    "speakers. Click again (or Stop) to cut it short."
)

#: Shown when the user asks Peeko to speak before there is anything to say.
NOTHING_TO_SPEAK_TEXT = (
    "There is nothing to speak yet — send Peeko a message first, or pick a "
    "reply from the transcript."
)

#: The hint line's account of the voice-output setting, on and off. TTS is off
#: by default, so the off-wording is what a fresh install shows.
SPEAK_ENABLED_HINT = (
    "Peeko speaks its replies out loud (Stage 5) — press Speak to hear one "
    "again, or Stop to cut it short."
)
SPEAK_DISABLED_HINT = (
    "Peeko's voice is switched off — set PEEKO_TTS_ENABLED=1 in .env to have "
    "it speak its replies out loud (Stage 5)."
)

#: Shown when a request failed for a reason nobody could describe better.
FAILED_TEXT = "Peeko could not answer just now."

#: How many transcript entries are kept (the display is a chat, not a log).
MAX_TRANSCRIPT_ENTRIES = 200

#: Transcript roles.
ROLE_USER = "user"
ROLE_PEEKO = "peeko"
ROLE_NOTICE = "notice"

#: How each role is labelled in the transcript.
SPEAKER_LABELS = {
    ROLE_USER: "You",
    ROLE_PEEKO: "Peeko",
    ROLE_NOTICE: "Notice",
}

@dataclass(frozen=True)
class ChatEntry:
    """One line of the transcript.

    :param role: :data:`ROLE_USER`, :data:`ROLE_PEEKO` or :data:`ROLE_NOTICE`.
    :param text: exactly what was said or shown (never rewritten).
    :param emotion: the emotion the AI labelled its reply with, if any.
    :param animation: the accepted animation name for that reply, if any.
    :param error: True for an honest failure notice (styled differently).
    """

    role: str
    text: str
    emotion: str | None = None
    animation: str | None = None
    error: bool = False

    @property
    def speaker(self) -> str:
        """The label shown before the text (``"You"``, ``"Peeko"``, …)."""
        return SPEAKER_LABELS.get(self.role, self.role.title())

    def render(self) -> str:
        """Plain-text form: ``"Peeko (playful): hi!"``."""
        suffix = f" ({self.emotion})" if self.emotion else ""
        return f"{self.speaker}{suffix}: {self.text}"


def build_banner_text(client: AIClient) -> str:
    """The honest banner shown when Peeko cannot chat yet (``""`` if fine).

    The first line is the precise configuration problem (for a missing key:
    *"AI not configured — set PEEKO_AI_API_KEY in .env"*), followed by where
    to fix it.
    """
    problem = client.configuration_problem()
    if not problem:
        return ""
    return (
        f"{problem}\n"
        "Open .env (copy .env.example first) next to the app, add your own "
        "key, and restart Peeko. See the README section “AI chat”."
    )


def build_heard_text(text: str) -> str:
    """The notice shown after a successful voice capture.

    Deliberately explicit that the words are *not* sent yet: speech
    recognition is not perfect, so the user gets to fix a word first.
    """
    return (
        f'Heard: “{" ".join(text.split())}” — press Send (or Enter) to send '
        f"it to Peeko, or edit it first."
    )


class ChatWindow(QWidget):
    """Peeko's chat window — one conversation, its own top-level window.

    :param settings: the running :class:`peeko.settings.Settings`.
    :param client: an :class:`peeko.ai.client.AIClient` to use. When omitted,
        one is built from ``settings`` and rebuilt every time the window is
        shown, so editing ``.env`` and reopening the window picks the new
        configuration up.
    :param context_provider: callable returning the structured context
        (:class:`peeko.ai.context.ChatContext`) for each message. This is the
        seam Stages 6/7/8 fill in with real emotions, needs, memory and app
        awareness; today the avatar passes an interaction log and the rest of
        the context is documented placeholders.
    :param interactions: shared :class:`peeko.ai.context.InteractionLog` the
        window records turns into.
    :param submit: the function that runs one reply off the UI thread.
        Defaults to :func:`peeko.ai.worker.submit_reply`; tests inject a
        synchronous double so no thread (and no network) is involved.
    :param recognizer: an :class:`peeko.voice.input.SpeechRecognizer` to use
        for voice input. When omitted, one is built from ``settings`` every
        time the microphone is opened, so editing ``.env`` and pressing Mic
        again picks the new configuration up.
    :param submit_voice: the function that runs one capture off the UI thread.
        Defaults to :func:`peeko.voice.worker.submit_capture`; tests inject a
        synchronous double so no thread (and no real microphone) is involved.
    :param synthesizer: a :class:`peeko.voice.output.SpeechSynthesizer` to use
        for voice output. When omitted, one is built from ``settings`` every
        time Peeko is asked to speak, so editing ``.env`` and pressing Speak
        again picks the new configuration up.
    :param submit_speech: the function that runs one utterance off the UI
        thread. Defaults to :func:`peeko.voice.worker.submit_speech`; tests
        inject a synchronous double so no thread (and no real speaker) is
        involved.
    :param parent: usually the avatar window, which owns this window.

    Signals:
        expressionRequested: an accepted animation name from an AI reply. The
            avatar window maps it onto one of its real animations and plays
            it; nothing here touches the state machine.
        listeningChanged: ``True`` when a voice capture starts, ``False`` when
            it ends (for any reason). The avatar window turns that into its
            listening animation.
        speakingChanged: ``True`` when a playback of Peeko's reply starts,
            ``False`` when it ends (finished, stopped or failed). The avatar
            window turns that into its talking animation.
    """

    expressionRequested = Signal(str)
    listeningChanged = Signal(bool)
    speakingChanged = Signal(bool)

    def __init__(self, settings, client: AIClient | None = None, *,
                 context_provider: Callable[[], ChatContext | None] | None = None,
                 interactions: InteractionLog | None = None,
                 submit: Callable[..., object] | None = None,
                 recognizer: SpeechRecognizer | None = None,
                 submit_voice: Callable[..., object] | None = None,
                 synthesizer: SpeechSynthesizer | None = None,
                 submit_speech: Callable[..., object] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._injected_client = client is not None
        self._client = client or AIClient.from_settings(settings)
        self._context_provider = context_provider
        self._interactions = interactions
        self._submit = submit or submit_reply
        self._injected_recognizer = recognizer is not None
        self._recognizer = recognizer
        self._submit_voice = submit_voice or submit_capture
        self._injected_synthesizer = synthesizer is not None
        self._synthesizer = synthesizer
        self._submit_speech = submit_speech or submit_speech_worker

        self._entries: list[ChatEntry] = []
        self._history: list[dict[str, str]] = []
        self._tasks: list[object] = []
        self._thinking = False
        self._notes = ""
        self._listening = False
        self._voice_task: object | None = None
        self._voice_cancel_requested = False
        self._speaking = False
        self._speech_task: object | None = None
        self._speech_cancel_requested = False

        # One signal emitter for the whole window, owned by the UI thread:
        # the worker emits from its own thread and Qt delivers the slots here.
        self._signals = AIWorkerSignals()
        self._signals.finished.connect(self._on_reply)
        self._signals.failed.connect(self._on_failed)

        self._voice_signals = VoiceWorkerSignals()
        self._voice_signals.transcribed.connect(self._on_transcribed)
        self._voice_signals.empty.connect(self._on_voice_empty)
        self._voice_signals.cancelled.connect(self._on_voice_cancelled)
        self._voice_signals.failed.connect(self._on_voice_failed)

        self._speech_signals = SpeechWorkerSignals()
        self._speech_signals.finished.connect(self._on_spoken)
        self._speech_signals.empty.connect(self._on_speech_empty)
        self._speech_signals.cancelled.connect(self._on_speech_cancelled)
        self._speech_signals.failed.connect(self._on_speech_failed)

        self._build_ui()
        self.refresh_configuration()

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        """Lay out banner, transcript, input row and status line."""
        self.setWindowTitle(f"{__app_name__} — Chat")
        self.setObjectName("chat_window")
        # A real top-level window (its own title bar) owned by the avatar, so
        # the robot stays a separate, frameless floating window. A QWidget
        # window is never modal, so the robot keeps animating behind it.
        self.setWindowFlag(Qt.Window, True)
        self.resize(480, 540)
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)

        self._banner = QLabel("")
        self._banner.setObjectName("chat_banner")
        self._banner.setWordWrap(True)
        self._banner.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._banner.setStyleSheet(
            "background: #fff4cc; color: #5a4300; border: 1px solid #e0c060;"
            " padding: 8px; border-radius: 4px;"
        )
        layout.addWidget(self._banner)

        self._transcript = QTextBrowser()
        self._transcript.setObjectName("chat_transcript")
        self._transcript.setOpenExternalLinks(False)
        self._transcript.setReadOnly(True)
        layout.addWidget(self._transcript, 1)

        self._status = QLabel("")
        self._status.setObjectName("chat_status")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: palette(mid);")
        layout.addWidget(self._status)

        row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setObjectName("chat_input")
        self._input.setPlaceholderText(f"Type a message to {__app_name__}…")
        self._input.returnPressed.connect(self._on_send_clicked)
        row.addWidget(self._input, 1)

        self._mic_button = QPushButton(MIC_LABEL)
        self._mic_button.setObjectName("chat_mic")
        self._mic_button.setToolTip(MIC_TOOLTIP)
        self._mic_button.clicked.connect(self._on_mic_clicked)
        row.addWidget(self._mic_button)

        self._speak_button = QPushButton(SPEAK_LABEL)
        self._speak_button.setObjectName("chat_speak")
        self._speak_button.setToolTip(SPEAK_TOOLTIP)
        self._speak_button.clicked.connect(self._on_speak_clicked)
        row.addWidget(self._speak_button)

        self._send_button = QPushButton("Send")
        self._send_button.setObjectName("chat_send")
        self._send_button.setToolTip("Send your message (or just press Enter).")
        self._send_button.clicked.connect(self._on_send_clicked)
        row.addWidget(self._send_button)
        layout.addLayout(row)

        self._hint = QLabel(self._build_hint())
        self._hint.setObjectName("chat_hint")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(self._hint)

        close_shortcut = QShortcut(QKeySequence("Esc"), self)
        close_shortcut.activated.connect(self.close)

        if not self._entries:
            self._render_transcript()
        self._input.setFocus()

    # ------------------------------------------------------------------ #
    # Configuration
    # ------------------------------------------------------------------ #
    def refresh_configuration(self) -> bool:
        """Re-read the AI configuration and update the banner.

        Called on construction and every time the window is shown, so a key
        added to ``.env`` while Peeko is running appears without a rebuild.

        :returns: True when Peeko can hold a conversation right now.
        """
        if not self._injected_client:
            self._client = AIClient.from_settings(self._settings)
        banner = build_banner_text(self._client)
        self._banner.setText(banner)
        self._banner.setVisible(bool(banner))
        self._refresh_mic_button()
        self._refresh_speak_button()
        LOG.info("Chat window configuration: %s", self._client.describe())
        return not banner

    def _refresh_mic_button(self) -> None:
        """Reflect the voice settings on the Mic button (never a dead button).

        The button stays clickable in every state: a click either starts a
        capture or explains, in the transcript, exactly why it cannot.
        """
        enabled = bool(getattr(self._settings, "voice_enabled", True))
        self._mic_button.setToolTip(
            MIC_ENABLED_TOOLTIP if enabled else MIC_DISABLED_TOOLTIP
        )

    def _build_hint(self) -> str:
        """The hint line, honest about which voice features are switched on."""
        parts = [
            "Enter sends · Esc closes this window · the robot keeps floating.",
            "Mic listens through your microphone (Stage 4) and puts the words "
            "in the message box — nothing is sent until you press Send.",
            SPEAK_ENABLED_HINT if self.tts_enabled() else SPEAK_DISABLED_HINT,
            f"Stage {__stage__} of {__total_stages__} — Peeko cannot control "
            f"your computer; it can only chat, speak and play an expression.",
        ]
        return " ".join(parts)

    def _refresh_speak_button(self) -> None:
        """Reflect the voice-output setting on the Speak button.

        Unlike the Mic — which always has a job (it can explain itself) — the
        Speak button only exists when there is a voice to use, so it is
        *hidden* while ``PEEKO_TTS_ENABLED`` is off. The hint line underneath
        then says exactly which variable turns Peeko's voice on, so the
        feature is never silently missing.
        """
        enabled = self.tts_enabled()
        self._speak_button.setVisible(enabled)
        self._speak_button.setEnabled(enabled)
        self._speak_button.setToolTip(SPEAK_TOOLTIP)
        self._hint.setText(self._build_hint())

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Refresh the configuration each time the window comes up."""
        super().showEvent(event)
        self.refresh_configuration()
        self._input.setFocus()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Never leave the microphone (or Peeko's voice) running behind us."""
        if self._listening:
            LOG.info("Chat window closed while listening — stopping the mic.")
            self.stop_listening()
        if self._speaking:
            LOG.info("Chat window closed while speaking — stopping playback.")
            self.stop_speaking()
        super().closeEvent(event)

    @property
    def client(self) -> AIClient:
        """The client this window talks to (never exposes the key)."""
        return self._client

    def configuration_problem(self) -> str:
        """What stops Peeko from chatting, or ``""`` when it can."""
        problem = self._client.configuration_problem()
        return problem

    def banner_text(self) -> str:
        """The banner currently shown (``""`` when nothing is wrong)."""
        return self._banner.text()

    def is_configured(self) -> bool:
        """Whether a request would actually be attempted."""
        return not self.configuration_problem()

    # ------------------------------------------------------------------ #
    # Transcript
    # ------------------------------------------------------------------ #
    def entries(self) -> tuple[ChatEntry, ...]:
        """Every transcript line, oldest first."""
        return tuple(self._entries)

    def transcript_text(self) -> str:
        """The whole transcript as plain text (used by the tests)."""
        return "\n".join(entry.render() for entry in self._entries)

    def transcript_entries(self) -> tuple[tuple[str, str], ...]:
        """``(speaker, text)`` pairs, for quick assertions."""
        return tuple((entry.speaker, entry.text) for entry in self._entries)

    def append_entry(self, entry: ChatEntry) -> None:
        """Add one line to the transcript and re-render it."""
        self._entries.append(entry)
        if len(self._entries) > MAX_TRANSCRIPT_ENTRIES:
            del self._entries[: len(self._entries) - MAX_TRANSCRIPT_ENTRIES]
        self._render_transcript()

    def _append_notice(self, text: str, *, error: bool = False) -> None:
        self.append_entry(ChatEntry(ROLE_NOTICE, text, error=error))

    def _render_transcript(self) -> None:
        """Draw the transcript (HTML, escaped — model text is untrusted)."""
        blocks: list[str] = []
        for entry in self._entries:
            colour = "#b00020" if entry.error else "#000000"
            if entry.role == ROLE_USER:
                colour = "#0b4f8a"
            body = html.escape(entry.text).replace("\n", "<br>")
            emotion = (
                f' <span style="color:#7a7a7a;">({html.escape(entry.emotion)})'
                f"</span>" if entry.emotion else ""
            )
            blocks.append(
                f'<p style="margin:6px 0;"><b style="color:{colour};">'
                f"{html.escape(entry.speaker)}</b>{emotion}<br>{body}</p>"
            )
        if not blocks:
            blocks.append(
                '<p style="color:#7a7a7a;">Say hello to Peeko — it answers '
                "here, in this window, while it keeps floating on your "
                "desktop.</p>"
            )
        self._transcript.setHtml("".join(blocks))

    # ------------------------------------------------------------------ #
    # Thinking state
    # ------------------------------------------------------------------ #
    def is_thinking(self) -> bool:
        """True while a reply is on its way."""
        return self._thinking

    def status_text(self) -> str:
        """The status line under the transcript."""
        return self._status.text()

    def _set_thinking(self, thinking: bool) -> None:
        self._thinking = thinking
        self._refresh_status()
        self._send_button.setEnabled(not thinking)
        self._send_button.setToolTip(
            "Peeko is thinking — one message at a time."
            if thinking else "Send your message (or just press Enter)."
        )

    def _refresh_status(self) -> None:
        """Show the one thing the user most needs to know right now.

        Priority: a request in flight, then a playback, then an open
        microphone, then any note about the last reply. Keeping this in one
        place means one indicator can never be wiped by another activity
        starting or finishing underneath it.
        """
        if self._thinking:
            text = THINKING_TEXT
        elif self._speaking:
            text = SPEAKING_TEXT
        elif self._listening:
            text = LISTENING_TEXT
        else:
            text = self._notes
        self._status.setText(text)

    # ------------------------------------------------------------------ #
    # Voice input (Stage 4): the microphone
    # ------------------------------------------------------------------ #
    def is_listening(self) -> bool:
        """True while a capture is running."""
        return self._listening

    def mic_text(self) -> str:
        """The Mic button's label (``"Mic"``, or ``"Stop"`` while listening)."""
        return self._mic_button.text()

    def voice_enabled(self) -> bool:
        """Whether the settings allow the microphone to be used at all."""
        return bool(getattr(self._settings, "voice_enabled", True))

    def recognizer(self) -> SpeechRecognizer:
        """The recognizer voice input will use.

        An injected one is reused; otherwise a fresh one is built from the
        current settings every time, so a ``.env`` edit is picked up by the
        next click (exactly like the AI client).
        """
        if self._injected_recognizer and self._recognizer is not None:
            return self._recognizer
        self._recognizer = SpeechRecognizer.from_settings(self._settings)
        return self._recognizer

    def voice_problem(self) -> str:
        """Why the microphone cannot be used right now (``""`` when it can)."""
        if not self.voice_enabled():
            return VOICE_DISABLED_TEXT
        try:
            return self.recognizer().availability_problem()
        except Exception as exc:  # noqa: BLE001 - never a crash from a check
            LOG.exception("Could not check the voice configuration")
            return (
                "Voice input unavailable: the microphone could not be checked "
                f"({exc})."
            )

    def _on_mic_clicked(self) -> None:
        """The Mic button: stop when listening, otherwise try to start."""
        if self._listening:
            self.stop_listening()
        else:
            self.start_listening()

    def start_listening(self) -> bool:
        """Open the microphone off the UI thread and show the listening state.

        :returns: True when a capture was actually started. ``False`` means
            nothing was opened — and the reason is always said out loud in the
            transcript, never swallowed.
        """
        if self._listening:
            return False
        if not self.voice_enabled():
            LOG.warning("Voice input is switched off (PEEKO_VOICE_ENABLED=0).")
            self._append_notice(VOICE_DISABLED_TEXT, error=True)
            return False

        try:
            recognizer = self.recognizer()
        except VoiceError as exc:
            LOG.warning("Voice input is misconfigured: %s", exc.message)
            self._append_notice(exc.message, error=True)
            return False
        except Exception:  # noqa: BLE001 - never a crash from configuration
            LOG.exception("Could not build the speech recognizer")
            self._append_notice(FAILED_TEXT, error=True)
            return False

        # Checked *before* the microphone is touched: with no key (or no
        # microphone) Peeko never opens an audio device at all.
        problem = recognizer.availability_problem()
        if problem:
            LOG.warning("Voice input unavailable: %s", problem)
            self._append_notice(problem, error=True)
            return False

        self._voice_cancel_requested = False
        try:
            task = self._submit_voice(
                recognizer, signals=self._voice_signals,
                max_duration_s=getattr(
                    self._settings, "voice_max_seconds", None
                ),
            )
        except Exception:  # noqa: BLE001 - a broken submit must not crash
            LOG.exception("Could not start the voice capture")
            self._append_notice(FAILED_TEXT, error=True)
            return False

        self._voice_task = task
        self._record("user started voice input")
        LOG.info("Voice capture started: %s", recognizer.describe())
        self._set_listening(True)
        return True

    def stop_listening(self, *, cancelled: bool = True) -> bool:
        """Stop a running capture; the words heard so far are dropped.

        :param cancelled: True when the user asked to stop (the worker reports
            a cancellation and nothing is shown); False for the internal
            teardown when a result already arrived.
        :returns: True when a capture was running.
        """
        if not self._listening:
            return False
        self._voice_cancel_requested = bool(cancelled)
        task = self._voice_task
        cancel = getattr(task, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:  # noqa: BLE001 - cancelling is best-effort
                LOG.debug("Could not cancel the voice capture", exc_info=True)
        self._record("user stopped voice input")
        LOG.info("Voice capture stopped by the user.")
        self._set_listening(False)
        return True

    def _set_listening(self, listening: bool) -> None:
        """Enter/leave the listening state and tell the avatar about it."""
        listening = bool(listening)
        was = self._listening
        self._listening = listening
        if not listening:
            self._voice_task = None
        self._mic_button.setText(MIC_STOP_LABEL if listening else MIC_LABEL)
        self._mic_button.setToolTip(
            "Stop listening and keep what was heard."
            if listening else (
                MIC_ENABLED_TOOLTIP if self.voice_enabled()
                else MIC_DISABLED_TOOLTIP
            )
        )
        self._refresh_status()
        if was != listening:
            self.listeningChanged.emit(listening)

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #
    def _on_send_clicked(self) -> None:
        self.send_message()

    def input_text(self) -> str:
        """What is currently typed in the input box."""
        return self._input.text()

    def set_input_text(self, text: str) -> None:
        """Put text in the input box (used by the tests)."""
        self._input.setText(text)

    def send_message(self, text: str | None = None) -> bool:
        """Send one message to Peeko.

        :param text: the message; defaults to what is typed in the input box.
        :returns: True when a request was actually started. ``False`` means
            nothing was sent — empty input, or a request already in flight.
            A missing key is **not** silent: the message is shown, followed
            by an honest notice, and no request is attempted at all.
        """
        message = (self._input.text() if text is None else text) or ""
        message = message.strip()
        if not message:
            LOG.debug("Ignoring an empty chat message.")
            return False
        if self._thinking:
            LOG.debug("A reply is already on the way — ignoring the message.")
            return False

        self._input.clear()
        self.append_entry(ChatEntry(ROLE_USER, message))
        self._record(f"user said: {_shorten(message)}")

        problem = self.configuration_problem()
        if problem:
            # Never a fake reply, and never a pointless request: say exactly
            # what is missing and stop here.
            LOG.info("Chat attempted while unconfigured: %s", problem)
            self._append_notice(problem, error=True)
            return False

        history = list(self._history)
        self._history.append({"role": "user", "content": message})
        self._set_thinking(True)
        context = self._build_context()
        try:
            task = self._submit(
                self._client, message, context=context, history=history,
                signals=self._signals,
            )
        except Exception:  # noqa: BLE001 - a broken submit must not crash
            LOG.exception("Could not start the AI request")
            self._set_thinking(False)
            self._append_notice(FAILED_TEXT, error=True)
            return False
        self._keep_task(task)
        return True

    def _build_context(self) -> ChatContext | None:
        """The structured context for the next message (see the seam above)."""
        if self._context_provider is None:
            return None
        try:
            return self._context_provider()
        except Exception:  # noqa: BLE001 - context is best-effort, never fatal
            LOG.exception("The chat context provider failed — sending none")
            return None

    def _keep_task(self, task: object) -> None:
        """Keep a reference to the running task (Qt may delete it itself)."""
        self._tasks.append(task)

    def _record(self, line: str) -> None:
        """Record a line in the shared interaction log, if there is one."""
        if self._interactions is not None:
            self._interactions.record(line)

    # ------------------------------------------------------------------ #
    # Results (delivered on the UI thread by the worker's signals)
    # ------------------------------------------------------------------ #
    def _on_reply(self, reply: object) -> None:
        """A validated reply arrived: show it and cue the avatar."""
        self._set_thinking(False)
        if not isinstance(reply, AIResponse):
            LOG.error("Worker delivered an unusable reply: %r", type(reply))
            self._append_notice(FAILED_TEXT, error=True)
            return
        self.append_entry(
            ChatEntry(ROLE_PEEKO, reply.response,
                      emotion=reply.emotion, animation=reply.animation)
        )
        self._history.append({"role": "assistant", "content": reply.response})
        self._record(
            f"Peeko replied ({reply.emotion}, {reply.animation})"
        )
        if reply.notes:
            # Honest about any value the model sent outside the allowed lists.
            self._notes = "; ".join(reply.notes)
        else:
            self._notes = ""
        self._refresh_status()
        self.expressionRequested.emit(reply.animation)
        LOG.info("Chat reply shown (emotion=%s animation=%s).",
                 reply.emotion, reply.animation)
        if self.tts_enabled():
            # Stage 5: say it out loud, off the UI thread. A failure here
            # never touches the reply that was just shown — it only adds an
            # honest note about the voice.
            self.speak(reply.response)

    def _on_failed(self, message: str) -> None:
        """A request failed: say so in the user's own words, never invent one."""
        self._set_thinking(False)
        text = (message or "").strip() or FAILED_TEXT
        self._append_notice(text, error=True)
        LOG.info("Chat request failed: %s", text)

    # ------------------------------------------------------------------ #
    # Voice results (delivered on the UI thread by the worker's signals)
    # ------------------------------------------------------------------ #
    def _on_transcribed(self, text: str) -> None:
        """Words arrived: put them in the message box, send nothing yet."""
        self._set_listening(False)
        if self._voice_cancel_requested:
            # The user stopped the capture; a late result is not wanted.
            LOG.debug("Dropping a transcript that arrived after a stop request.")
            return
        heard = (text or "").strip()
        if not heard:
            LOG.info("Voice input produced no words — nothing shown.")
            return
        self._input.setText(heard)
        self._input.setFocus()
        self._append_notice(build_heard_text(heard))
        self._record(f"user spoke (transcribed {len(heard)} characters)")
        LOG.info("Voice transcript placed in the input box (%d characters).",
                 len(heard))

    def _on_voice_empty(self) -> None:
        """Nothing was heard — say nothing, invent nothing."""
        self._set_listening(False)
        LOG.info("Voice capture ended without any words — nothing shown.")

    def _on_voice_cancelled(self) -> None:
        """The user stopped the capture — nothing to report."""
        self._set_listening(False)
        LOG.info("Voice capture cancelled — nothing shown.")

    def _on_voice_failed(self, message: str) -> None:
        """A voice problem: report it in plain words, never invent a transcript."""
        self._set_listening(False)
        text = (message or "").strip() or FAILED_TEXT
        self._append_notice(text, error=True)
        LOG.warning("Voice input failed: %s", text)

    # ------------------------------------------------------------------ #
    # Voice output (Stage 5): Peeko speaks its replies
    # ------------------------------------------------------------------ #
    def is_speaking(self) -> bool:
        """True while Peeko's reply is being played out loud."""
        return self._speaking

    def speak_text(self) -> str:
        """The Speak button's label (``"Speak"``, or ``"Stop"`` while playing)."""
        return self._speak_button.text()

    def tts_enabled(self) -> bool:
        """Whether the settings allow Peeko to speak at all."""
        return bool(getattr(self._settings, "tts_enabled", False))

    def synthesizer(self) -> SpeechSynthesizer:
        """The synthesizer speaking will use.

        An injected one is reused; otherwise a fresh one is built from the
        current settings every time, so a ``.env`` edit is picked up by the
        next click (exactly like the AI client and the recognizer).
        """
        if self._injected_synthesizer and self._synthesizer is not None:
            return self._synthesizer
        self._synthesizer = SpeechSynthesizer.from_settings(self._settings)
        return self._synthesizer

    def speak_problem(self) -> str:
        """Why Peeko cannot speak right now (``""`` when it can)."""
        if not self.tts_enabled():
            return TTS_DISABLED_TEXT
        try:
            return self.synthesizer().availability_problem()
        except Exception as exc:  # noqa: BLE001 - never a crash from a check
            LOG.exception("Could not check the speech configuration")
            return (
                "Peeko cannot speak: the voice could not be checked "
                f"({exc})."
            )

    def last_reply_text(self) -> str:
        """The text of Peeko's most recent reply (``""`` when there is none)."""
        for entry in reversed(self._entries):
            if entry.role == ROLE_PEEKO:
                return entry.text
        return ""

    def _on_speak_clicked(self) -> None:
        """The Speak button: stop when playing, otherwise (re)play a reply."""
        if self._speaking:
            self.stop_speaking()
        else:
            self.speak()

    def speak(self, text: str | None = None) -> bool:
        """Speak Peeko's last reply (or ``text``) out loud.

        Synthesis and playback run off the UI thread, so nothing here blocks.

        :param text: what to say; defaults to Peeko's most recent reply.
        :returns: True when a playback was actually started. ``False`` means
            nothing was played — and the reason is always said out loud in
            the transcript: no reply yet, the voice switched off, a missing
            key, no audio output device, or a broken worker. Nothing is ever
            faked as "spoken".
        """
        if self._speaking:
            LOG.debug("Peeko is already speaking — ignoring the request.")
            return False

        spoken = (self.last_reply_text() if text is None else text) or ""
        spoken = spoken.strip()
        if not spoken:
            LOG.debug("Nothing to speak yet.")
            self._append_notice(NOTHING_TO_SPEAK_TEXT, error=True)
            return False

        if not self.tts_enabled():
            LOG.warning("Speaking is switched off (PEEKO_TTS_ENABLED=0).")
            self._append_notice(TTS_DISABLED_TEXT, error=True)
            return False

        try:
            synthesizer = self.synthesizer()
        except VoiceError as exc:
            LOG.warning("The voice is misconfigured: %s", exc.message)
            self._append_notice(exc.message, error=True)
            return False
        except Exception:  # noqa: BLE001 - never a crash from configuration
            LOG.exception("Could not build the speech synthesizer")
            self._append_notice(FAILED_TEXT, error=True)
            return False

        # Checked *before* the service is called: with no key (or no audio
        # output) Peeko never sends the reply anywhere, and nothing is played.
        problem = synthesizer.availability_problem()
        if problem:
            LOG.warning("Peeko cannot speak: %s", problem)
            self._append_notice(problem, error=True)
            return False

        self._speech_cancel_requested = False
        try:
            task = self._submit_speech(
                synthesizer, spoken, signals=self._speech_signals,
            )
        except Exception:  # noqa: BLE001 - a broken submit must not crash
            LOG.exception("Could not start the speech playback")
            self._append_notice(FAILED_TEXT, error=True)
            return False

        self._speech_task = task
        self._record(f"user asked Peeko to speak ({len(spoken)} characters)")
        LOG.info("Speech playback started: %s", synthesizer.describe())
        self._set_speaking(True)
        return True

    def stop_speaking(self, *, cancelled: bool = True) -> bool:
        """Stop the playback in progress.

        :param cancelled: True when the user asked to stop (the worker reports
            a cancellation and nothing is shown); False for the internal
            teardown when the playback already finished.
        :returns: True when a playback was running.
        """
        if not self._speaking:
            return False
        self._speech_cancel_requested = bool(cancelled)
        task = self._speech_task
        cancel = getattr(task, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:  # noqa: BLE001 - cancelling is best-effort
                LOG.debug("Could not cancel the speech playback",
                          exc_info=True)
        self._record("user stopped Peeko speaking")
        LOG.info("Speech playback stopped by the user.")
        self._set_speaking(False)
        return True

    def _set_speaking(self, speaking: bool) -> None:
        """Enter/leave the speaking state and tell the avatar about it."""
        speaking = bool(speaking)
        was = self._speaking
        self._speaking = speaking
        if not speaking:
            self._speech_task = None
        self._speak_button.setText(
            SPEAK_STOP_LABEL if speaking else SPEAK_LABEL
        )
        self._speak_button.setToolTip(
            "Stop speaking and stay quiet." if speaking else SPEAK_TOOLTIP
        )
        self._refresh_status()
        if was != speaking:
            self.speakingChanged.emit(speaking)

    # ------------------------------------------------------------------ #
    # Speech results (delivered on the UI thread by the worker's signals)
    # ------------------------------------------------------------------ #
    def _on_spoken(self) -> None:
        """The playback finished — back to being quiet."""
        self._set_speaking(False)
        LOG.info("Peeko finished speaking.")

    def _on_speech_empty(self) -> None:
        """There was nothing to say, or no audio came back: quietly ignore."""
        self._set_speaking(False)
        LOG.info("Speech produced no audio — nothing was shown.")

    def _on_speech_cancelled(self) -> None:
        """The user stopped the playback — nothing to report."""
        self._set_speaking(False)
        LOG.info("Speech playback cancelled — nothing shown.")

    def _on_speech_failed(self, message: str) -> None:
        """A speaking problem: report it in plain words, never pretend."""
        self._set_speaking(False)
        text = (message or "").strip() or FAILED_TEXT
        self._append_notice(text, error=True)
        LOG.warning("Speaking failed: %s", text)

    # ------------------------------------------------------------------ #
    # History (sent back to the AI with the next message)
    # ------------------------------------------------------------------ #
    def history(self) -> tuple[dict[str, str], ...]:
        """The conversation sent to the provider with the next message."""
        return tuple(dict(turn) for turn in self._history)


def _shorten(text: str, limit: int = 60) -> str:
    """One short line for the interaction log (the log is bounded anyway)."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_chat_window(settings, client: AIClient | None = None, *,
                      context_provider: Callable[[], ChatContext | None] | None = None,
                      interactions: InteractionLog | None = None,
                      submit: Callable[..., object] | None = None,
                      recognizer: SpeechRecognizer | None = None,
                      submit_voice: Callable[..., object] | None = None,
                      synthesizer: SpeechSynthesizer | None = None,
                      submit_speech: Callable[..., object] | None = None,
                      parent: QWidget | None = None) -> ChatWindow:
    """Build (but do not show) the chat window."""
    return ChatWindow(
        settings, client, context_provider=context_provider,
        interactions=interactions, submit=submit, recognizer=recognizer,
        submit_voice=submit_voice, synthesizer=synthesizer,
        submit_speech=submit_speech, parent=parent,
    )


def show_chat_window(settings, client: AIClient | None = None, *,
                     context_provider: Callable[[], ChatContext | None] | None = None,
                     interactions: InteractionLog | None = None,
                     submit: Callable[..., object] | None = None,
                     recognizer: SpeechRecognizer | None = None,
                     submit_voice: Callable[..., object] | None = None,
                     synthesizer: SpeechSynthesizer | None = None,
                     submit_speech: Callable[..., object] | None = None,
                     parent: QWidget | None = None) -> ChatWindow:
    """Build, show and focus the chat window (non-blocking)."""
    window = build_chat_window(
        settings, client, context_provider=context_provider,
        interactions=interactions, submit=submit, recognizer=recognizer,
        submit_voice=submit_voice, synthesizer=synthesizer,
        submit_speech=submit_speech, parent=parent,
    )
    window.show()
    window.raise_()
    window.activateWindow()
    return window


#: Menu entry id that opens this window (kept here for the wiring code).
CHAT_MENU_ID = TALK_ID

__all__ = [
    "CHAT_MENU_ID",
    "ChatEntry",
    "ChatWindow",
    "FAILED_TEXT",
    "LISTENING_TEXT",
    "MAX_TRANSCRIPT_ENTRIES",
    "MIC_DISABLED_TOOLTIP",
    "MIC_LABEL",
    "MIC_STOP_LABEL",
    "MIC_TOOLTIP",
    "NOTHING_TO_SPEAK_TEXT",
    "ROLE_NOTICE",
    "ROLE_PEEKO",
    "ROLE_USER",
    "SPEAKER_LABELS",
    "SPEAKING_TEXT",
    "SPEAK_DISABLED_HINT",
    "SPEAK_ENABLED_HINT",
    "SPEAK_LABEL",
    "SPEAK_STOP_LABEL",
    "SPEAK_TOOLTIP",
    "THINKING_TEXT",
    "VOICE_DISABLED_TEXT",
    "build_banner_text",
    "build_chat_window",
    "build_heard_text",
    "show_chat_window",
]
