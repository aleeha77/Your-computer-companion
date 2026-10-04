"""The chat window (Stage 3) — offscreen, and without a real key.

Everything here runs against a fake HTTP transport, so the whole chat path
(prompt -> provider -> validator -> window) is exercised with no network and
no API key. What the tests pin down:

* **product form**: the chat is its own window, and the robot stays a
  frameless, always-on-top floating window which keeps running when the chat
  closes;
* **the Talk entry**: it opens the chat window instead of the old "not
  implemented" dialog;
* **honesty**: a missing key shows a banner and a plain message — never a
  made-up reply — and every failure is reported in words the owner can act
  on;
* **feedback**: the user's message and Peeko's answer are both visible, and
  a "thinking…" state covers the wait (which happens off the UI thread);
* **the bridge to the avatar**: an accepted animation name reaches the
  robot, and nothing else does.
"""

from __future__ import annotations

import json
import sys

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from peeko.ai.client import AIClient
from peeko.ai.context import InteractionLog, build_context
from peeko.avatar.widget import AvatarWindow
from peeko.settings import Settings
from peeko.ui.chat_window import (
    LISTENING_TEXT,
    MIC_LABEL,
    MIC_STOP_LABEL,
    NOTHING_TO_SPEAK_TEXT,
    ROLE_NOTICE,
    ROLE_PEEKO,
    ROLE_USER,
    SPEAKING_TEXT,
    SPEAK_ENABLED_HINT,
    SPEAK_LABEL,
    SPEAK_STOP_LABEL,
    THINKING_TEXT,
    VOICE_DISABLED_TEXT,
    ChatEntry,
    ChatWindow,
    build_banner_text,
    build_heard_text,
)
from peeko.ui.context_menu import TALK_ID, future_entries, find_entry
from peeko.voice.audio import UnavailableAudioSource
from peeko.voice.errors import STTProviderError, TTSProviderError
from peeko.voice.input import SpeechRecognizer
from peeko.voice.output import DISABLED_TEXT as TTS_DISABLED_TEXT
from peeko.voice.output import SpeechSynthesizer
from peeko.voice.output_providers import MockSpeechProvider
from peeko.voice.providers import MockTranscriptionProvider
from tests.conftest import (
    TEST_API_KEY,
    TEST_BASE_URL,
    FakeAudioSource,
    FakeSpeechPlayer,
    FakeTransport,
    ai_client,
    ai_settings,
    completion_body,
    pcm_samples,
    speech_synthesizer,
    speech_wav,
    synchronous_submit,
    synchronous_submit_capture,
    synchronous_submit_speech,
    tts_settings,
    voice_recognizer,
    voice_settings,
    wait_for,
)

GOOD_REPLY_TEXT = "Hi! I am Peeko, happy to chat."

GOOD_REPLY = json.dumps({
    "response": GOOD_REPLY_TEXT,
    "emotion": "playful",
    "animation": "talking_happy",
    "action": None,
})


def make_window(tmp_path, *, reply: str = GOOD_REPLY, error=None,
                key: str = TEST_API_KEY, transport: FakeTransport | None = None,
                submit=..., **kwargs):
    """A chat window wired to a fake transport (never a real request).

    ``reply`` is the JSON the *model* returns; the transport wraps it in a
    real chat-completions body so the whole provider path is exercised.
    """
    settings = ai_settings(tmp_path, key=key)
    transport = transport if transport is not None else FakeTransport(
        completion_body(reply), error=error
    )
    client = AIClient.from_settings(settings, transport=transport)
    if submit is ...:
        submit = synchronous_submit()
    window = ChatWindow(settings, client, submit=submit, **kwargs)
    return window, transport


# --------------------------------------------------------------------------- #
# Product form: a separate window while the robot keeps floating
# --------------------------------------------------------------------------- #
def test_the_chat_window_is_a_real_separate_window(qapp, tmp_path):
    window, _ = make_window(tmp_path)
    try:
        assert window.windowTitle() == "Peeko — Chat"
        assert window.isModal() is False
        assert window.windowFlags() & Qt.Window
        assert window.objectName() == "chat_window"
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_robot_stays_a_floating_window_next_to_the_chat(qapp, tmp_path):
    settings = ai_settings(tmp_path)
    avatar = AvatarWindow(settings)
    try:
        avatar.show()
        window, _ = make_window(tmp_path, parent=avatar)
        window.show()
        qapp.processEvents()

        assert window is not avatar
        # The robot is still frameless, always on top and open.
        assert avatar.windowFlags() & Qt.FramelessWindowHint
        assert avatar.windowFlags() & Qt.WindowStaysOnTopHint
        assert avatar.isVisible() is True

        # Closing the chat window must not close the robot.
        window.close()
        qapp.processEvents()
        assert avatar.isVisible() is True
        assert avatar.isHidden() is False
    finally:
        for widget in (avatar,):
            widget.hide()
            widget.deleteLater()
        qapp.processEvents()


def test_the_talk_entry_opens_the_chat_window(qapp, tmp_path):
    settings = ai_settings(tmp_path)
    avatar = AvatarWindow(settings)
    try:
        action = next(
            a for a in avatar._menu.actions() if a.data() == TALK_ID
        )
        assert "not implemented" not in action.text().lower()
        avatar._on_menu_triggered(action)

        window = avatar._chat_window
        assert isinstance(window, ChatWindow)
        assert window.parent() is avatar
        assert window.isVisible() is True
        # The talk entry records itself in the context log.
        assert any("opened the chat window" in line
                   for line in avatar._interactions.entries())
    finally:
        avatar.hide()
        avatar.deleteLater()
        qapp.processEvents()


def test_talk_is_the_only_menu_entry_that_graduated():
    talk = find_entry(TALK_ID)
    assert talk is not None and talk.implemented is True
    assert talk.display_label == "Talk"
    still_future = {entry.id for entry in future_entries()}
    assert still_future == {"feed", "pet", "play", "sleep", "wake"}


def test_reopening_talk_reuses_the_window_and_keeps_the_conversation(
    qapp, tmp_path
):
    settings = ai_settings(tmp_path)
    avatar = AvatarWindow(settings)
    try:
        action = next(a for a in avatar._menu.actions() if a.data() == TALK_ID)
        avatar._on_menu_triggered(action)
        window = avatar._chat_window
        window.send_message("hello")
        first = window.transcript_text()
        window.close()
        avatar._on_menu_triggered(action)

        assert avatar._chat_window is window
        assert window.transcript_text() == first
    finally:
        avatar.hide()
        avatar.deleteLater()
        qapp.processEvents()


