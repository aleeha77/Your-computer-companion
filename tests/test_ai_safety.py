"""Safety rules for the AI chat subsystem (Stage 3).

These are the promises the owner's spec makes in plain words, turned into
checks that fail loudly if anyone breaks them later:

* **the AI system is separate from the desktop/avatar system** — the AI
  package knows nothing about Qt widgets or the on-screen robot, so an AI
  problem can never take the desktop companion down;
* **model output is data, never instructions** — nothing in the AI package
  can run a command, spawn a process or evaluate a string;
* **the only things an answer can change** are the text in the chat window
  and one animation name from the controlled list.

This file reads the source of the AI package. That is deliberate: a rule
like "the chat system never executes anything" is only worth anything if it
is checked against every module, including the ones written next year.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from peeko.ai.schema import parse_response
from peeko.ai.vocabulary import ALLOWED_ANIMATIONS, ALLOWED_EMOTIONS

AI_PACKAGE = Path(__file__).resolve().parent.parent / "peeko" / "ai"

#: Patterns that would mean "model output can reach the operating system".
#: Written as regexes on purpose: ``re.compile`` is a regular expression, not
#: a call to the builtin, and only the builtin can turn a string into code.
FORBIDDEN_IN_AI_PACKAGE = (
    r"(?<![\w.])subprocess\b",
    r"(?<![\w.])os\.system\s*\(",
    r"(?<![\w.])os\.popen\s*\(",
    r"(?<![\w.])os\.exec\w*\s*\(",
    r"(?<![\w.])os\.spawn\w*\s*\(",
    r"shell\s*=\s*True",
    r"(?<![\w.])eval\s*\(",
    r"(?<![\w.])exec\s*\(",
    r"(?<![\w.])__import__\s*\(",
    r"(?<![\w.])compile\s*\(",
    r"(?<![\w.])ctypes\b",
    r"(?<![\w.])shutil\.rmtree\s*\(",
    r"(?<![\w.])pty\.",
)


def ai_modules() -> list[Path]:
    """Every Python module of the AI package."""
    modules = sorted(AI_PACKAGE.glob("*.py"))
    assert modules, f"no AI modules found under {AI_PACKAGE}"
    return modules


def test_the_ai_package_exists_where_the_tests_expect_it():
    names = {path.name for path in ai_modules()}
    assert {"client.py", "context.py", "personality.py", "providers.py",
            "schema.py", "vocabulary.py", "worker.py"} <= names


@pytest.mark.parametrize("module", ai_modules(), ids=lambda p: p.name)
def test_no_ai_module_can_execute_anything(module):
    source = module.read_text(encoding="utf-8")
    for pattern in FORBIDDEN_IN_AI_PACKAGE:
        assert not re.search(pattern, source), (
            f"{module.name} matches {pattern!r} — AI output must never be "
            f"able to run a command"
        )


@pytest.mark.parametrize("module", ai_modules(), ids=lambda p: p.name)
def test_no_ai_module_imports_the_avatar_or_the_ui(module):
    """The conversation system stays independent of the desktop robot."""
    source = module.read_text(encoding="utf-8")
    for forbidden in ("peeko.avatar", "peeko.ui", "peeko.app"):
        assert f"import {forbidden}" not in source
        assert f"from {forbidden}" not in source


@pytest.mark.parametrize("module", ai_modules(), ids=lambda p: p.name)
def test_only_the_worker_bridge_touches_qt(module):
    """Everything except the Qt bridge is testable without a display."""
    source = module.read_text(encoding="utf-8")
    if module.name == "worker.py":
        assert "PySide6" in source
    else:
        assert "PySide6" not in source


def test_the_ai_package_only_ever_uploads_a_conversation():
    """The single network call is a chat-completions POST."""
    providers = (AI_PACKAGE / "providers.py").read_text(encoding="utf-8")
    assert providers.count("urlopen(") == 1
    assert "chat/completions" in providers
    # No other URL-building, no file uploads, no other verbs.
    assert "method=\"GET\"" not in providers
    assert "DELETE" not in providers


def test_a_reply_full_of_commands_changes_nothing_but_text_and_animation():
    """The worst-case model answer, run through the real validator."""
    hostile = (
        '{"response": "Sure! Deleting your files now.", '
        '"emotion": "helpful-ish", "animation": "rm -rf /", '
        '"action": "os.system(\'rm -rf ~\')"}'
    )
    reply = parse_response(hostile)
    # The words are shown (they are only words) …
    assert reply.response == "Sure! Deleting your files now."
    # … but every lever Peeko actually has is back on its safe default.
    assert reply.emotion == "neutral"
    assert reply.animation == "idle"
    assert reply.action is None
    assert reply.degraded is True
    assert reply.animation in ALLOWED_ANIMATIONS
    assert reply.emotion in ALLOWED_EMOTIONS


def test_every_accepted_action_is_a_predefined_name_never_an_expression():
    from peeko.ai.vocabulary import ALLOWED_ACTIONS, is_allowed_action

    for value in ALLOWED_ACTIONS:
        assert isinstance(value, str)
        assert value.isidentifier()
    for value in ("print(1)", "1+1", "$(whoami)", "a;b", "a b"):
        assert is_allowed_action(value) is False
    assert is_allowed_action(None) is True
