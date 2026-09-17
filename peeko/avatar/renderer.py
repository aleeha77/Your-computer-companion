"""Avatar rendering: composite the current animation frame.

:func:`draw_frame` paints one frame of the running animation onto a
``QPainter``: a soft ground shadow (fixed, so a bobbing character appears
to float) plus every layer pixmap in manifest z-order, offset by the
frame's ``dx``/``dy``.

The renderer is deliberately dumb: it draws whatever
:class:`peeko.avatar.state_machine.AvatarStateMachine` says is the current
frame. Swapping artwork never touches this module.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPainter

from peeko.avatar.assets import AssetLibrary
from peeko.avatar.manifest import Frame

#: Where the drop shadow sits. The character artwork is 160x180 with the
#: feet around y≈170; the shadow stays put while ``dy`` bobs the sprite.
_SHADOW_CENTER = QPoint(80, 174)
_SHADOW_RX = 34
_SHADOW_RY = 5


def draw_frame(painter: QPainter, assets: AssetLibrary, frame: Frame) -> None:
    """Paint ``frame`` at the origin, offset by its dx/dy.

    Caller owns the ``QPainter`` and must have started it on the target
    widget/pixmap.
    """
    dx, dy = frame.dx, frame.dy

    # Ground shadow — fixed position, lighter while the character is lifted
    # (dy < 0) and firmer once it settles back down (dy > 0), so a bobbing
    # sprite reads as floating above the desktop instead of pasted on it.
    shadow_alpha = max(12, 46 + int(dy) * 5)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(0, 0, 0, shadow_alpha))
    painter.drawEllipse(_SHADOW_CENTER, _SHADOW_RX, _SHADOW_RY)

    for _name, pixmap in assets.frame_layers(frame):
        painter.drawPixmap(int(dx), int(dy), pixmap)