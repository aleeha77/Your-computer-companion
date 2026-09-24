"""UI subsystem package (menus, dialogs, overlays)."""

from peeko.ui.context_menu import (
    MENU_ENTRIES,
    MENU_SPEC,
    QUIT_ID,
    SETTINGS_ID,
    STATUS_ID,
    MenuEntry,
    build_avatar_context_menu,
    find_entry,
    future_entries,
    quit_application,
)
from peeko.ui.dialogs import (
    SETTINGS_NOTE,
    SettingsDialog,
    build_not_implemented_dialog,
    build_settings_dialog,
    build_status_dialog,
    build_status_text,
    settings_rows,
    show_not_implemented_dialog,
    show_settings_dialog,
    show_status_dialog,
)

__all__ = [
    "MENU_ENTRIES",
    "MENU_SPEC",
    "QUIT_ID",
    "SETTINGS_ID",
    "SETTINGS_NOTE",
    "STATUS_ID",
    "MenuEntry",
    "SettingsDialog",
    "build_avatar_context_menu",
    "build_not_implemented_dialog",
    "build_settings_dialog",
    "build_status_dialog",
    "build_status_text",
    "find_entry",
    "future_entries",
    "quit_application",
    "settings_rows",
    "show_not_implemented_dialog",
    "show_settings_dialog",
    "show_status_dialog",
]