# --------------------------------------------------------------------------- #
# Honesty: not configured
# --------------------------------------------------------------------------- #
def test_a_missing_key_shows_the_banner_and_says_where_to_fix_it(qapp, tmp_path):
    window, transport = make_window(tmp_path, key="")
    try:
        banner = window.banner_text()
        assert "AI not configured — set PEEKO_AI_API_KEY in .env" in banner
        assert ".env.example" in banner
        assert window.is_configured() is False
        assert window.configuration_problem() == (
            "AI not configured — set PEEKO_AI_API_KEY in .env"
        )
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_no_banner_is_shown_once_a_key_is_set(qapp, tmp_path):
    window, _ = make_window(tmp_path)
    try:
        assert window.banner_text() == ""
        assert window.is_configured() is True
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_banner_also_covers_a_missing_model(qapp, tmp_path):
    """A key without a model is still not configured — and it says which."""
    window = ChatWindow(ai_settings(tmp_path, model=""),
                        submit=synchronous_submit())
    try:
        assert window.is_configured() is False
        assert "PEEKO_AI_MODEL" in window.banner_text()
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_sending_without_a_key_is_honest_and_makes_no_request(qapp, tmp_path):
    window, transport = make_window(tmp_path, key="")
    try:
        assert window.send_message("hello?") is False
        assert transport.call_count == 0  # nothing was even attempted
        roles = [entry.role for entry in window.entries()]
        assert roles == [ROLE_USER, ROLE_NOTICE]
        assert window.entries()[-1].error is True
        assert "PEEKO_AI_API_KEY" in window.entries()[-1].text
        assert window.is_thinking() is False
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_refresh_configuration_picks_up_settings_changed_on_disk(
    qapp, tmp_path
):
    """Reopening the window re-reads the configuration (a .env edit works)."""
    settings = ai_settings(tmp_path, key="")
    window = ChatWindow(settings, submit=synchronous_submit())
    try:
        assert window.banner_text() != ""
        # As if .env had been filled in and the owner reopened the window.
        window._settings = ai_settings(tmp_path)
        assert window.refresh_configuration() is True
        assert window.banner_text() == ""
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_window_never_shows_the_key_anywhere(qapp, tmp_path):
    window, _ = make_window(tmp_path)
    try:
        window.send_message("hello")
        rendered = "\n".join(
            widget.text() for widget in window.findChildren(QLabel)
        )
        assert TEST_API_KEY not in rendered
        assert TEST_API_KEY not in window.transcript_text()
        assert TEST_API_KEY not in window.windowTitle()
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_build_banner_text_is_empty_only_when_nothing_is_wrong(tmp_path):
    assert build_banner_text(ai_client(FakeTransport())) == ""
    broken = ai_client(FakeTransport(), key="")
    assert "PEEKO_AI_API_KEY" in build_banner_text(broken)


# --------------------------------------------------------------------------- #
# The conversation itself
# --------------------------------------------------------------------------- #
def test_sending_shows_the_message_and_the_reply(qapp, tmp_path):
    window, transport = make_window(tmp_path)
    try:
        assert window.send_message("hello there") is True
        assert transport.call_count == 1
        entries = window.entries()
        assert [(e.role, e.text) for e in entries] == [
            (ROLE_USER, "hello there"),
            (ROLE_PEEKO, "Hi! I am Peeko, happy to chat."),
        ]
        assert entries[1].emotion == "playful"
        assert entries[1].animation == "talking_happy"
        # Plain-text view the user can select and the tests can read.
        assert "You: hello there" in window.transcript_text()
        assert "Peeko (playful): Hi! I am Peeko" in window.transcript_text()
        assert window.transcript_entries()[0] == ("You", "hello there")
        # The input box is cleared and the window is idle again.
        assert window.input_text() == ""
        assert window.is_thinking() is False
        assert window.status_text() == ""
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_send_button_sends(qapp, tmp_path):
    window, transport = make_window(tmp_path)
    try:
        window.show()
        window.set_input_text("hi from the button")
        window._send_button.click()
        assert transport.call_count == 1
        assert window.entries()[0].text == "hi from the button"
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_enter_in_the_input_box_sends(qapp, tmp_path):
    window, transport = make_window(tmp_path)
    try:
        window.show()
        window._input.setFocus()
        window.set_input_text("hi from the keyboard")
        QTest.keyClick(window._input, Qt.Key_Return)
        assert transport.call_count == 1
        assert window.entries()[0].text == "hi from the keyboard"
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_an_empty_message_is_not_sent(qapp, tmp_path):
    window, transport = make_window(tmp_path)
    try:
        assert window.send_message("") is False
        assert window.send_message("   ") is False
        assert transport.call_count == 0
        assert window.entries() == ()
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_window_says_it_cannot_control_the_computer(qapp, tmp_path):
    window, _ = make_window(tmp_path)
    try:
        hint = next(
            widget.text() for widget in window.findChildren(QLabel)
            if widget.objectName() == "chat_hint"
        )
        assert "cannot control your computer" in hint
        assert "Enter sends" in hint
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_earlier_turns_are_sent_back_with_the_next_message(qapp, tmp_path):
    window, transport = make_window(tmp_path)
    try:
        window.send_message("hello")
        window.send_message("how are you?")
        assert [m["role"] for m in transport.messages] == [
            "system", "user", "assistant", "user"
        ]
        assert transport.messages[1]["content"] == "hello"
        assert transport.messages[2]["content"] == GOOD_REPLY_TEXT
        assert transport.messages[3]["content"] == "how are you?"
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_structured_context_reaches_the_model(qapp, tmp_path):
    log = InteractionLog()
    log.record("user clicked the robot")
    window, transport = make_window(
        tmp_path,
        context_provider=lambda: build_context(interactions=log),
    )
    try:
        window.send_message("what did I just do?")
        prompt = transport.system_prompt
        assert "user clicked the robot" in prompt
        assert '"sleepiness"' in prompt
        assert TEST_BASE_URL in transport.last_call["url"]
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_a_broken_context_provider_never_stops_the_chat(qapp, tmp_path):
    def boom():
        raise RuntimeError("no context today")

    window, transport = make_window(tmp_path, context_provider=boom)
    try:
        assert window.send_message("hello") is True
        assert transport.call_count == 1
        assert window.entries()[-1].role == ROLE_PEEKO
    finally:
        window.deleteLater()
        qapp.processEvents()


