"""UI helpers for Peeko (menus, dialogs, overlays).

Stage 0: provides the avatar's right-click context menu with an obvious,
working Quit affordance. Later stages add the chat overlay, the needs
panel and the settings dialog here.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QMenu

from peeko import __stage__, __total_stages__, __version__


def build_avatar_context_menu(parent) -> QMenu:
    """Build the avatar's right-click context menu.

    The menu is honest about the current stage: an informational header
    lists the running version, and Quit actually shuts the app down.
    Future actions (talk to Peeko, feed, settings...) appear as they are
    implemented — never as dead buttons.
    """
    menu = QMenu(parent)

    header = menu.addAction(f"Peeko v{__version__}")
    header.setEnabled(False)
    stage = menu.addAction(f"Stage {__stage__} of {__total_stages__}")
    stage.setEnabled(False)
    menu.addSeparator()

    quit_action = menu.addAction("Quit")
    quit_action.setData("quit")
    quit_action.setShortcut("Ctrl+Q")
    return menu


def quit_application() -> None:
    """Quit the running QApplication (used by menu actions)."""
    app = QApplication.instance()
    if app is not None:
        app.quit()