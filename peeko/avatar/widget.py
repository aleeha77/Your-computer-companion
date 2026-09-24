"""Avatar subsystem: the on-screen robot companion.

Stage 1: the avatar is a real little character — a transparent, frameless,
always-on-top window that renders the animated robot described by
``peeko/avatar/assets/manifest.json`` and driven by the
:class:`~peeko.avatar.state_machine.AvatarStateMachine`:

* gentle idle bob (looping animation from the manifest);
* natural-ish blinking on an irregular timer;
* occasional looks left/right/up/down, plus glancing toward the mouse
  cursor when it hovers nearby;
* a happy squash-and-bounce when clicked;
* a "being carried" wiggle while dragged anywhere on the desktop.

Stage 2 adds the interaction layer:

* a subtle **hover** reaction when the pointer enters the window (it never
  interferes with a click or a drag, and ends when the pointer leaves);
* a distinct **double-click** reaction (a bigger, winkier bounce);
* the full right-click **interaction menu** — Check Status and Settings open
  real, honest windows; the planned pet actions are labelled as not
  implemented and answer with an explanation plus a "huh?" reaction;
* right-click menu / Ctrl+Q / window close quit, exactly as before.

Everything animation-related runs on ``QTimer`` callbacks — no sleeps, no
blocking calls. Dialogs are opened asynchronously (``open()``/``show()``),
so nothing ever blocks the animation loop. AI/voice/needs input arrives in
later stages as new states on the same machine.
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
from peeko.ui.context_menu import (
    QUIT_ID,
    SETTINGS_ID,
    STATUS_ID,
    build_avatar_context_menu,
    find_entry,
)
from peeko.ui.dialogs import (
    show_not_implemented_dialog,
    show_settings_dialog,
    show_status_dialog,
)

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
    #: Emitted when a menu entry that is not implemented yet is chosen
    #: (carries the entry id) — useful for logging and tests.
    notImplementedRequested = Signal(str)

    def __init__(self, settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._drag_offset: QPoint | None = None
        self._press_global: QPoint | None = None
        self._press_active = False
        self._drag_active = False
        self._last_frame_key: tuple | None = None
        self._dialogs: list = []
        self._settings_dialog = None

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
    # Mouse interaction (hover / click / double-click vs drag)
    # ------------------------------------------------------------------ #
    def enterEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        """Pointer entered the avatar -> a subtle "oh!" reaction.

        The state machine decides whether it is a good moment (it stays
        quiet while a press/drag is in flight), so this never interferes
        with clicking or dragging.
        """
        if self._machine.hover_enter():
            LOG.debug("Hover reaction triggered.")

    def leaveEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        """Pointer left the avatar -> end a running hover reaction at once."""
        if self._machine.hover_leave():
            LOG.debug("Hover reaction cut short by pointer leaving.")

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton:
            self._arm_press(event.globalPosition().toPoint())
            event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """A double-click plays its own, more excited reaction.

        Qt delivers press -> release (which already fired the single-click
        reaction) -> double-click -> release. Re-arming the press here keeps
        "double-click and then drag" working, and the state machine refuses
        to downgrade the double-click reaction to a plain click when the
        following release arrives.
        """
        if event.button() != Qt.LeftButton:
            return
        self._arm_press(event.globalPosition().toPoint())
        if self._machine.double_click():
            LOG.debug("Double-click reaction triggered.")
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

    def _arm_press(self, global_pos: QPoint) -> None:
        """Remember where a left-button interaction started (click or drag)."""
        self._press_active = True
        self._drag_active = False
        self._press_global = global_pos
        self._drag_offset = global_pos - self.frameGeometry().topLeft()
        self._machine.press()

    # ------------------------------------------------------------------ #
    # Interaction menu / dialogs / quit
    # ------------------------------------------------------------------ #
    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._menu.exec(event.globalPos())

    def _on_menu_triggered(self, action) -> None:
        """Dispatch a menu entry by its stable id (see ``ui.context_menu``)."""
        action_id = action.data()
        entry = find_entry(action_id)
        if action_id == QUIT_ID:
            self.quitRequested.emit()
        elif action_id == STATUS_ID:
            self._show_status()
        elif action_id == SETTINGS_ID:
            self._show_settings()
        elif entry is not None and not entry.implemented:
            self._on_not_implemented(entry)
        else:  # pragma: no cover - defensive: unknown action id
            LOG.warning("Unknown menu action id: %r", action_id)

    def _show_status(self) -> None:
        """Check Status: an honest readout of live state."""
        LOG.info("Showing status readout.")
        self._keep_dialog(
            show_status_dialog(
                self, self._settings, machine=self._machine,
                manifest=self._manifest,
            )
        )

    def _show_settings(self) -> None:
        """Settings: the (read-only) configuration Peeko is running with."""
        LOG.info("Showing settings window.")
        if self._settings_dialog is None:
            self._settings_dialog = show_settings_dialog(self, self._settings)
        else:
            self._settings_dialog.show()
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()

    def _on_not_implemented(self, entry) -> None:
        """A planned action: react honestly instead of pretending."""
        LOG.info("Menu entry %r is not implemented (Stage %s).",
                 entry.id, entry.stage)
        self._machine.confused()
        self.notImplementedRequested.emit(entry.id)
        self._keep_dialog(show_not_implemented_dialog(self, entry))

    def _keep_dialog(self, dialog) -> None:
        """Hold a reference so an async dialog isn't collected mid-display."""
        alive = []
        for existing in self._dialogs:
            try:
                if existing.isVisible():
                    alive.append(existing)
            except RuntimeError:  # pragma: no cover - already deleted by Qt
                continue
        alive.append(dialog)
        self._dialogs = alive

    def _quit(self) -> None:
        LOG.info("Quit requested — closing avatar window.")
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        LOG.info("Avatar window closed.")
        super().closeEvent(event)