# --------------------------------------------------------------------------- #
# The prompt is the AI layer's business — the window just carries it
# --------------------------------------------------------------------------- #
def test_the_window_sends_the_persona_prompt(qapp, tmp_path):
    from peeko.ai.personality import PERSONA

    window, transport = make_window(tmp_path)
    try:
        window.send_message("hello")
        assert PERSONA.strip() in transport.system_prompt
    finally:
        window.deleteLater()
        qapp.processEvents()


# --------------------------------------------------------------------------- #
# Bonus: the avatar bridge
# --------------------------------------------------------------------------- #
def test_the_replys_animation_is_offered_to_the_avatar(qapp, tmp_path):
    window, _ = make_window(tmp_path)
    seen: list[str] = []
    window.expressionRequested.connect(seen.append)
    try:
        window.send_message("hello")
        assert seen == ["talking_happy"]
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_an_unknown_animation_never_reaches_the_avatar(qapp, tmp_path):
    """The validator is the gate: only allowed names are passed on."""
    body = completion_body(json.dumps({
        "response": "wheee", "emotion": "hyper", "animation": "backflip",
    }))
    window, _ = make_window(tmp_path, transport=FakeTransport(body))
    seen: list[str] = []
    window.expressionRequested.connect(seen.append)
    try:
        window.send_message("hello")
        assert seen == ["idle"]
        assert window.entries()[1].emotion == "neutral"
        assert "animation" in window.status_text()
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_avatar_plays_the_animation_the_window_requests(qapp, tmp_path):
    """End to end, without Qt signals: window cue -> avatar state machine."""
    settings = ai_settings(tmp_path)
    avatar = AvatarWindow(settings)
    try:
        assert avatar.play_expression("talking_happy") == "click"
        assert avatar._machine.state == "click"
        assert avatar.play_expression("not a real animation") == "idle"
        assert avatar._machine.state == "idle"
    finally:
        avatar.hide()
        avatar.deleteLater()
        qapp.processEvents()


def test_the_avatar_context_is_built_from_real_interactions(qapp, tmp_path):
    settings = ai_settings(tmp_path)
    avatar = AvatarWindow(settings)
    try:
        context = avatar.build_ai_context()
        assert "recent_interactions" in context.placeholders
        avatar._interactions.record("user clicked the robot")
        context = avatar.build_ai_context()
        assert context.recent_interactions == ("user clicked the robot",)
        assert "recent_interactions" not in context.placeholders
        # Pet state stays an honest placeholder until Stages 6/7.
        assert "emotion" in context.placeholders
    finally:
        avatar.hide()
        avatar.deleteLater()
        qapp.processEvents()


# --------------------------------------------------------------------------- #
# Thinking state: the wait is visible, and nothing blocks
# --------------------------------------------------------------------------- #
def test_the_thinking_state_is_shown_while_peeko_waits(qapp, tmp_path):
    transport = FakeTransport(completion_body(GOOD_REPLY), delay_s=0.4)
    window, _ = make_window(tmp_path, transport=transport, submit=None)
    try:
        window.show()
        window.set_input_text("hello")
        assert window.send_message() is True

        # Immediately after sending: thinking, and the UI is alive.
        assert window.is_thinking() is True
        assert window.status_text() == THINKING_TEXT
        assert window._send_button.isEnabled() is False
        # …and the window is responsive: the robot keeps animating, and a
        # second message cannot pile up on the first.
        assert transport.call_count == 0  # the request is off the UI thread
        assert wait_for(lambda: transport.call_count >= 1, timeout_s=5.0)
        assert window.send_message("me too") is False
        assert transport.call_count == 1

        assert wait_for(lambda: window.is_thinking() is False, timeout_s=5.0)
        assert window.entries()[-1].role == ROLE_PEEKO
        assert window.status_text() == ""
        assert window._send_button.isEnabled() is True
    finally:
        from PySide6.QtCore import QThreadPool

        QThreadPool.globalInstance().waitForDone(5000)
        window.deleteLater()
        qapp.processEvents()


# --------------------------------------------------------------------------- #
# Failures are reported, never faked
# --------------------------------------------------------------------------- #
def test_a_network_failure_is_reported_without_a_fake_reply(qapp, tmp_path):
    import urllib.error

    window, _ = make_window(
        tmp_path, error=urllib.error.URLError("Name or service not known")
    )
    try:
        assert window.send_message("hello") is True  # sent, then failed
        entries = window.entries()
        assert [entry.role for entry in entries] == [ROLE_USER, ROLE_NOTICE]
        assert entries[1].error is True
        assert "Could not reach" in entries[1].text
        assert window.is_thinking() is False
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_a_timeout_is_reported_in_plain_words(qapp, tmp_path):
    window, _ = make_window(tmp_path, error=TimeoutError("slow"))
    try:
        window.send_message("hello")
        notice = window.entries()[-1]
        assert notice.role == ROLE_NOTICE and notice.error is True
        assert "did not answer within" in notice.text
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_an_unreadable_reply_is_reported_honestly(qapp, tmp_path):
    body = json.dumps({"choices": [{"message": {"content": "not json, really"}}]})
    window, _ = make_window(tmp_path, transport=FakeTransport(body))
    try:
        window.send_message("hello")
        # Plain prose is still a real answer — it is shown as text.
        assert window.entries()[-1].role == ROLE_PEEKO

        # A body with no usable text at all is a failure, not an invention.
        window2, _ = make_window(
            tmp_path, transport=FakeTransport(json.dumps({"choices": []}))
        )
        try:
            window2.send_message("hello")
            notice = window2.entries()[-1]
            assert notice.role == ROLE_NOTICE and notice.error is True
            assert "no answer" in notice.text.lower()
        finally:
            window2.deleteLater()
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_a_wrong_schema_reply_is_reported_honestly(qapp, tmp_path):
    body = completion_body(json.dumps({"emotion": "happy"}))  # no "response"
    window, _ = make_window(tmp_path, transport=FakeTransport(body))
    try:
        window.send_message("hello")
        notice = window.entries()[-1]
        assert notice.role == ROLE_NOTICE and notice.error is True
        assert "response" in notice.text
        assert TEST_API_KEY not in notice.text
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_a_worker_blowup_is_reported_without_crashing_the_window(
    qapp, tmp_path
):
    class Exploding:
        """A client that is configured but blows up in an unexpected way."""

        model = "test-model"
        provider = "openai-compatible"

        def is_configured(self):
            return True

        def configuration_problem(self):
            return ""

        def describe(self):
            return "Exploding(configured=True)"

        def respond(self, *_args, **_kwargs):
            raise RuntimeError("internal bug")

    settings = ai_settings(tmp_path)
    window = ChatWindow(settings, Exploding())
    try:
        assert window.send_message("hello") is True
        assert wait_for(lambda: bool(window.entries()[1:]), timeout_s=5.0)
        notice = window.entries()[-1]
        assert notice.role == ROLE_NOTICE and notice.error is True
        assert "unexpected" in notice.text.lower()
    finally:
        from PySide6.QtCore import QThreadPool

        QThreadPool.globalInstance().waitForDone(5000)
        window.deleteLater()
        qapp.processEvents()


