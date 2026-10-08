"""The Feed picker (Stage 7): an honest little food menu for Peeko.

The right-click menu's **Feed** entry opens this dialog — a normal (non-modal)
window listing the five foods from :data:`peeko.needs.system.FOODS`, each with
its documented deltas, so the user can see exactly what eating it does before
choosing. Picking a food calls back into the avatar window, which feeds Peeko
through the emotion engine; the engine is the only thing that decides whether
the food is accepted, so a refusal ("Peeko is full") is real and reported by
the engine rather than guessed here.

Nothing in this dialog is decorative: every button feeds a real food, the
numbers come from the catalogue, and the note line states plainly when a
refusal is likely. The text builders (:func:`food_rows`, :func:`picker_note`)
are plain functions so the honesty of the dialog is unit-testable without a
display.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from peeko import __app_name__
from peeko.needs.system import FOOD_ORDER, FOODS

#: What the whole dialog is for (also the dialog's window-title suffix).
PICKER_TITLE = "Feed"

#: The line under the food list, always shown (never a claim about state).
PICKER_FOOTNOTE = (
    "Picking a food feeds Peeko through the needs system: hunger, energy, "
    "happiness and the rest change for real. If he is full or asleep he will "
    "refuse, and Peeko will say so instead of wasting the food."
)


def food_rows() -> list[tuple[str, str]]:
    """``(label, documented deltas)`` for every food, in menu order."""
    rows: list[tuple[str, str]] = []
    for food_id in FOOD_ORDER:
        food = FOODS[food_id]
        rows.append((f"{food.emoji} {food.label}", food.detail()))
    return rows


def picker_note(*, asleep: bool = False, full: bool = False) -> str:
    """The honest state note at the top of the picker.

    It only ever reports what the engine already knows: whether Peeko is
    asleep, and whether he is full enough to refuse. Both are stated as
    *likely* refusals — the engine has the final word.
    """
    if asleep:
        return (
            "Peeko is asleep right now. He would sleep through the food, so "
            "choose Wake Up in the right-click menu first — nothing is wasted "
            "by waiting."
        )
    if full:
        return (
            "Peeko is full right now, so he would refuse anything you pick "
            "here. Offer food again once his hunger has had time to fall."
        )
    return "Peeko is awake and has room for something."


class FoodPickerDialog(QDialog):
    """Non-modal list of the Stage 7 foods, with their real effects."""

    def __init__(self, parent=None, *, on_choose=None, asleep: bool = False,
                 full: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{__app_name__} — {PICKER_TITLE}")
        self.setWindowFlag(Qt.Window, True)
        self.setModal(False)
        self._on_choose = on_choose
        #: The food ids chosen so far (in order) — used by the tests and logs.
        self.chosen: list[str] = []

        layout = QVBoxLayout(self)
        heading = QLabel("What should Peeko eat?")
        heading.setStyleSheet("font-weight: bold;")
        layout.addWidget(heading)

        note = QLabel(picker_note(asleep=asleep, full=full))
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        layout.addWidget(note)

        self._buttons: dict[str, QPushButton] = {}
        for food_id in FOOD_ORDER:
            food = FOODS[food_id]
            button = QPushButton(f"{food.emoji} {food.label} — {food.summary}")
            button.setToolTip(f"{food.detail()}")
            button.clicked.connect(
                lambda _checked=False, fid=food_id: self.choose(fid)
            )
            layout.addWidget(button)
            self._buttons[food_id] = button

        deltas = QLabel(
            "\n".join(f"{label}: {detail}" for label, detail in food_rows())
        )
        deltas.setWordWrap(True)
        deltas.setStyleSheet("color: palette(mid);")
        layout.addWidget(deltas)

        footnote = QLabel(PICKER_FOOTNOTE)
        footnote.setWordWrap(True)
        layout.addWidget(footnote)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        layout.addWidget(buttons)
        self.setMinimumWidth(460)

    # ------------------------------------------------------------------ #
    # Introspection (used by the tests and by the avatar window)
    # ------------------------------------------------------------------ #
    def food_ids(self) -> list[str]:
        """The food ids this dialog offers, in menu order."""
        return list(self._buttons)

    def button(self, food_id: str) -> QPushButton:
        """The button for one food (KeyError when it is not on the menu)."""
        return self._buttons[food_id]

    def choose(self, food_id: str) -> bool:
        """Feed ``food_id`` now: hand it to the callback and close.

        Returns ``True`` when the choice was handed over. An unknown food id
        does nothing (and returns ``False``) — there is no fake food.
        """
        if food_id not in self._buttons:
            return False
        self.chosen.append(food_id)
        if self._on_choose is not None:
            self._on_choose(food_id)
        self.close()
        return True

    def text(self) -> str:
        """Everything the dialog says, as plain text (for tests)."""
        return "\n".join(
            widget.text()
            for widget in self.findChildren(QLabel)
        )


def build_food_picker(parent, on_choose=None, *, asleep: bool = False,
                      full: bool = False) -> FoodPickerDialog:
    """Build (but do not show) the Feed picker."""
    return FoodPickerDialog(
        parent, on_choose=on_choose, asleep=asleep, full=full
    )


def show_food_picker(parent, on_choose=None, *, asleep: bool = False,
                     full: bool = False) -> FoodPickerDialog:
    """Show the Feed picker without blocking the animation loop."""
    dialog = build_food_picker(
        parent, on_choose, asleep=asleep, full=full
    )
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


__all__ = [
    "FoodPickerDialog",
    "PICKER_FOOTNOTE",
    "PICKER_TITLE",
    "build_food_picker",
    "food_rows",
    "picker_note",
    "show_food_picker",
]
