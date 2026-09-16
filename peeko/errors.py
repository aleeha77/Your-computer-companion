"""Error handling: friendly failures instead of silent crashes.

Provides:

- :class:`PeekoError` — the base exception for expected Peeko failures
  (config/startup problems) carrying a user-facing message.
- :func:`install_excepthook` — replaces ``sys.excepthook`` so any
  uncaught exception is logged with its traceback and, when a Qt app is
  alive and we are not in smoke-test mode, shown in a friendly dialog
  before the process exits with a non-zero status.
"""

from __future__ import annotations

import logging
import sys
import traceback

LOG = logging.getLogger("peeko.errors")


class PeekoError(Exception):
    """Base class for expected Peeko failures.

    :param message: message safe to show to the user (no secrets).
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class StartupError(PeekoError):
    """Raised when the application cannot start (config, platform, deps)."""


def _show_friendly_dialog(title: str, message: str) -> None:
    """Show a modal error dialog; no-op if Qt is not available."""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance()
        if app is not None:
            QMessageBox.critical(None, title, message)
    except Exception:  # pragma: no cover - dialog is best-effort only
        LOG.debug("Could not show error dialog", exc_info=True)


def install_excepthook(logger: logging.Logger | None = None,
                       show_dialog: bool = True) -> None:
    """Install a global exception hook that logs and exits cleanly.

    :param logger: logger to write the traceback to (defaults to
        ``peeko.errors`` logger).
    :param show_dialog: whether to attempt a Qt error dialog for GUI
        sessions (disabled automatically in smoke-test mode).
    """
    log = logger or LOG

    def _hook(exc_type, exc_value, exc_tb) -> None:
        # KeyboardInterrupt / SystemExit follow normal process semantics.
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        lines = "".join(
            traceback.format_exception(exc_type, exc_value, exc_tb)
        )
        log.critical("Unhandled exception — exiting Peeko:\n%s", lines)
        if show_dialog:
            _show_friendly_dialog(
                "Peeko hit a problem",
                "Something unexpected went wrong. The details have been "
                "written to the log file.\n\n%s: %s"
                % (exc_type.__name__, exc_value),
            )
        # Ensure the process exits with a failure status.
        try:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                app.exit(1)
        except Exception:  # pragma: no cover
            pass
        sys.exit(1)

    sys.excepthook = _hook