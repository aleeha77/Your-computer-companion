"""Renderer and ``AvatarWindow`` tests (offscreen Qt).

These cover the Qt half of the avatar: painting a frame onto a painter,
and the transparent, frameless, always-on-top window that drives the
state machine from a ``QTimer`` and turns mouse input into click/drag
behaviour. Everything runs on the offscreen platform plugin via the
``qapp`` fixture, so no display (and no visible window) is required.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QWidget

from peeko.avatar.assets import AssetLibrary
from peeko.avatar.manifest import load_manifest
from peeko.avatar.renderer import draw_frame
from peeko.avatar.state_machine import CLICK, DRAGGING, IDLE
from peeko.avatar.widget import (
    DEFAULT_ASSETS_DIR,
    TICK_INTERVAL_MS,
    AvatarWindow,
)
from peeko.errors import StartupError
from peeko.settings import Settings

PACKAGED_MANIFEST = DEFAULT_ASSETS_DIR / "manifest.json"

#: The avatar's soft ground shadow is drawn here (see renderer.py).
SHADOW_POINT = QPoint(80, 174)


@pytest.fixture()
def settings():
    """Real settings; the avatar uses the packaged artwork by default."""
    return Settings()


@pytest.fixture()
def packaged_assets(qapp):
    """A loaded asset library for the artwork that ships with Peeko."""
    library = AssetLibrary(load_manifest(PACKAGED_MANIFEST))
    library.load()
    return library


@pytest.fixture()
def test_assets(qapp, avatar_assets):
    manifest = load_manifest(avatar_assets)
    library = AssetLibrary(manifest)
    library.load()
    return library, manifest


def render_to_image(assets, frame) -> QImage:
    """Run the real paint path into an image and hand it back."""
    image = QImage(160, 180, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    try:
        draw_frame(painter, assets, frame)
    finally:
        painter.end()
    return image


def _mouse(kind, local, glob, button, buttons) -> QMouseEvent:
    return QMouseEvent(
        kind, QPointF(*local), QPointF(*glob), button, buttons, Qt.NoModifier
    )


# --------------------------------------------------------------------------- #
# Renderer
# --------------------------------------------------------------------------- #
def test_draw_frame_paints_the_character_and_its_shadow(packaged_assets):
    manifest = load_manifest(PACKAGED_MANIFEST)
    image = render_to_image(packaged_assets, manifest.animations["idle"].frames[0])

    shadow_alpha = image.pixelColor(SHADOW_POINT).alpha()
    assert shadow_alpha > 0, "the ground shadow was not painted"

    opaque = sum(
        1
        for y in range(0, image.height(), 4)
        for x in range(0, image.width(), 4)
        if image.pixelColor(x, y).alpha() > 0
    )
    assert opaque > 0, "no layer pixels were painted"
    # The frame is mostly transparent — it is a floating character, not a
    # painted rectangle.
    assert opaque < (image.width() // 4) * (image.height() // 4)


def test_draw_frame_offsets_the_sprite_but_not_the_shadow(test_assets):
    assets, manifest = test_assets
    base = manifest.animations["idle"].frames[0]
    lifted = replace(base, dy=-12)

    base_image = render_to_image(assets, base)
    lifted_image = render_to_image(assets, lifted)

    assert base_image != lifted_image, "dy did not move the sprite"
    # The shadow stays on the ground under the character, but grows lighter
    # as the character lifts, so the bob reads as floating.
    grounded_alpha = base_image.pixelColor(SHADOW_POINT).alpha()
    lifted_alpha = lifted_image.pixelColor(SHADOW_POINT).alpha()
    assert lifted_alpha > 0, "the shadow disappeared entirely"
    assert lifted_alpha < grounded_alpha


def test_draw_frame_uses_the_layer_library_for_every_frame(test_assets):
    assets, manifest = test_assets
    for anim in manifest.animations.values():
        for frame in anim.frames:
            assert not render_to_image(assets, frame).isNull()


# --------------------------------------------------------------------------- #
# AvatarWindow
# --------------------------------------------------------------------------- #
def test_avatar_window_is_frameless_translucent_and_on_top(qapp, settings):
    window = AvatarWindow(settings)
    try:
        assert window.windowTitle() == "Peeko"
        flags = window.windowFlags()
        assert flags & Qt.FramelessWindowHint
        assert flags & Qt.WindowStaysOnTopHint
        assert window.testAttribute(Qt.WA_TranslucentBackground)
        # Sized to the artwork canvas so layers stack pixel-perfectly.
        assert (window.width(), window.height()) == (
            window._manifest.canvas_width,
            window._manifest.canvas_height,
        )
        assert (window.width(), window.height()) == (160, 180)
    finally:
        _destroy(window, qapp)


class _TickSpy:
    """Wraps the state machine and records the deltas the timer delivers."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.deltas: list[float] = []

    def tick(self, dt_ms: float) -> None:
        self.deltas.append(dt_ms)
        self.inner.tick(dt_ms)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_avatar_window_drives_the_state_machine_on_a_timer(qapp, settings):
    window = AvatarWindow(settings)
    try:
        assert window._tick_timer.isActive()
        assert window._tick_timer.interval() == TICK_INTERVAL_MS

        spy = _TickSpy(window._machine)
        window._machine = spy
        for _ in range(3):
            window._on_tick()
        assert len(spy.deltas) == 3
        assert all(delta >= 0 for delta in spy.deltas)

        # Real time passes between timer callbacks: the widget measures it
        # (QElapsedTimer) and hands it to the engine as a delta.
        time.sleep(0.05)
        window._on_tick()
        assert spy.deltas[-1] >= 20

        # ...and once the engine moves to a new frame, the widget repaints.
        before = window._last_frame_key
        for _ in range(14):  # 14 x 100 ms: past the 1300 ms first idle frame
            spy.inner.tick(100.0)
        window._on_tick()
        assert window._last_frame_key == (spy.state, spy.frame_index)
        assert window._last_frame_key != before
    finally:
        _destroy(window, qapp)


