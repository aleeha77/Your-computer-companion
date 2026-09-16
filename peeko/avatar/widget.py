"""Avatar subsystem: the on-screen robot companion.

Stage 0: the avatar is a minimal frameless, translucent, always-on-top
window that draws a simple placeholder creature (a rounded blob with
eyes) — the seed of the future animated avatar. It can be dragged around
the desktop, has a right-click context menu, and quits via the menu,
``Ctrl+Q``, window close, or SIGINT.

Stage 1 (avatar interaction/animation) will replace the static painting
with real animation states driven by the emotions subsystem.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QShortcut,
)
from PySide6.QtWidgets import QWidget

from peeko.ui.context_menu import build_avatar_context_menu

LOG = logging.getLogger("peeko.avatar")

#: Widget size — larger than the blob so there is room for future
#: expressive features (ears, mouth, tail) without changing the window.
WIDTH = 160
HEIGHT = 180


class AvatarWindow(QWidget):
    """Frameless, translucent desktop window showing the placeholder avatar.

    Signals:
        quitRequested: emitted when the user asks Peeko to quit (menu,
            Ctrl+Q, or window-close equivalent).
    """

    quitRequested = Signal()

    def __init__(self, settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._drag_offset: QPoint | None = None

        self.setWindowTitle("Peeko")
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # keep out of the taskbar/dock
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedSize(WIDTH, HEIGHT)

        self._menu = build_avatar_context_menu(self)
        self._menu.triggered.connect(self._on_menu_triggered)

        quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        quit_shortcut.activated.connect(self.quitRequested.emit)
        self.quitRequested.connect(self._quit)

        self._place_on_screen()

    # ------------------------------------------------------------------ #
    # Geometry
    # ------------------------------------------------------------------ #
    def _place_on_screen(self) -> None:
        """Position the window on the primary screen (top-right, inset)."""
        screen = self.screen() or self.windowHandle()
        if screen is None:  # pragma: no cover - defensive, no display yet
            return
        available = screen.availableGeometry()
        margin = 24
        self.move(available.right() - self.width() - margin,
                  available.top() + margin)

    # ------------------------------------------------------------------ #
    # Painting
    # ------------------------------------------------------------------ #
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        """Paint the placeholder avatar: a rounded teal blob with eyes."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        blob = QRectF(12, 18, WIDTH - 24, HEIGHT - 40)

        gradient = QLinearGradient(blob.topLeft(), blob.bottomRight())
        gradient.setColorAt(0.0, QColor("#8FE3C8"))
        gradient.setColorAt(1.0, QColor("#3AAFA9"))

        path = QPainterPath()
        path.addRoundedRect(blob, blob.height() / 2.0, blob.height() / 2.0)
        painter.fillPath(path, gradient)
        painter.setPen(QPen(QColor("#2B7A78"), 2.0))
        painter.drawPath(path)

        # Two simple eyes — Stage 1 replaces this with animated expressions.
        eye_y = blob.top() + blob.height() * 0.36
        for eye_x in (blob.center().x() - 22.0, blob.center().x() + 22.0):
            painter.setBrush(QColor("white"))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPoint(int(eye_x), int(eye_y)), 9, 11)
            painter.setBrush(QColor("#17252E"))
            painter.drawEllipse(QPoint(int(eye_x) + 3, int(eye_y) + 2), 4, 5)

        painter.end()

    # ------------------------------------------------------------------ #
    # Dragging
    # ------------------------------------------------------------------ #
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton:
            self._drag_offset = None
            event.accept()

    # ------------------------------------------------------------------ #
    # Context menu / quit
    # ------------------------------------------------------------------ #
    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._menu.exec(event.globalPos())

    def _on_menu_triggered(self, action) -> None:
        if action.data() == "quit":
            self.quitRequested.emit()

    def _quit(self) -> None:
        LOG.info("Quit requested — closing avatar window.")
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        LOG.info("Avatar window closed.")
        super().closeEvent(event)