def test_a_broken_submit_is_reported_instead_of_raising(qapp, tmp_path):
    def broken_submit(*_args, **_kwargs):
        raise RuntimeError("cannot start the worker")

    window, _ = make_window(tmp_path, submit=broken_submit)
    try:
        assert window.send_message("hello") is False
        notice = window.entries()[-1]
        assert notice.role == ROLE_NOTICE and notice.error is True
        assert window.is_thinking() is False
    finally:
        window.deleteLater()
        qapp.processEvents()


# --------------------------------------------------------------------------- #
# Transcript details
# --------------------------------------------------------------------------- #
def test_entries_are_immutable_and_labelled():
    assert ChatEntry(ROLE_USER, "hi").speaker == "You"
    assert ChatEntry(ROLE_PEEKO, "hi", emotion="happy").speaker == "Peeko"
    assert ChatEntry(ROLE_NOTICE, "oops").speaker == "Notice"
    assert ChatEntry(ROLE_PEEKO, "hi", emotion="happy").render() == (
        "Peeko (happy): hi"
    )


def test_the_transcript_stays_bounded(qapp, tmp_path):
    from peeko.ui.chat_window import MAX_TRANSCRIPT_ENTRIES

    window, _ = make_window(tmp_path)
    try:
        for i in range(MAX_TRANSCRIPT_ENTRIES + 10):
            window.append_entry(ChatEntry(ROLE_PEEKO, f"line {i}"))
        assert len(window.entries()) == MAX_TRANSCRIPT_ENTRIES
        assert window.entries()[-1].text == (
            f"line {MAX_TRANSCRIPT_ENTRIES + 9}"
        )
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_model_text_cannot_inject_markup_into_the_transcript(qapp, tmp_path):
    hostile = json.dumps({
        "response": "<b>bold</b><script>bad()</script>",
        "emotion": "playful",
        "animation": "idle",
        "action": None,
    })
    window, _ = make_window(tmp_path, transport=FakeTransport(
        completion_body(hostile)
    ))
    try:
        window.send_message("hello")
        html = window._transcript.toPlainText()
        assert "<b>bold</b>" in html  # shown as text, not as markup
        assert window._transcript.toHtml().count("&lt;b&gt;") >= 1
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_history_is_exposed_for_the_next_message(qapp, tmp_path):
    window, _ = make_window(tmp_path)
    try:
        window.send_message("hello")
        history = window.history()
        assert [turn["role"] for turn in history] == ["user", "assistant"]
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_window_can_be_built_without_a_client(qapp, tmp_path):
    """Without an injected client it builds one from the settings."""
    settings = Settings(
        data_dir=tmp_path / "data", config_dir=tmp_path / "config",
        log_dir=tmp_path / "logs", ai_provider="openai-compatible",
        ai_model="m", ai_api_key=TEST_API_KEY,
    )
    window = ChatWindow(settings)
    try:
        assert window.client.is_configured() is True
        assert window.is_configured() is True
    finally:
        window.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("role", [ROLE_USER, ROLE_PEEKO, ROLE_NOTICE])
def test_the_first_render_invites_the_user_and_stays_clean(qapp, tmp_path, role):
    window, _ = make_window(tmp_path)
    try:
        # An empty transcript shows a friendly prompt, not a blank window.
        text = window._transcript.toPlainText()
        assert "Say hello to Peeko" in text
        window.append_entry(ChatEntry(role, "something"))
        assert "something" in window._transcript.toPlainText()
        assert "Say hello to Peeko" not in window._transcript.toPlainText()
    finally:
        window.deleteLater()
        qapp.processEvents()


# --------------------------------------------------------------------------- #
# Stage 4: voice input — the Mic button, the listening state, honesty
# --------------------------------------------------------------------------- #
HEARD = "what is the weather like"


class FakeVoiceTask:
    """A cancellable stand-in for the worker task the window holds."""

    def __init__(self) -> None:
        self.cancelled = 0

    def cancel(self) -> None:
        self.cancelled += 1


def stalled_submit_voice():
    """A ``submit_voice`` double that starts nothing but hands back a task.

    Used for the state-machine assertions (start/stop/close) where no result
    should arrive at all — the arrival path is covered separately with
    :func:`tests.conftest.synchronous_submit_capture`.
    """
    tasks: list[FakeVoiceTask] = []

    def submit(recognizer, *, signals=None, max_duration_s=None, pool=None):
        task = FakeVoiceTask()
        tasks.append(task)
        return task

    submit.tasks = tasks  # type: ignore[attr-defined]
    return submit


