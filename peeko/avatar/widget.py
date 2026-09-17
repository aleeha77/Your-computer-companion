"""Avatar subsystem: the on-screen robot companion.

Stage 1: the avatar is now a real little character — a transparent,
frameless, always-on-top window that renders the animated robot described
by ``peeko/avatar/assets/manifest.json`` and driven by the
:class:`~peeko.avatar.state_machine.AvatarStateMachine`:

* gentle idle bob (looping animation from the manifest);
* natural-ish blinking on an irregular timer;
* occasional looks left/right/up/down, plus glancing toward the mouse
  cursor when it hovers nearby;
* a happy squash-and-bounce when clicked;
* a "being carried" wiggle while dragged anywhere on the desktop;
* right-click context menu / Ctrl+Q / window close quit.

Everything animation-related runs on ``QTimer`` callbacks — no sleeps, no
blocking calls. AI/voice/needs input arrives in later stages as new states
on the same machine.
"""

from __future__ import annotations

import logging
import random
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCursor,
    QKeySequence,
    QPainter,
    QShortcut,
)
from PySide6.QtWidgets import QWidget

from peeko.avatar.assets import AssetLibrary
from peeko.avatar.manifest import load_manifest
from peeko.avatar.renderer import draw_frame
from peeko.avatar.state_machine import IDLE, AvatarStateMachine
from peeko.ui.context_menu import build_avatar_context_menu

LOG = logging.getLogger("peeko.avatar")

#: Where the packaged placeholder artwork lives (next to this module).
DEFAULT_ASSETS_DIR = Path(__file__).resolve().parent / "assets"

#: Animation tick interval (ms) — 30 fps is plenty for a tiny character.
TICK_INTERVAL_MS = 33
#: How often we check where the mouse cursor is (for glances).
POINTER_POLL_MS = 150
#: Pointer distance from the window centre that triggers a glance (px).
POINTER_GLANCE_RADIUS = 320
#: Mouse movement (px) that separates a click from a drag.
DRAG_THRESHOLD_PX = 6


class AvatarWindow(QWidget):
    """Frameless, translucent, always-on-top window showing the avatar.

    Signals:
        quitRequested: emitted when the user asks Peeko to quit (menu,
            Ctrl+Q, or window-close equivalent).
    """

    quitRequested = Signal()

    def __init__(self, settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._drag_offset: QPoint | None = None
        self._press_global: QPoint | None = None
        self._press_active = False
        self._drag_active = False
        self._last_frame_key: tuple | None = None

        # ---- asset pipeline: manifest -> pixmaps -> machine ---------------- #
        # ``PEEKO_AVATAR_ASSETS_DIR`` lets the owner point Peeko at their own
        # artwork folder; empty means "use the artwork shipped in the package".
        assets_dir = Path(
            getattr(settings, "avatar_assets_dir", None) or DEFAULT_ASSETS_DIR
        )
        self._manifest = load_manifest(assets_dir / "manifest.json")
        self._assets = AssetLibrary(self._manifest)
        self._assets.load()
        self._machine = AvatarStateMachine(self._manifest, rng=random.Random())
        LOG.info(
            "Avatar ready: %d animation(s), %d layer(s), canvas %dx%d.",
            len(self._manifest.animations),
            len(self._manifest.z_order),
            self._manifest.canvas_width,
            self._manifest.canvas_height,
        )

        # ---- window -------------- --------------------------------------- #
        self.setWindowTitle("Peeko")
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # keep out of the taskbar/dock
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedSize(
            self._manifest.canvas_width, self._manifest.canvas_height
        )

        self._menu = build_avatar_context_menu(self)
        self._menu.triggered.connect(self._on_menu_triggered)

        quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        quit_shortcut.activated.connect(self.quitRequested.emit)
        self.quitRequested.connect(self._quit)

        self._place_on_screen()

        # ---- animation loop (QTimer-driven; never blocks) ------------------ #
        self._elapsed = QElapsedTimer()
        self._elapsed.start()
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(TICK_INTERVAL_MS)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start()

        self._pointer_timer = QTimer(self)
        self._pointer_timer.setInterval(POINTER_POLL_MS)
        self._pointer_timer.timeout.connect(self._on_pointer_poll)
        self._pointer_timer.start()

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
    # Animation loop
    # ------------------------------------------------------------------ #
    def _on_tick(self) -> None:
        """One animation tick: advance the state machine, repaint if needed."""
        dt_ms = self._elapsed.restart()
        self._machine.tick(dt_ms)
        key = (self._machine.state, self._machine.frame_index)
        if key != self._last_frame_key:
            self._last_frame_key = key
            self.update()

    def _on_pointer_poll(self) -> None:
        """Cheap "taste" of cursor awareness: glance toward a nearby cursor."""
        if self._press_active or self._machine.state != IDLE:
            return
        cursor = QCursor.pos()
        center = self.frameGeometry().center()
        delta = cursor - center
        if abs(delta.x()) + abs(delta.y()) > POINTER_GLANCE_RADIUS:
            return
        if abs(delta.x()) > abs(delta.y()):
            direction = "left" if delta.x() < 0 else "right"
        else:
            direction = "up" if delta.y() < 0 else "down"
        self._machine.pointer_direction(direction)

    # ------------------------------------------------------------------ #
    # Painting
    # ------------------------------------------------------------------ #
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        """Paint the current animation frame from the asset manifest."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        draw_frame(painter, self._assets, self._machine.frame)
        painter.end()

    # ------------------------------------------------------------------ #
    # Mouse interaction (click vs drag)
    # ------------------------------------------------------------------ #
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton:
            self._press_active = True
            self._drag_active = False
            self._press_global = event.globalPosition().toPoint()
            self._drag_offset = (
                self._press_global - self.frameGeometry().topLeft()
            )
            self._machine.press()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if not (self._press_active and event.buttons() & Qt.LeftButton):
            return
        if not self._drag_active:
            delta = event.globalPosition().toPoint() - self._press_global
            if delta.manhattanLength() > DRAG_THRESHOLD_PX:
                self._drag_active = True
                self._machine.drag_started()
        if self._drag_active:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton and self._press_active:
            self._press_active = False
            self._machine.release(moved=self._drag_active)
            self._drag_offset = None
            self._press_global = None
            self._drag_active = False
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