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

The window owns no AI logic of its own: prompts, validation and the
controlled vocabulary all live in :mod:`peeko.ai`. Here we only talk to
:class:`peeko.ai.client.AIClient`, which is why the whole chat path can be
tested with a fake provider and no network.

Nothing in this module executes anything an AI reply says. An answer can
change exactly two things: the text shown here and which allowed animation
the robot plays.
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

LOG = logging.getLogger("peeko.ui")

#: Shown while a request is in flight (the user must never be left guessing).
THINKING_TEXT = "Peeko is thinking…"

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
    :param parent: usually the avatar window, which owns this window.

    Signals:
        expressionRequested: an accepted animation name from an AI reply. The
            avatar window maps it onto one of its real animations and plays
            it; nothing here touches the state machine.
    """

    expressionRequested = Signal(str)

    def __init__(self, settings, client: AIClient | None = None, *,
                 context_provider: Callable[[], ChatContext | None] | None = None,
                 interactions: InteractionLog | None = None,
                 submit: Callable[..., object] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._injected_client = client is not None
        self._client = client or AIClient.from_settings(settings)
        self._context_provider = context_provider
        self._interactions = interactions
        self._submit = submit or submit_reply

        self._entries: list[ChatEntry] = []
        self._history: list[dict[str, str]] = []
        self._tasks: list[object] = []
        self._thinking = False

        # One signal emitter for the whole window, owned by the UI thread:
        # the worker emits from its own thread and Qt delivers the slots here.
        self._signals = AIWorkerSignals()
        self._signals.finished.connect(self._on_reply)
        self._signals.failed.connect(self._on_failed)

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

        self._send_button = QPushButton("Send")
        self._send_button.setObjectName("chat_send")
        self._send_button.setToolTip("Send your message (or just press Enter).")
        self._send_button.clicked.connect(self._on_send_clicked)
        row.addWidget(self._send_button)
        layout.addLayout(row)

        self._hint = QLabel(
            f"Enter sends · Esc closes this window · the robot keeps floating. "
            f"Stage {__stage__} of {__total_stages__} — Peeko cannot control "
            f"your computer; it can only chat and play an expression."
        )
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
        LOG.info("Chat window configuration: %s", self._client.describe())
        return not banner

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Refresh the configuration each time the window comes up."""
        super().showEvent(event)
        self.refresh_configuration()
        self._input.setFocus()

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
        self._status.setText(THINKING_TEXT if thinking else "")
        self._send_button.setEnabled(not thinking)
        self._send_button.setToolTip(
            "Peeko is thinking — one message at a time."
            if thinking else "Send your message (or just press Enter)."
        )

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
            self._status.setText("; ".join(reply.notes))
        self.expressionRequested.emit(reply.animation)
        LOG.info("Chat reply shown (emotion=%s animation=%s).",
                 reply.emotion, reply.animation)

    def _on_failed(self, message: str) -> None:
        """A request failed: say so in the user's own words, never invent one."""
        self._set_thinking(False)
        text = (message or "").strip() or FAILED_TEXT
        self._append_notice(text, error=True)
        LOG.info("Chat request failed: %s", text)

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
                      parent: QWidget | None = None) -> ChatWindow:
    """Build (but do not show) the chat window."""
    return ChatWindow(
        settings, client, context_provider=context_provider,
        interactions=interactions, submit=submit, parent=parent,
    )


def show_chat_window(settings, client: AIClient | None = None, *,
                     context_provider: Callable[[], ChatContext | None] | None = None,
                     interactions: InteractionLog | None = None,
                     submit: Callable[..., object] | None = None,
                     parent: QWidget | None = None) -> ChatWindow:
    """Build, show and focus the chat window (non-blocking)."""
    window = build_chat_window(
        settings, client, context_provider=context_provider,
        interactions=interactions, submit=submit, parent=parent,
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
    "MAX_TRANSCRIPT_ENTRIES",
    "ROLE_NOTICE",
    "ROLE_PEEKO",
    "ROLE_USER",
    "SPEAKER_LABELS",
    "THINKING_TEXT",
    "build_banner_text",
    "build_chat_window",
    "show_chat_window",
]
