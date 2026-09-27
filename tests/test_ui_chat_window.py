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

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from peeko.ai.client import AIClient
from peeko.ai.context import InteractionLog, build_context
from peeko.avatar.widget import AvatarWindow
from peeko.settings import Settings
from peeko.ui.chat_window import (
    ROLE_NOTICE,
    ROLE_PEEKO,
    ROLE_USER,
    THINKING_TEXT,
    ChatEntry,
    ChatWindow,
    build_banner_text,
)
from peeko.ui.context_menu import TALK_ID, future_entries, find_entry
from tests.conftest import (
    TEST_API_KEY,
    TEST_BASE_URL,
    FakeTransport,
    ai_client,
    ai_settings,
    completion_body,
    synchronous_submit,
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