def test_the_mic_button_is_part_of_the_window(qapp, tmp_path):
    window, _ = make_window(tmp_path)
    try:
        button = window.findChild(type(window._mic_button), "chat_mic")
        assert button is not None
        assert button.text() == MIC_LABEL
        assert "microphone" in button.toolTip().lower()
        assert window.mic_text() == MIC_LABEL
        assert window.is_listening() is False
        assert window.voice_enabled() is True
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_hint_tells_the_user_that_mic_only_fills_the_message_box(
    qapp, tmp_path
):
    window, _ = make_window(tmp_path)
    try:
        hint = next(
            widget.text() for widget in window.findChildren(QLabel)
            if widget.objectName() == "chat_hint"
        )
        assert "Mic listens through your microphone (Stage 4)" in hint
        assert "nothing is sent until you press Send" in hint
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_switching_voice_off_still_shows_the_button_and_explains_itself(
    qapp, tmp_path
):
    """No dead button: a click on a disabled Microphone says why."""
    settings = voice_settings(tmp_path, enabled=False)
    window = ChatWindow(settings, submit=synchronous_submit())
    try:
        assert window.voice_enabled() is False
        assert window.mic_text() == MIC_LABEL     # never a hidden control
        assert "PEEKO_VOICE_ENABLED" in window._mic_button.toolTip()
        assert window.voice_problem() == VOICE_DISABLED_TEXT

        assert window.start_listening() is False
        assert window.is_listening() is False
        notice = window.entries()[-1]
        assert notice.role == ROLE_NOTICE and notice.error is True
        assert "PEEKO_VOICE_ENABLED=1" in notice.text
        assert window.status_text() == ""         # nothing is "listening"
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_settings_toggle_is_picked_up_when_the_window_refreshes(
    qapp, tmp_path
):
    window = ChatWindow(voice_settings(tmp_path, enabled=False),
                        submit=synchronous_submit())
    try:
        assert window.voice_enabled() is False
        window._settings = voice_settings(tmp_path, enabled=True)
        assert window.refresh_configuration() is True
        assert window.voice_enabled() is True
        assert "PEEKO_VOICE_ENABLED" not in window._mic_button.toolTip()
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_clicking_mic_listens_and_shows_the_state(qapp, tmp_path):
    seen: list[bool] = []
    window, _ = make_window(
        tmp_path,
        recognizer=voice_recognizer(text=HEARD),
        submit_voice=synchronous_submit_capture(),
    )
    window.listeningChanged.connect(seen.append)
    try:
        assert window.start_listening() is True
        assert window.is_listening() is True
        assert window.status_text() == LISTENING_TEXT
        assert window.mic_text() == MIC_STOP_LABEL
        assert seen == [True]

        # The words arrive through the event loop, as they do in the app.
        assert wait_for(lambda: window.is_listening() is False, timeout_s=5.0)
        assert seen == [True, False]
        assert window.mic_text() == MIC_LABEL
        assert window.status_text() == ""
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_transcript_lands_in_the_message_box_and_is_not_sent(
    qapp, tmp_path
):
    window, transport = make_window(
        tmp_path,
        recognizer=voice_recognizer(text=HEARD),
        submit_voice=synchronous_submit_capture(),
    )
    try:
        assert window.send_message("") is False   # nothing to send yet
        window.start_listening()
        assert wait_for(
            lambda: window.input_text() == HEARD, timeout_s=5.0
        ), "the transcript never reached the message box"

        assert transport.call_count == 0          # nothing was sent
        assert window.entries()[-1].role == ROLE_NOTICE
        assert window.entries()[-1].error is False
        assert build_heard_text(HEARD) == window.entries()[-1].text
        assert "press Send" in window.entries()[-1].text

        # …and the user can send it (or fix a word first).
        assert window.send_message() is True
        assert transport.call_count == 1
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_transcript_is_not_invented_when_nothing_was_heard(qapp, tmp_path):
    window, _ = make_window(
        tmp_path,
        recognizer=voice_recognizer(text=""),
        submit_voice=synchronous_submit_capture(),
    )
    try:
        window.start_listening()
        assert wait_for(lambda: window.is_listening() is False, timeout_s=5.0)
        assert window.input_text() == ""
        assert "Heard:" not in window.transcript_text()
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_clicking_mic_again_stops_the_capture_and_drops_the_result(
    qapp, tmp_path
):
    submit = synchronous_submit_capture()
    window, _ = make_window(
        tmp_path, recognizer=voice_recognizer(text=HEARD), submit_voice=submit,
    )
    try:
        assert window.start_listening() is True
        assert window.stop_listening() is True
        assert window.is_listening() is False
        assert window.status_text() == ""

        # The queued result arrives after the stop: it is dropped, silently.
        for _ in range(50):
            qapp.processEvents()
        assert window.input_text() == ""
        assert "Heard:" not in window.transcript_text()
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_mic_button_toggles_listening(qapp, tmp_path):
    submit = stalled_submit_voice()
    window, _ = make_window(
        tmp_path, recognizer=voice_recognizer(text=HEARD), submit_voice=submit,
    )
    try:
        button = window.findChild(type(window._mic_button), "chat_mic")
        button.click()
        assert window.is_listening() is True
        assert button.text() == MIC_STOP_LABEL
        button.click()
        assert window.is_listening() is False
        assert submit.tasks[0].cancelled == 1
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_closing_the_window_stops_the_capture(qapp, tmp_path):
    submit = stalled_submit_voice()
    window, _ = make_window(
        tmp_path, recognizer=voice_recognizer(text=HEARD), submit_voice=submit,
    )
    try:
        window.show()
        assert window.start_listening() is True
        window.close()
        assert window.is_listening() is False
        assert submit.tasks[0].cancelled == 1
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_a_failing_provider_is_reported_without_a_fake_transcript(
    qapp, tmp_path
):
    window, transport = make_window(
        tmp_path,
        recognizer=voice_recognizer(provider=MockTranscriptionProvider(
            error=STTProviderError(
                "The speech service answered with HTTP 500."
            )
        )),
        submit_voice=synchronous_submit_capture(),
    )
    try:
        assert window.start_listening() is True
        assert wait_for(
            lambda: window.entries() and window.entries()[-1].error is True,
            timeout_s=5.0,
        ), "the voice failure was never shown"

        notice = window.entries()[-1]
        assert notice.role == ROLE_NOTICE
        assert "HTTP 500" in notice.text
        assert window.input_text() == ""          # no invented transcript
        assert "Heard:" not in window.transcript_text()
        assert window.is_listening() is False
        assert window.status_text() == ""
        assert transport.call_count == 0          # the chat was untouched
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_a_missing_key_is_reported_when_mic_is_pressed(qapp, tmp_path):
    source = FakeAudioSource([pcm_samples(1_600, amplitude=1_200)])
    recognizer = SpeechRecognizer.from_settings(
        voice_settings(tmp_path, key=""), source=source,
    )
    window, transport = make_window(tmp_path, recognizer=recognizer)
    try:
        assert window.start_listening() is False
        assert source.open_calls == 0             # no audio device touched
        notice = window.entries()[-1]
        assert notice.role == ROLE_NOTICE and notice.error is True
        assert "PEEKO_AI_API_KEY" in notice.text
        assert window.is_listening() is False
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_no_microphone_is_reported_honestly(qapp, tmp_path):
    recognizer = SpeechRecognizer(
        source=UnavailableAudioSource(),
        provider=MockTranscriptionProvider(HEARD),
    )
    window, _ = make_window(tmp_path, recognizer=recognizer)
    try:
        assert window.start_listening() is False
        notice = window.entries()[-1]
        assert notice.role == ROLE_NOTICE and notice.error is True
        assert "no microphone" in notice.text
        assert window.is_listening() is False
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_a_broken_submit_is_reported_instead_of_raising(qapp, tmp_path):
    def broken_submit(*_args, **_kwargs):
        raise RuntimeError("cannot start the voice worker")

    window, _ = make_window(
        tmp_path, recognizer=voice_recognizer(text=HEARD),
        submit_voice=broken_submit,
    )
    try:
        assert window.start_listening() is False
        notice = window.entries()[-1]
        assert notice.role == ROLE_NOTICE and notice.error is True
        assert window.is_listening() is False
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_recognizer_is_rebuilt_from_settings_unless_one_is_injected(
    qapp, tmp_path
):
    """A .env edit is picked up by the next Mic press, like the AI client."""
    window, _ = make_window(tmp_path)
    try:
        first = window.recognizer()
        assert window.recognizer() is not first     # rebuilt from settings
        assert first.source.name == "sounddevice"
    finally:
        window.deleteLater()
        qapp.processEvents()

    injected = voice_recognizer(text=HEARD)
    window, _ = make_window(tmp_path, recognizer=injected)
    try:
        assert window.recognizer() is injected
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_a_request_and_a_capture_never_confuse_each_other(qapp, tmp_path):
    """A capture and a request may overlap, each with its own state.

    While the microphone is open the listening hint stays on screen (it is the
    more actionable line, and it is true), and the request runs regardless.
    """
    window, transport = make_window(
        tmp_path, recognizer=voice_recognizer(text=HEARD),
        submit_voice=synchronous_submit_capture(),
    )
    try:
        window.start_listening()
        assert window.status_text() == LISTENING_TEXT
        assert window.send_message("typed while listening") is True
        assert transport.call_count == 1               # the request went out
        assert window.is_listening() is True           # the mic stays open
        assert window.status_text() == LISTENING_TEXT

        assert wait_for(
            lambda: not window.is_thinking() and not window.is_listening(),
            timeout_s=5.0,
        )
        assert window.status_text() == ""
        # Neither the typed message nor the heard one was lost.
        roles = [entry.role for entry in window.entries()]
        assert ROLE_USER in roles and ROLE_PEEKO in roles
        assert any(entry.text.startswith("Heard:")
                   for entry in window.entries())
    finally:
        window.deleteLater()
        qapp.processEvents()


