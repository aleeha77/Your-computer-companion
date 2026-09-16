"""Tests for peeko.logging_setup — handler wiring, file rotation, redaction."""

from __future__ import annotations

import logging

from peeko.logging_setup import (
    LOG_FILE_NAME,
    ROOT_LOGGER_NAME,
    sanitize_message,
    setup_logging,
)


def test_setup_creates_log_file(tmp_path):
    logger = setup_logging("DEBUG", tmp_path)
    assert logger.name == ROOT_LOGGER_NAME
    logger.info("hello from a test")
    # handlers flush on emit for file handler? force flush for determinism
    for handler in logger.handlers:
        handler.flush()
    log_file = tmp_path / LOG_FILE_NAME
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "hello from a test" in content


def test_console_and_rotating_handlers(tmp_path):
    logger = setup_logging("INFO", tmp_path)
    handler_types = sorted(type(h).__name__ for h in logger.handlers)
    assert "RotatingFileHandler" in handler_types
    assert "StreamHandler" in handler_types
    assert len([h for h in logger.handlers if h.level == logging.INFO]) >= 1


def test_reconfigure_replaces_handlers(tmp_path):
    logger = setup_logging("INFO", tmp_path)
    first_handlers = set(logger.handlers)
    logger = setup_logging("DEBUG", tmp_path)
    assert set(logger.handlers).isdisjoint(first_handlers)


def test_level_controls_visibility(tmp_path):
    logger = setup_logging("WARNING", tmp_path)
    logger.info("invisible message")
    logger.warning("visible warning")
    for handler in logger.handlers:
        handler.flush()
    content = (tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "invisible message" not in content
    assert "visible warning" in content


def test_sanitize_message_redacts_secrets():
    message = "calling api with key sk-abc123 and refresh sk-abc123 again"
    safe = sanitize_message(message, secrets=["sk-abc123"])
    assert "sk-abc123" not in safe
    assert safe.count("[REDACTED]") == 2


def test_sanitize_message_ignores_empty_secret():
    assert sanitize_message("plain text", secrets=[""]) == "plain text"