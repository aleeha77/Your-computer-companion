"""UI helpers for Peeko: the avatar's right-click interaction menu.

Stage 2 reorganises the right-click menu into the full **interaction menu**
the owner specified: the pet actions (Talk / Feed / Pet / Play / Sleep /
Wake Up) appear in their final places even though they arrive in later
stages — labelled honestly as *not implemented*, and answering with an
explanation of when they land. Two entries work today (**Check Status**,
**Settings**), and **Quit** stays exactly as it was.

Everything is described by :data:`MENU_SPEC` — a single, inspectable data
structure the Qt builder consumes, so "what the menu shows" can be tested
without opening a menu. There are no dead buttons: every entry either does
something real or says plainly that it does not exist yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtWidgets import QApplication, QMenu

from peeko import __stage__, __total_stages__, __version__

# --------------------------------------------------------------------------- #
# Action ids (the value carried in ``QAction.setData``)
# --------------------------------------------------------------------------- #
STATUS_ID = "check_status"
SETTINGS_ID = "settings"
QUIT_ID = "quit"


@dataclass(frozen=True)
class MenuEntry:
    """One entry of the avatar's interaction menu.

    :param id: stable identifier carried by the ``QAction`` (``setData``).
    :param label: text shown in the menu.
    :param stage: the roadmap stage that implements it, or ``None`` when it
        works today.
    :param summary: one-line, user-facing description (used by the honest
        "not implemented yet" dialog).
    """

    id: str
    label: str
    stage: Optional[int] = None
    summary: str = ""

    @property
    def implemented(self) -> bool:
        """Does this entry do real work in the current build?"""
        return self.stage is None

    @property
    def display_label(self) -> str:
        """Menu text — future entries say so right in the menu."""
        if self.implemented:
            return self.label
        return f"{self.label} — Stage {self.stage} (not implemented)"

    @property
    def description(self) -> str:
        """Long-form description for dialogs (no false promises)."""
        if self.implemented:
            return self.summary or self.label
        note = f" arrives in Stage {self.stage} of {__total_stages__}"
        detail = f" ({self.summary})" if self.summary else ""
        return f"{self.label} is not implemented yet — it{note}{detail}."


#: The complete menu, in order. ``None`` is a separator.
#:
#: Stage 2 keeps every planned pet action visible so the roadmap is legible
#: from the app itself, marks the future ones in their own label, and groups
#: them above a separator so "what works" is obvious at a glance.
MENU_SPEC: tuple[Optional[MenuEntry], ...] = (
    MenuEntry("talk", "Talk", 3, "conversational AI chat with personality"),
    MenuEntry("feed", "Feed", 7, "hunger, from the virtual-pet needs system"),
    MenuEntry("pet", "Pet", 6, "petting reactions from the emotions system"),
    MenuEntry("play", "Play", 6, "play reactions from the emotions system"),
    MenuEntry("sleep", "Sleep", 7, "sleep and energy from the needs system"),
    MenuEntry("wake", "Wake Up", 7, "waking up, from the needs system"),
    None,  # separator: planned features above, working ones below
    MenuEntry(STATUS_ID, "Check Status…", None,
              "what Peeko is doing right now"),
    MenuEntry(SETTINGS_ID, "Settings…", None,
              "the configuration Peeko is running with"),
    None,  # separator before Quit
    MenuEntry(QUIT_ID, "Quit", None, "close Peeko"),
)

#: Menu entries only (no separators), in menu order.
MENU_ENTRIES: tuple[MenuEntry, ...] = tuple(
    entry for entry in MENU_SPEC if entry is not None
)

_CONTROL_IDS = (STATUS_ID, SETTINGS_ID, QUIT_ID)


def entries() -> tuple[MenuEntry, ...]:
    """Every menu entry, in order."""
    return MENU_ENTRIES


def future_entries() -> tuple[MenuEntry, ...]:
    """Entries that are shown but not implemented yet."""
    return tuple(e for e in MENU_ENTRIES if not e.implemented)


def find_entry(action_id) -> Optional[MenuEntry]:
    """Look an entry up by the ``QAction`` data value; ``None`` if unknown."""
    if not isinstance(action_id, str):
        return None
    return next((e for e in MENU_ENTRIES if e.id == action_id), None)


def header_text() -> str:
    """The menu's informational header line."""
    return f"Peeko v{__version__} — Stage {__stage__} of {__total_stages__}"


def build_avatar_context_menu(parent) -> QMenu:
    """Build the avatar's right-click interaction menu.

    The menu is honest about the current stage: an informational header
    lists the running version, planned entries are labelled with the stage
    that implements them, and every entry either works or says it doesn't.
    Wiring the triggers is the caller's job (see
    :class:`peeko.avatar.widget.AvatarWindow`).
    """
    menu = QMenu(parent)
    menu.setToolTipsVisible(True)

    header = menu.addAction(header_text())
    header.setEnabled(False)
    header.setData("header")
    menu.addSeparator()

    for entry in MENU_SPEC:
        if entry is None:
            menu.addSeparator()
            continue
        action = menu.addAction(entry.display_label)
        action.setData(entry.id)
        if entry.implemented:
            if entry.id == QUIT_ID:
                action.setShortcut("Ctrl+Q")
        else:
            action.setToolTip(
                f"Not implemented yet — planned for Stage {entry.stage}."
            )
    return menu


def quit_application() -> None:
    """Quit the running QApplication (used by menu actions)."""
    app = QApplication.instance()
    if app is not None:
        app.quit()


def action_ids(menu: QMenu) -> list[Optional[str]]:
    """The data values of every action in ``menu``, in order (separators too).

    Convenience for tests and diagnostics: separators appear as ``None``.
    """
    return [action.data() for action in menu.actions()]


def separator_positions(menu: QMenu) -> list[int]:
    """Indexes of separators within ``menu.actions()``."""
    return [i for i, action in enumerate(menu.actions()) if action.isSeparator()]


__all__ = [
    "MENU_ENTRIES",
    "MENU_SPEC",
    "QUIT_ID",
    "SETTINGS_ID",
    "STATUS_ID",
    "MenuEntry",
    "action_ids",
    "build_avatar_context_menu",
    "entries",
    "find_entry",
    "future_entries",
    "header_text",
    "quit_application",
    "separator_positions",
]