# --------------------------------------------------------------------------- #
# The bridge to the avatar: the robot shows it is listening
# --------------------------------------------------------------------------- #
def test_the_avatar_shows_the_listening_pose(qapp, tmp_path):
    avatar = AvatarWindow(ai_settings(tmp_path))
    try:
        assert avatar.play_listening(True) == "listening"
        assert avatar._machine.state == "listening"
        assert avatar._machine.listening is True
        assert avatar.play_listening(False) == "idle"
        assert avatar._machine.state == "idle"
        assert avatar._machine.listening is False
        assert avatar.play_listening(False) is None    # already ended
    finally:
        avatar.hide()
        avatar.deleteLater()
        qapp.processEvents()


def test_the_chat_window_tells_the_avatar_it_is_listening(qapp, tmp_path):
    """The real wiring: window.listeningChanged -> the robot's pose."""
    avatar = AvatarWindow(ai_settings(tmp_path))
    try:
        action = next(a for a in avatar._menu.actions() if a.data() == TALK_ID)
        avatar._on_menu_triggered(action)
        window = avatar._chat_window

        window.listeningChanged.emit(True)
        assert avatar._machine.state == "listening"
        window.listeningChanged.emit(False)
        assert avatar._machine.state == "idle"
    finally:
        avatar.hide()
        avatar.deleteLater()
        qapp.processEvents()


# --------------------------------------------------------------------------- #
# Stage 5: voice output — the Speak button, the speaking state, honesty
# --------------------------------------------------------------------------- #
class FakeSpeechTask:
    """A cancellable stand-in for the speech worker task the window holds."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.cancelled = 0

    def cancel(self) -> None:
        self.cancelled += 1


def stalled_submit_speech():
    """A ``submit_speech`` double that starts nothing but hands back a task.

    Used for the state-machine assertions (start/stop/close) where no outcome
    should arrive at all — the arrival paths are covered separately with
    :func:`tests.conftest.synchronous_submit_speech`.
    """
    tasks: list[FakeSpeechTask] = []

    def submit(synthesizer, text, *, signals=None, pool=None):
        task = FakeSpeechTask(text)
        tasks.append(task)
        return task

    submit.tasks = tasks  # type: ignore[attr-defined]
    return submit


def speaking_window(tmp_path, speaker=None, *, enabled: bool = True,
                    submit_speech=...):
    """A chat window wired to fake audio (never a real speaker, never a request).

    ``speaker`` is injected when a test needs to inspect what Peeko would have
    said; when it is omitted the window builds one from the settings, exactly
    as the running app does.
    """
    settings = tts_settings(tmp_path, enabled=enabled)
    transport = FakeTransport(completion_body(GOOD_REPLY))
    client = AIClient.from_settings(settings, transport=transport)
    kwargs = {}
    if speaker is not None:
        kwargs["synthesizer"] = speaker
    if submit_speech is not ...:
        kwargs["submit_speech"] = submit_speech
    window = ChatWindow(settings, client, submit=synchronous_submit(), **kwargs)
    return window, transport


def hint_text(window: ChatWindow) -> str:
    """The window's hint line (the honest account of what is switched on)."""
    return next(
        widget.text() for widget in window.findChildren(QLabel)
        if widget.objectName() == "chat_hint"
    )


