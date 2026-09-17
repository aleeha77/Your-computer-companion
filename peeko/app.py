"""Application entry point: wires everything together and runs the app.

Responsible for:

- graceful handling of SIGINT/SIGTERM (Ctrl+C quits the app cleanly);
- loading :class:`peeko.settings.Settings`;
- configuring logging;
- installing the global exception hook;
- creating the Qt application and the avatar window;
- honouring ``PEEKO_SMOKE_TEST=1`` (auto-quit ~2 s after startup with
  exit code 0, so headless CI can verify the full app boots).

Nothing here blocks the UI thread: there is no network or audio I/O at
Stage 0.
"""

from __future__ import annotations

import logging
import signal
import sys

from peeko import __app_name__, __stage__, __total_stages__, __version__
from peeko.avatar.widget import AvatarWindow
from peeko.errors import PeekoError, install_excepthook
from peeko.logging_setup import setup_logging
from peeko.settings import Settings, load_settings

LOG = logging.getLogger("peeko.app")

#: How long the app runs before auto-quitting in smoke-test mode (seconds).
SMOKE_TEST_DURATION_S = 2


def _install_signal_handlers(app) -> None:
    """Map SIGINT/SIGTERM to a graceful Qt quit.

    A plain ``signal.signal`` handler is NOT sufficient on its own: while
    ``app.exec()`` blocks the main thread inside C++, Python-level signal
    handlers cannot run, so Ctrl+C would do nothing. To make termination
    signals reliable we install a wakeup-fd bridge:

    - ``signal.set_wakeup_fd`` (POSIX) makes the OS kernel write a byte
      into one end of a socketpair whenever a handled signal arrives;
    - a ``QSocketNotifier`` watching the other end turns that byte into a
      normal Qt event, which calls ``app.quit()`` from the event loop.

    On platforms where the bridge cannot be installed (fallback), the
    plain handlers still apply (e.g. for signals arriving while the
    interpreter runs Python code).

    The bridge objects must be kept alive for the whole process: if the
    sockets are garbage-collected, their fds close and the wakeup bytes
    are silently lost. We therefore attach them to the ``app`` object.
    """
    def _quit(*_args) -> None:
        LOG.info("Received termination signal — quitting.")
        app.quit()

    try:
        signal.signal(signal.SIGINT, _quit)
        signal.signal(signal.SIGTERM, _quit)
    except (ValueError, OSError):  # pragma: no cover - non-main thread, etc.
        LOG.debug("Signal handlers not installed", exc_info=True)
        return

    if not hasattr(signal, "set_wakeup_fd"):
        LOG.debug("signal.set_wakeup_fd unavailable — signal bridge skipped.")
        return

    try:
        import socket

        from PySide6.QtCore import QSocketNotifier

        read_sock, write_sock = socket.socketpair()
        read_sock.setblocking(False)
        write_sock.setblocking(False)
        signal.set_wakeup_fd(write_sock.fileno())

        def _on_wakeup() -> None:
            try:
                read_sock.recv(4096)
            except OSError:  # pragma: no cover - socket drained/closed
                pass
            LOG.info("Termination signal delivered — quitting.")
            app.quit()

        notifier = QSocketNotifier(
            read_sock.fileno(), QSocketNotifier.Type.Read
        )
        notifier.activated.connect(_on_wakeup)
        notifier.setEnabled(True)
        # Keep the sockets and notifier alive for the whole process.
        app._peeko_signal_bridge = [read_sock, write_sock, notifier]
        LOG.info("Termination-signal bridge installed (SIGINT/SIGTERM).")
    except Exception:  # pragma: no cover - best-effort bridge
        LOG.debug("Wakeup-fd signal bridge not installed", exc_info=True)


def _create_app(argv: list[str]):
    """Create the QApplication with a stable identity for the OS."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QApplication.setApplicationName(__app_name__)
    QApplication.setApplicationVersion(__version__)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    return QApplication(argv)


def _report_startup_failure(message: str, *, interactive: bool) -> None:
    """Show the friendly startup-error dialog for a real GUI session.

    In a non-interactive run (``PEEKO_SMOKE_TEST=1``, headless CI) nobody
    can dismiss a modal dialog, so showing one would hang the process
    instead of letting it fail cleanly with a non-zero exit code. The
    failure is always logged by the caller; only the dialog is skipped.
    """
    if not interactive:
        LOG.debug("Non-interactive run: startup error dialog suppressed.")
        return
    from peeko.errors import _show_friendly_dialog

    _show_friendly_dialog("Peeko could not start", message)


def main(argv: list[str] | None = None) -> int:
    """Run Peeko; returns the process exit code."""
    argv = list(sys.argv if argv is None else argv)
    app = None
    interactive = True
    try:
        settings = load_settings()
        interactive = not settings.smoke_test
        setup_logging(settings.log_level, settings.log_dir)
        LOG.info(
            "%s v%s starting (Stage %d of %d)",
            __app_name__, __version__, __stage__, __total_stages__,
        )
        LOG.debug("Settings: %s", settings.public_dict())

        app = _create_app(argv)
        install_excepthook(show_dialog=not settings.smoke_test)
        _install_signal_handlers(app)

        window = AvatarWindow(settings)
        window.show()
        LOG.info("Avatar window shown on screen.")

        if settings.smoke_test:
            from PySide6.QtCore import QTimer

            LOG.info("Smoke-test mode: quitting in %ds.", SMOKE_TEST_DURATION_S)
            QTimer.singleShot(
                SMOKE_TEST_DURATION_S * 1000, app.quit
            )

        return app.exec()
    except PeekoError as exc:
        LOG.error("Startup failed: %s", exc.message)
        _report_startup_failure(exc.message, interactive=interactive)
        return 1
    except Exception:  # noqa: BLE001 - last-resort startup guard
        LOG.critical("Unexpected startup failure", exc_info=True)
        _report_startup_failure(
            "An unexpected error occurred while starting. Check the "
            "log file for details.",
            interactive=interactive,
        )
        return 1


if __name__ == "__main__":  # pragma: no cover - console entry convenience
    raise SystemExit(main())