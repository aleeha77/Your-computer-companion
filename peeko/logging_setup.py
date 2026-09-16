"""Logging configuration for Peeko.

Sets up stdlib logging with two handlers:

- a console handler (human-readable);
- a rotating file handler inside the user data directory
  (``<data_dir>/logs/peeko.log``, 1 MB per file, 3 backups).

The log level comes from :class:`peeko.settings.Settings` (``PEEKO_LOG_LEVEL``).
Secrets are never logged: use :func:`sanitize_message` (defence in depth)
and never pass API keys to log calls.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT_LOGGER_NAME = "peeko"
LOG_FILE_NAME = "peeko.log"
_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"


def setup_logging(level: str | int = "INFO",
                  log_dir: Path | None = None) -> logging.Logger:
    """Configure and return the ``peeko`` logger.

    :param level: logging level name (e.g. ``"DEBUG"``) or constant.
    :param log_dir: directory for the rotating log file. If ``None`` only
        the console handler is attached.

    Calling this more than once is safe: existing handlers are removed
    first so reconfiguration (e.g. in tests) takes effect cleanly.
    """
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)
    logger.addHandler(console)

    if log_dir is not None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / LOG_FILE_NAME,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def sanitize_message(message: str, secrets: list[str]) -> str:
    """Redact ``secrets`` from ``message`` before it reaches a log handler."""
    safe = message
    for secret in secrets:
        if secret:  # never redact the empty string
            safe = safe.replace(secret, "[REDACTED]")
    return safe