def test_the_speak_button_is_visible_when_the_voice_is_on(qapp, tmp_path):
    window, _ = speaking_window(
        tmp_path, speech_synthesizer(), submit_speech=synchronous_submit_speech()
    )
    try:
        assert window.tts_enabled() is True
        assert window._speak_button.isHidden() is False
        assert window._speak_button.isEnabled() is True
        assert window.speak_text() == SPEAK_LABEL
        assert "speakers" in window._speak_button.toolTip().lower()
        assert window.is_speaking() is False
        # The hint line says what the button will do.
        assert SPEAK_ENABLED_HINT in hint_text(window)
        assert "PEEKO_TTS_ENABLED" not in hint_text(window)
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_speak_button_is_hidden_not_dead_when_the_voice_is_off(
    qapp, tmp_path
):
    """With TTS off the hint line says which variable turns it on."""
    window, _ = speaking_window(
        tmp_path, enabled=False, submit_speech=synchronous_submit_speech()
    )
    try:
        assert window.tts_enabled() is False
        assert window._speak_button.isHidden() is True
        assert window._speak_button.isEnabled() is False
        assert "PEEKO_TTS_ENABLED=1" in hint_text(window)
        assert window.speak_problem() == TTS_DISABLED_TEXT

        # Asking anyway refuses in plain words instead of pretending.
        window.append_entry(ChatEntry(ROLE_PEEKO, "something Peeko said"))
        assert window.speak() is False
        notice = window.entries()[-1]
        assert notice.role == ROLE_NOTICE and notice.error is True
        assert "PEEKO_TTS_ENABLED=1" in notice.text
        assert window.is_speaking() is False
        assert window.status_text() == ""
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_speak_setting_is_picked_up_when_the_window_refreshes(
    qapp, tmp_path
):
    window, _ = speaking_window(
        tmp_path, enabled=False, submit_speech=synchronous_submit_speech()
    )
    try:
        assert window.tts_enabled() is False
        assert window._speak_button.isHidden() is True
        # As if PEEKO_TTS_ENABLED had been changed and the window reopened.
        window._settings = tts_settings(tmp_path, enabled=True)
        assert window.refresh_configuration() is True
        assert window.tts_enabled() is True
        assert window._speak_button.isHidden() is False
        assert SPEAK_ENABLED_HINT in hint_text(window)
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_speak_button_plays_the_reply_in_the_transcript(qapp, tmp_path):
    submit = stalled_submit_speech()
    window, _ = speaking_window(
        tmp_path, speech_synthesizer(), submit_speech=submit
    )
    seen: list[bool] = []
    window.speakingChanged.connect(seen.append)
    try:
        window.show()
        window.append_entry(ChatEntry(ROLE_PEEKO, "a reply to say out loud"))
        assert window.last_reply_text() == "a reply to say out loud"

        window._speak_button.click()
        assert window.is_speaking() is True
        # Exactly Peeko's own reply text is what was handed to the worker.
        assert submit.tasks[-1].text == "a reply to say out loud"
        assert window.status_text() == SPEAKING_TEXT
        assert window.speak_text() == SPEAK_STOP_LABEL
        assert seen == [True]

        # The same button stops it again, and nothing is left "speaking".
        window._speak_button.click()
        assert window.is_speaking() is False
        assert submit.tasks[-1].cancelled == 1
        assert window.speak_text() == SPEAK_LABEL
        assert window.status_text() == ""
        assert seen == [True, False]
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_reply_is_spoken_automatically_and_really_played(qapp, tmp_path):
    """The whole path: reply -> synthesize -> decode -> "speakers"."""
    provider = MockSpeechProvider(audio=speech_wav())
    player = FakeSpeechPlayer()
    window, transport = speaking_window(
        tmp_path, speech_synthesizer(provider=provider, player=player),
        submit_speech=synchronous_submit_speech(),
    )
    seen: list[bool] = []
    window.speakingChanged.connect(seen.append)
    try:
        assert window.send_message("hello") is True
        assert transport.call_count == 1
        assert window.is_speaking() is True        # it starts with the reply
        assert window.status_text() == SPEAKING_TEXT

        assert wait_for(lambda: not window.is_speaking(), timeout_s=5.0)
        assert provider.texts == [GOOD_REPLY_TEXT]
        assert len(player.plays) == 1
        assert player.plays[0][0].audio.startswith(b"RIFF")
        assert seen == [True, False]
        assert window.status_text() == ""
        assert window.speak_text() == SPEAK_LABEL
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_with_the_voice_off_a_reply_is_never_spoken(qapp, tmp_path):
    spoken: list[str] = []

    def submit_speech(synthesizer, text, *, signals=None, pool=None):
        spoken.append(text)
        return "should-not-be-called"

    window, transport = speaking_window(
        tmp_path, speech_synthesizer(), enabled=False, submit_speech=submit_speech
    )
    try:
        assert window.send_message("hello") is True
        assert transport.call_count == 1
        assert window.entries()[-1].role == ROLE_PEEKO
        assert spoken == []                        # nothing was spoken
        assert window.is_speaking() is False
        assert window.status_text() == ""
        assert [entry.role for entry in window.entries()] == [
            ROLE_USER, ROLE_PEEKO
        ]
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_speak_with_nothing_to_say_yet_says_so(qapp, tmp_path):
    window, _ = speaking_window(
        tmp_path, speech_synthesizer(), submit_speech=stalled_submit_speech()
    )
    try:
        assert window.speak() is False
        notice = window.entries()[-1]
        assert notice.role == ROLE_NOTICE and notice.error is True
        assert notice.text == NOTHING_TO_SPEAK_TEXT
        assert window.is_speaking() is False
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_a_missing_key_is_reported_when_speak_is_pressed(qapp, tmp_path):
    """One key configures chat and speech — and its absence is said out loud."""
    speaker = SpeechSynthesizer.from_settings(
        tts_settings(tmp_path, key=""), player=FakeSpeechPlayer()
    )
    window, transport = speaking_window(
        tmp_path, speaker, submit_speech=synchronous_submit_speech()
    )
    try:
        window.append_entry(ChatEntry(ROLE_PEEKO, "hi there"))
        assert "PEEKO_AI_API_KEY" in window.speak_problem()

        assert window.speak() is False
        notice = window.entries()[-1]
        assert notice.role == ROLE_NOTICE and notice.error is True
        assert "PEEKO_AI_API_KEY" in notice.text
        assert window.is_speaking() is False
        assert transport.call_count == 0           # the chat was untouched
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_no_audio_device_is_reported_honestly_and_nothing_is_sent(
    qapp, tmp_path
):
    provider = MockSpeechProvider(audio=speech_wav())
    player = FakeSpeechPlayer(
        availability="Peeko cannot speak: no audio output device."
    )
    window, _ = speaking_window(
        tmp_path, speech_synthesizer(provider=provider, player=player),
        submit_speech=synchronous_submit_speech(),
    )
    try:
        window.append_entry(ChatEntry(ROLE_PEEKO, "hi there"))
        assert "no audio output device" in window.speak_problem()

        assert window.speak() is False
        notice = window.entries()[-1]
        assert notice.role == ROLE_NOTICE and notice.error is True
        assert "no audio output device" in notice.text
        assert provider.calls == 0                 # the reply never left the machine
        assert window.is_speaking() is False
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_an_http_error_is_reported_without_pretending(qapp, tmp_path):
    speaker = speech_synthesizer(provider=MockSpeechProvider(
        error=TTSProviderError(
            "The speech service answered with HTTP 400. "
            "It said: voice not found"
        )
    ))
    window, _ = speaking_window(
        tmp_path, speaker, submit_speech=synchronous_submit_speech()
    )
    try:
        window.append_entry(ChatEntry(ROLE_PEEKO, "hi there"))
        assert window.speak() is True
        assert window.is_speaking() is True

        assert wait_for(lambda: not window.is_speaking(), timeout_s=5.0)
        notice = window.entries()[-1]
        assert notice.role == ROLE_NOTICE and notice.error is True
        assert "HTTP 400" in notice.text
        assert "voice not found" in notice.text
        assert window.status_text() == ""
        assert window.speak_text() == SPEAK_LABEL
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_a_cancelled_playback_says_nothing_at_all(qapp, tmp_path):
    player = FakeSpeechPlayer()
    window, _ = speaking_window(
        tmp_path, speech_synthesizer(player=player),
        submit_speech=synchronous_submit_speech(),
    )
    try:
        window.append_entry(ChatEntry(ROLE_PEEKO, "a longer reply"))
        assert window.speak() is True
        assert window.stop_speaking() is True
        for _ in range(50):
            qapp.processEvents()

        assert window.is_speaking() is False
        assert window.entries()[-1].role == ROLE_PEEKO    # nothing was added
        assert window.status_text() == ""
        assert player.stops == 1                          # the stop was honoured
        assert player.plays == []                         # and nothing was played
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_closing_the_window_stops_the_playback(qapp, tmp_path):
    submit = stalled_submit_speech()
    window, _ = speaking_window(
        tmp_path, speech_synthesizer(), submit_speech=submit
    )
    try:
        window.show()
        window.append_entry(ChatEntry(ROLE_PEEKO, "hi"))
        assert window.speak() is True
        window.close()
        assert window.is_speaking() is False
        assert submit.tasks[-1].cancelled == 1
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_a_broken_speech_worker_is_reported_instead_of_raising(qapp, tmp_path):
    def broken_submit(*_args, **_kwargs):
        raise RuntimeError("cannot start the speech worker")

    window, _ = speaking_window(
        tmp_path, speech_synthesizer(), submit_speech=broken_submit
    )
    try:
        window.append_entry(ChatEntry(ROLE_PEEKO, "hi"))
        assert window.speak() is False
        notice = window.entries()[-1]
        assert notice.role == ROLE_NOTICE and notice.error is True
        assert window.is_speaking() is False
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_synthesizer_is_rebuilt_from_settings_unless_one_is_injected(
    qapp, tmp_path
):
    """A .env edit is picked up by the next Speak press, like the AI client."""
    window, _ = speaking_window(tmp_path, submit_speech=synchronous_submit_speech())
    try:
        first = window.synthesizer()
        assert isinstance(first, SpeechSynthesizer)
        assert window.synthesizer() is not first    # rebuilt from settings
        assert first.player.name == "sounddevice"
    finally:
        window.deleteLater()
        qapp.processEvents()

    injected = speech_synthesizer()
    window, _ = speaking_window(
        tmp_path, injected, submit_speech=synchronous_submit_speech()
    )
    try:
        assert window.synthesizer() is injected
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_chat_window_keeps_the_audio_library_lazy(qapp, tmp_path):
    """Speaking is optional: the window never imports ``sounddevice``."""
    assert "sounddevice" not in sys.modules
    window, _ = speaking_window(
        tmp_path, speech_synthesizer(), submit_speech=synchronous_submit_speech()
    )
    try:
        window.speak_problem()                     # a real config check
        window.append_entry(ChatEntry(ROLE_PEEKO, "hi"))
        assert window.speak() is True
        assert window.speak_text() == SPEAK_STOP_LABEL
        assert "sounddevice" not in sys.modules
    finally:
        window.deleteLater()
        qapp.processEvents()


