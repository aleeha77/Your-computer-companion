"""The visible needs bars (Stage 7) — Peeko's stats, on screen, with the robot.

Owner feedback (2026-10-07) was blunt: Peeko has real stats, but you cannot
*see* them. This module is the answer: a compact, translucent panel of six
little bars (:data:`BAR_ORDER`) that sits **with** the robot.

How it is attached (never a separate desktop window of its own):

* it is a Qt child *window* of the avatar window, created with
  ``Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint`` and
  ``Qt.WindowTransparentForInput`` — so it stays out of the taskbar, never
  takes focus, and never steals a click or a drag from the robot;
* :meth:`NeedsBarsPanel.follow` keeps it pinned beside the avatar window, and
  the avatar window calls it on every animation tick, so dragging Peeko around
  drags the bars along;
* it is hidden and shown as one thing with the robot (the avatar window owns
  it), toggled from the right-click menu's **Show Needs Bars** entry.

How it stays honest:

* every number comes from :class:`peeko.emotions.engine.EmotionSnapshot` — the
  same snapshot Check Status and the AI context read — so a bar can never
  disagree with the simulation;
* a bar's length is the stat exactly as the engine reports it on its 0..100
  scale (:func:`bar_percent`); ``100`` always means "as satisfied as this stat
  gets" and ``0`` means "as bad as it gets". ``sleepiness`` is the documented
  *inverse* of the sleep need, so a long sleepiness bar means a sleepy robot —
  the label says so, and :data:`BAR_NOTES` spells it out;
* while Peeko is really asleep the panel shows :data:`SLEEPING_CUE` instead of
  pretending the numbers are frozen for no reason;
* nothing is painted until the first snapshot arrives: the panel says
  :data:`EMPTY_TEXT` rather than drawing invented zeroes.

Everything is sized from Qt's font metrics (:meth:`NeedsBarsPanel.sizeHint`),
never from hard-coded pixels, so the panel is DPI-safe: it scales with the
display's scaling factor exactly like any other Qt text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

#: The six stats, in the order they are shown. The names are the keys of
#: :meth:`peeko.emotions.engine.EmotionSnapshot.stats`.
BAR_ORDER: tuple[str, ...] = (
    "happiness", "energy", "hunger", "boredom", "sleepiness", "friendship",
)

#: Short, human labels for the six bars.
BAR_LABELS: dict[str, str] = {
    "happiness": "Happiness",
    "energy": "Energy",
    "hunger": "Hunger",
    "boredom": "Boredom",
    "sleepiness": "Sleepiness",
    "friendship": "Friendship",
}

#: What each bar *means* on the 0..100 scale, for anyone reading the code (and
#: for the honest tooltip). Two of the six are easy to misread: ``hunger`` is
#: "how fed Peeko is" (100 = full) and ``sleepiness`` is "how sleepy" (100 =
#: exhausted), matching the engine's documented scales.
BAR_NOTES: dict[str, str] = {
    "happiness": "100 = delighted, 50 = neutral",
    "energy": "100 = wide awake, 0 = exhausted",
    "hunger": "100 = full, 0 = starving",
    "boredom": "100 = entertained, 0 = bored stiff",
    "sleepiness": "100 = exhausted, 0 = rested",
    "friendship": "100 = devoted, 0 = strangers",
}

#: Fill colour per bar (the track behind it is translucent, so the panel works
#: on any desktop background).
BAR_COLOURS: dict[str, str] = {
    "happiness": "#ff8fb1",
    "energy": "#ffd166",
    "hunger": "#ff9f45",
    "boredom": "#7fc8f8",
    "sleepiness": "#b39ddb",
    "friendship": "#8ee08a",
}

#: Shown while the needs system has Peeko asleep.
SLEEPING_CUE = "zzz — asleep"
#: The panel's own little title.
PANEL_TITLE = "Peeko's needs"
#: Shown before the first snapshot arrives (never invented numbers).
EMPTY_TEXT = "waiting for Peeko's stats"
#: Gap between the robot and the panel, in points.
GAP_PX = 6


@dataclass(frozen=True)
class BarRow:
    """One bar: the stat key, its label, the engine value and the length."""

    key: str
    label: str
    value: float
    percent: int
    colour: QColor

    @property
    def text(self) -> str:
        """The row as the user reads it, e.g. ``Energy 62%``."""
        return f"{self.label} {self.percent}%"


def clamp_value(value) -> float:
    """The engine value clamped onto the 0..100 scale (never raises)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, number))