def test_avatar_window_paints_a_frame(qapp, settings):
    window = AvatarWindow(settings)
    try:
        first = window.grab().toImage()
        assert not first.isNull()
        assert (first.width(), first.height()) == (160, 180)
        # A click swaps the artwork mid-animation: the painted frame changes.
        window._machine.press()
        window._machine.release(moved=False)
        assert window._machine.state == CLICK
        window._on_tick()
        assert window.grab().toImage() != first
    finally:
        _destroy(window, qapp)


def test_avatar_window_has_a_quit_signal(qapp, settings):
    class SpyWindow(AvatarWindow):
        """Records the close so the test never touches a deleted widget."""

        def __init__(self, settings):
            super().__init__(settings)
            self.closed = False

        def closeEvent(self, event):  # noqa: N802 - Qt naming
            self.closed = True
            super().closeEvent(event)

    window = SpyWindow(settings)
    try:
        # The signal exists and is wired to closing the window (Qt's
        # QShortcut for Ctrl+Q activates exactly this signal).
        assert hasattr(window, "quitRequested")
        window.quitRequested.emit()
        assert window.closed
    finally:
        try:
            window.hide()
        except RuntimeError:  # pragma: no cover - already deleted
            pass
        qapp.processEvents()


def test_avatar_window_is_draggable_once_past_the_threshold(qapp, settings):
    window = AvatarWindow(settings)
    try:
        origin = window.pos()
        grab_point = QPoint(5, 5)
        press_global = origin + grab_point

        window.mousePressEvent(
            _mouse(
                QEvent.MouseButtonPress,
                (grab_point.x(), grab_point.y()),
                (press_global.x(), press_global.y()),
                Qt.LeftButton,
                Qt.LeftButton,
            )
        )
        assert window._drag_active is False
        assert window._machine.state == IDLE  # a press alone is silent

        # A 2 px wobble is not a drag (so shaky hands still count as clicks).
        wobble = press_global + QPoint(2, 0)
        window.mouseMoveEvent(
            _mouse(
                QEvent.MouseMove,
                (7, 5),
                (wobble.x(), wobble.y()),
                Qt.NoButton,
                Qt.LeftButton,
            )
        )
        assert window._drag_active is False
        assert window._machine.state == IDLE

        # A real move starts the "being carried" wiggle and drags the window.
        target = press_global + QPoint(120, 40)
        window.mouseMoveEvent(
            _mouse(
                QEvent.MouseMove,
                (125, 45),
                (target.x(), target.y()),
                Qt.NoButton,
                Qt.LeftButton,
            )
        )
        assert window._drag_active is True
        assert window._machine.state == DRAGGING
        assert window.pos() == target - grab_point

        window.mouseReleaseEvent(
            _mouse(
                QEvent.MouseButtonRelease,
                (125, 45),
                (target.x(), target.y()),
                Qt.LeftButton,
                Qt.NoButton,
            )
        )
        assert window._machine.state == IDLE
        assert window._drag_active is False
    finally:
        _destroy(window, qapp)


def test_avatar_window_click_reacts_instead_of_moving(qapp, settings):
    window = AvatarWindow(settings)
    try:
        origin = window.pos()
        press_global = origin + QPoint(4, 4)
        window.mousePressEvent(
            _mouse(
                QEvent.MouseButtonPress,
                (4, 4),
                (press_global.x(), press_global.y()),
                Qt.LeftButton,
                Qt.LeftButton,
            )
        )
        window.mouseReleaseEvent(
            _mouse(
                QEvent.MouseButtonRelease,
                (4, 4),
                (press_global.x(), press_global.y()),
                Qt.LeftButton,
                Qt.NoButton,
            )
        )
        assert window._machine.state == CLICK
        assert window.pos() == origin  # a click never moves the window
    finally:
        _destroy(window, qapp)


def test_avatar_window_ignores_right_button_drags(qapp, settings):
    window = AvatarWindow(settings)
    try:
        origin = window.pos()
        window.mousePressEvent(
            _mouse(
                QEvent.MouseButtonPress,
                (4, 4),
                (origin.x() + 4, origin.y() + 4),
                Qt.RightButton,
                Qt.RightButton,
            )
        )
        assert window._press_active is False
        assert window._machine.state == IDLE
    finally:
        _destroy(window, qapp)


def test_avatar_window_uses_the_configured_asset_folder(qapp, test_assets,
                                                        avatar_assets):
    """``PEEKO_AVATAR_ASSETS_DIR`` swaps in the owner's own artwork."""
    _assets, manifest = test_assets
    settings = Settings(avatar_assets_dir=Path(avatar_assets).parent)
    window = AvatarWindow(settings)
    try:
        assert window._manifest.base_dir == Path(avatar_assets).parent
        assert window._manifest.canvas_width == manifest.canvas_width
    finally:
        _destroy(window, qapp)


def test_avatar_window_reports_a_broken_asset_folder(qapp, tmp_path):
    """A missing manifest fails loudly instead of drawing an empty window."""
    settings = Settings(avatar_assets_dir=tmp_path / "no-such-art")
    with pytest.raises(StartupError, match="manifest"):
        AvatarWindow(settings)


def _destroy(window: QWidget, qapp) -> None:
    """Tear a test window down without leaving timers behind."""
    try:
        window.hide()
        window.deleteLater()
    except RuntimeError:  # pragma: no cover - already deleted by Qt
        pass
    qapp.processEvents()