# --------------------------------------------------------------------------- #
# The bridge to the avatar: the robot shows it is speaking
# --------------------------------------------------------------------------- #
def test_the_avatar_shows_the_talking_pose(qapp, tmp_path):
    avatar = AvatarWindow(ai_settings(tmp_path))
    try:
        assert avatar.play_speaking(True) == "talking"
        assert avatar._machine.state == "talking"
        assert avatar._machine.talking is True
        assert avatar.play_speaking(False) == "idle"
        assert avatar._machine.state == "idle"
        assert avatar._machine.talking is False
        assert avatar.play_speaking(False) is None    # already ended
    finally:
        avatar.hide()
        avatar.deleteLater()
        qapp.processEvents()


def test_the_chat_window_tells_the_avatar_it_is_speaking(qapp, tmp_path):
    """The real wiring: window.speakingChanged -> the robot's pose."""
    avatar = AvatarWindow(ai_settings(tmp_path))
    try:
        action = next(a for a in avatar._menu.actions() if a.data() == TALK_ID)
        avatar._on_menu_triggered(action)
        window = avatar._chat_window

        window.speakingChanged.emit(True)
        assert avatar._machine.state == "talking"
        window.speakingChanged.emit(False)
        assert avatar._machine.state == "idle"
    finally:
        avatar.hide()
        avatar.deleteLater()
        qapp.processEvents()