def bar_percent(value) -> int:
    """The bar's length in percent: the clamped value, rounded to a whole."""
    return int(round(clamp_value(value)))


def bar_rows(stats: Mapping[str, float] | None) -> list[BarRow]:
    """Build the six :class:`BarRow` values from an engine stats mapping.

    A missing key reads as ``0.0`` (as bad as it gets) rather than silently
    reusing another stat's value; :data:`BAR_ORDER` fixes the order.
    """
    values = stats or {}
    return [
        BarRow(
            key=key,
            label=BAR_LABELS[key],
            value=clamp_value(values.get(key, 0.0)),
            percent=bar_percent(values.get(key, 0.0)),
            colour=QColor(BAR_COLOURS[key]),
        )
        for key in BAR_ORDER
    ]


def bar_text(stats: Mapping[str, float] | None, *, asleep: bool = False) -> str:
    """The panel's whole readout as plain text (used by tests and tooltips)."""
    rows = bar_rows(stats)
    if stats is None:
        return EMPTY_TEXT
    lines = [PANEL_TITLE]
    if asleep:
        lines.append(SLEEPING_CUE)
    lines.extend(row.text for row in rows)
    return "\n".join(lines)


class NeedsBarsPanel(QWidget):
    """The small translucent panel of live bars that sits beside the robot."""

    def __init__(self, parent: QWidget | None = None, *,
                 show_without_activating: bool = True) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        # Clicks and drags belong to the robot underneath: the panel is a view.
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setWindowTitle(PANEL_TITLE)
        if show_without_activating:
            self.setAttribute(Qt.WA_ShowWithoutActivating)
        for key in BAR_ORDER:
            self.setToolTip(f"{BAR_LABELS[key]}: {BAR_NOTES[key]}")
            break
        self._rows: list[BarRow] = bar_rows(None)
        self._stats: Mapping[str, float] | None = None
        self._asleep = False
        self.resize(self.sizeHint())

    # ------------------------------------------------------------------ #
    # Data (one snapshot at a time, straight from the engine)
    # ------------------------------------------------------------------ #
    def update_from_snapshot(self, snapshot) -> None:
        """Show one :class:`peeko.emotions.engine.EmotionSnapshot`."""
        stats = getattr(snapshot, "stats", None)
        values = dict(stats()) if callable(stats) else None
        self.set_stats(values, asleep=bool(getattr(snapshot, "asleep", False)))

    def set_stats(self, stats: Mapping[str, float] | None, *,
                  asleep: bool = False) -> None:
        """Show ``stats`` (a stats mapping) — or nothing when it is ``None``."""
        self._stats = stats
        self._rows = bar_rows(stats)
        self._asleep = bool(asleep)
        self.resize(self.sizeHint())
        self.update()

    @property
    def rows(self) -> tuple[BarRow, ...]:
        """The six bars, in :data:`BAR_ORDER`."""
        return tuple(self._rows)

    @property
    def has_snapshot(self) -> bool:
        """Has a snapshot arrived yet? (The panel paints an honest placeholder
        until one has.)"""
        return self._stats is not None

    @property
    def asleep(self) -> bool:
        """Is the robot really asleep right now (from the last snapshot)?"""
        return self._asleep

    @property
    def cue_text(self) -> str:
        """The sleeping cue, or ``""`` while Peeko is awake."""
        return SLEEPING_CUE if self._asleep else ""

    def percent(self, key: str) -> int:
        """The bar length for one stat key (``KeyError`` if it is not a bar)."""
        for row in self._rows:
            if row.key == key:
                return row.percent
        raise KeyError(f"{key!r} is not a needs bar; known: {', '.join(BAR_ORDER)}")

    def text(self) -> str:
        """Everything the panel shows, as plain text."""
        return bar_text(self._stats, asleep=self._asleep)

    # ------------------------------------------------------------------ #
    # Geometry: sized from font metrics, so it scales with the display
    # ------------------------------------------------------------------ #
    def _bar_width(self, metrics: QFontMetrics) -> int:
        return max(metrics.averageCharWidth() * 9, metrics.height() * 4)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        metrics = self.fontMetrics()
        pad = max(4, metrics.height() // 3)
        label_w = max(
            metrics.horizontalAdvance(BAR_LABELS[key]) for key in BAR_ORDER
        )
        percent_w = metrics.horizontalAdvance("100%")
        gap = max(4, metrics.averageCharWidth())
        bar_h = max(4, int(metrics.height() * 0.45))
        row_h = bar_h + max(2, metrics.height() // 5)
        width = pad * 2 + label_w + gap + self._bar_width(metrics) + gap + percent_w
        height = pad * 2 + metrics.height() + len(BAR_ORDER) * row_h
        return QSize(int(width), int(height))

    def follow(self, window) -> None:
        """Sit beside ``window`` (the robot), preferring its left-hand side.

        Called by the avatar window on every tick, so the bars travel with the
        robot while it is dragged. The panel is clamped into the screen so it
        can never be dragged off the desktop by the robot's own position.
        """
        geo = window.frameGeometry()
        size = self.size()
        x = geo.left() - size.width() - GAP_PX
        y = geo.top()
        screen = window.screen() or self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            if x < available.left():
                x = geo.right() + GAP_PX
            x = min(max(x, available.left()), available.right() - size.width())
            y = min(max(y, available.top()), available.bottom() - size.height())
        self.move(x, y)

    # ------------------------------------------------------------------ #
    # Painting
    # ------------------------------------------------------------------ #
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        metrics = self.fontMetrics()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pad = max(4, metrics.height() // 3)
        # Translucent rounded background: reads on light and dark desktops.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(16, 18, 26, 150))
        painter.drawRoundedRect(self.rect(), 6.0, 6.0)

        if not self.has_snapshot:
            painter.setPen(QColor(235, 235, 245, 230))
            painter.drawText(
                self.rect().adjusted(pad, pad, -pad, -pad),
                Qt.AlignLeft | Qt.AlignVCenter, EMPTY_TEXT,
            )
            return

        label_w = max(
            metrics.horizontalAdvance(BAR_LABELS[key]) for key in BAR_ORDER
        )
        percent_w = metrics.horizontalAdvance("100%")
        gap = max(4, metrics.averageCharWidth())
        bar_w = self._bar_width(metrics)
        bar_h = max(4, int(metrics.height() * 0.45))
        row_h = bar_h + max(2, metrics.height() // 5)
        x = pad
        y = pad

        # Title row: the panel's name, plus the cue when Peeko is asleep.
        painter.setPen(QColor(245, 245, 250, 235))
        painter.drawText(
            x, int(y), label_w + gap + bar_w, metrics.height(),
            Qt.AlignLeft | Qt.AlignVCenter, PANEL_TITLE,
        )
        if self._asleep:
            painter.setPen(QColor(195, 175, 235, 245))
            painter.drawText(
                x, int(y), self.width() - pad * 2, metrics.height(),
                Qt.AlignRight | Qt.AlignVCenter, SLEEPING_CUE,
            )
        y += metrics.height()

        for row in self._rows:
            painter.setPen(QColor(230, 232, 240, 215))
            painter.drawText(
                x, int(y), label_w, bar_h + row_h - bar_h,
                Qt.AlignLeft | Qt.AlignVCenter, row.label,
            )
            bar_x = x + label_w + gap
            track = QColor(255, 255, 255, 45)
            painter.setPen(Qt.NoPen)
            painter.setBrush(track)
            painter.drawRoundedRect(bar_x, y, bar_w, bar_h, bar_h / 2, bar_h / 2)
            filled = bar_w * (row.percent / 100.0)
            if filled > 0:
                painter.setBrush(row.colour)
                painter.drawRoundedRect(
                    bar_x, y, max(filled, bar_h), bar_h, bar_h / 2, bar_h / 2
                )
            painter.setPen(QColor(245, 245, 250, 225))
            painter.drawText(
                int(bar_x + bar_w + gap), int(y), percent_w, bar_h,
                Qt.AlignRight | Qt.AlignVCenter, f"{row.percent}%",
            )
            y += row_h


__all__ = [
    "BAR_COLOURS",
    "BAR_LABELS",
    "BAR_NOTES",
    "BAR_ORDER",
    "BarRow",
    "EMPTY_TEXT",
    "GAP_PX",
    "NeedsBarsPanel",
    "PANEL_TITLE",
    "SLEEPING_CUE",
    "bar_percent",
    "bar_rows",
    "bar_text",
    "clamp_value",
]
