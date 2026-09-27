"""Honest dialogs for Peeko: status readout, settings viewer, "not yet".

Stage 2 introduces the first real UI windows beyond the context menu:

* **Check Status** — a readout assembled from live state (version, stage,
  the avatar's current animation, how many animations the artwork manifest
  contains) plus a plain-language list of what works and what does not.
* **Settings** — a read-only viewer of the configuration Peeko is actually
  running with (paths, log level, artwork folder). Editing from the UI is
  not implemented yet; the dialog says so and points at ``.env`` instead.
* **Not implemented yet** — the explanation shown when the user picks one
  of the planned pet actions from the menu. It names the roadmap stage that
  will implement it. No fake windows, no dead buttons.

Every dialog is opened **non-blocking** (:meth:`QMessageBox.show` /
:meth:`QDialog.show` — never a nested ``exec()`` event loop), so the
animation loop keeps running while a dialog is on screen. The text
builders are plain functions, so the honesty of each readout is
unit-testable without a display.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from peeko import __app_name__, __stage__, __total_stages__, __version__
from peeko.ai.client import AIClient
from peeko.ai.providers import DEFAULT_BASE_URL
from peeko.ui.context_menu import MenuEntry, future_entries

#: Section rules are plain text so the readout renders identically everywhere.
_RULE = "-" * 34


# --------------------------------------------------------------------------- #
# Check Status
# --------------------------------------------------------------------------- #
def _avatar_lines(machine, manifest) -> list[str]:
    """Live avatar facts — degrades honestly when something is missing."""
    lines: list[str] = []
    if machine is None:
        lines.append("State now: (unavailable — no avatar state machine)")
    else:
        animation_name = machine.current_animation
        animation = machine.manifest.animations.get(animation_name)
        frames = len(animation.frames) if animation is not None else 0
        lines.append(
            f"State now: {machine.state}"
            f'  (animation "{animation_name}", '
            f"frame {machine.frame_index + 1} of {frames})"
        )
        reactions = sorted(machine.available_reactions)
        lines.append(
            "Reactions available: "
            + (", ".join(reactions) if reactions else "none")
        )

    if manifest is None:
        lines.append("Artwork manifest: (unavailable)")
    else:
        lines.append(f"Animations in the manifest: {len(manifest.animations)}")
        lines.append(
            f"Canvas: {manifest.canvas_width} x {manifest.canvas_height} px, "
            f"{len(manifest.z_order)} layer(s): {', '.join(manifest.z_order)}"
        )
    return lines


def build_status_text(settings, machine=None, manifest=None) -> str:
    """The full Check Status readout, built from live, existing state.

    :param settings: the running :class:`peeko.settings.Settings`.
    :param machine: optional avatar state machine (duck-typed).
    :param manifest: optional parsed artwork manifest (duck-typed).
    """
    artworks = (
        str(settings.avatar_assets_dir)
        if getattr(settings, "avatar_assets_dir", None)
        else "packaged artwork inside the Peeko package"
    )
    # The AI line reports the real configuration (never the key itself), so
    # "why does the chat not answer?" is answerable from inside the app.
    client = AIClient.from_settings(settings)
    works_today = [
        "idle bob, blinking, glances around, glances toward the cursor",
        "hover reaction, click squash-and-bounce, double-click bounce",
        "drag wiggle while you carry it",
        "right-click menu: Talk, Check Status, Settings, Quit (Ctrl+Q)",
        "chat window (Talk): typed conversation with the AI, and the reply "
        "drives the robot's expression",
    ]
    future = [
        f"{entry.label} (Stage {entry.stage})"
        for entry in future_entries()
    ]
    future_lines = []
    for i in range(0, len(future), 3):
        future_lines.append("  " + ", ".join(future[i:i + 3]))

    lines = [
        f"{__app_name__} v{__version__} — Stage {__stage__} of {__total_stages__}",
        "",
        "AVATAR",
        _RULE,
        *_avatar_lines(machine, manifest),
        f"Artwork loaded from: {artworks}",
        "",
        "AI CHAT (Stage 3)",
        _RULE,
        f"  Configured: {'yes' if client.is_configured() else 'no'}"
        f"  ({client.configuration_problem() or 'ready to chat'})",
        f"  {client.describe()}",
        "",
        "WORKS TODAY",
        _RULE,
        *[f"  - {item}" for item in works_today],
        "",
        "NOT IMPLEMENTED YET",
        _RULE,
        *future_lines,
        "",
        "Voice input, TTS, memory and app awareness are not implemented yet",
        "either — their settings exist but do nothing.",
    ]
    return "\n".join(lines)


def build_status_dialog(parent, settings, machine=None, manifest=None) -> QMessageBox:
    """Build (but do not show) the Check Status message box."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Information)
    box.setWindowTitle(f"{__app_name__} — Status")
    box.setTextFormat(Qt.PlainText)
    box.setText(f"{__app_name__} v{__version__} — Stage {__stage__} of "
                f"{__total_stages__}")
    box.setInformativeText(build_status_text(settings, machine, manifest))
    box.setStandardButtons(QMessageBox.Close)
    return box


def show_status_dialog(parent, settings, machine=None, manifest=None) -> QMessageBox:
    """Show the status readout without blocking the UI thread."""
    box = build_status_dialog(parent, settings, machine, manifest)
    _open_async(box)
    return box


# --------------------------------------------------------------------------- #
# Settings (read-only viewer for now)
# --------------------------------------------------------------------------- #
#: Rows shown by the settings viewer: (label, attribute name, kind).
_SETTINGS_ROWS = (
    ("Application", "app_name", "text"),
    ("Version", "version", "text"),
    ("Development stage", None, "stage"),
    ("Log level", "log_level", "text"),
    ("Data directory", "data_dir", "path"),
    ("Config directory", "config_dir", "path"),
    ("Log directory", "log_dir", "path"),
    ("Database file", "db_path", "path"),
    ("Artwork folder", "avatar_assets_dir", "artwork"),
    # Stage 3: what the chat window will actually use. The key itself is
    # never rendered — only whether one is set.
    ("AI provider", "ai_provider", "text"),
    ("AI model", "ai_model", "text"),
    ("AI base URL", "ai_base_url", "base_url"),
    ("AI API key", None, "api_key"),
)

#: What the settings viewer says about editing (honest about the stage).
SETTINGS_NOTE = (
    "Read-only for now: these are the values Peeko is actually running with. "
    "Changing settings from the UI is not implemented yet — set them with "
    "environment variables or a .env file (see the README). The AI settings "
    "are used by the chat window (Talk); the voice and TTS settings still "
    "have no effect until Stages 4-5."
)


def settings_rows(settings) -> list[tuple[str, str]]:
    """The (label, value) pairs the settings viewer displays.

    Paths that are not set show what Peeko falls back to, never a blank or
    a made-up value. The API key is reported as set/unset only — its value
    is never put into a widget, a log line or a report.
    """
    rows: list[tuple[str, str]] = []
    for label, attribute, kind in _SETTINGS_ROWS:
        if kind == "stage":
            value = f"Stage {__stage__} of {__total_stages__}"
        elif kind == "path":
            value = str(getattr(settings, attribute, "")) or "(not set)"
        elif kind == "artwork":
            configured = getattr(settings, "avatar_assets_dir", None)
            value = (
                str(configured) if configured
                else "packaged artwork inside the Peeko package"
            )
        elif kind == "base_url":
            value = str(getattr(settings, attribute, "")) or DEFAULT_BASE_URL
        elif kind == "api_key":
            key = str(getattr(settings, "ai_api_key", "") or "")
            value = "<set — never shown>" if key else "(not set)"
        else:
            value = str(getattr(settings, attribute, "")) or "(not set)"
        rows.append((label, value))
    return rows


class SettingsDialog(QDialog):
    """Read-only view of the configuration Peeko is running with."""

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{__app_name__} — Settings (read-only)")
        # A real top-level window (with its own title bar), owned by the
        # avatar so it closes with the app.
        self.setWindowFlag(Qt.Window, True)
        self.setModal(False)
        self._settings = settings

        layout = QVBoxLayout(self)
        heading = QLabel(f"{__app_name__} v{__version__} settings")
        heading.setStyleSheet("font-weight: bold;")
        layout.addWidget(heading)

        form = QFormLayout()
        for label, value in settings_rows(settings):
            value_label = QLabel(value)
            value_label.setTextInteractionFlags(
                Qt.TextSelectableByMouse
            )
            value_label.setWordWrap(True)
            form.addRow(QLabel(f"{label}:"), value_label)
        layout.addLayout(form)

        note = QLabel(SETTINGS_NOTE)
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        layout.addWidget(buttons)
        self.setMinimumWidth(420)

    def settings_text(self) -> str:
        """The dialog's content as plain text (used by the tests)."""
        return "\n".join(
            f"{label} {value}" for label, value in settings_rows(self._settings)
        )


def build_settings_dialog(parent, settings) -> SettingsDialog:
    """Build (but do not show) the read-only settings window."""
    return SettingsDialog(settings, parent)


def show_settings_dialog(parent, settings) -> SettingsDialog:
    """Show the settings window without blocking the UI thread."""
    dialog = build_settings_dialog(parent, settings)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


# --------------------------------------------------------------------------- #
# "Not implemented yet" (planned menu entries)
# --------------------------------------------------------------------------- #
def build_not_implemented_text(entry: MenuEntry) -> str:
    """The message shown for a planned-but-not-built menu entry."""
    lines = [
        entry.description,
        "",
        f"{__app_name__} is at Stage {__stage__} of {__total_stages__}, so this "
        "action does nothing yet — it is deliberately not faked.",
        "",
        "Working right now: Talk (chat), Check Status, Settings, Quit — and "
        "clicking, double-clicking, hovering and dragging the robot itself.",
    ]
    return "\n".join(lines)


def build_not_implemented_dialog(parent, entry: MenuEntry) -> QMessageBox:
    """Build (but do not show) the honest explanation for ``entry``."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Information)
    box.setWindowTitle(f"{__app_name__} — {entry.label}")
    box.setTextFormat(Qt.PlainText)
    box.setText(f"{entry.label} is not implemented yet")
    box.setInformativeText(build_not_implemented_text(entry))
    box.setStandardButtons(QMessageBox.Ok)
    return box


def show_not_implemented_dialog(parent, entry: MenuEntry) -> QMessageBox:
    """Show the explanation without blocking the UI thread."""
    box = build_not_implemented_dialog(parent, entry)
    _open_async(box)
    return box


# --------------------------------------------------------------------------- #
# Internal
# --------------------------------------------------------------------------- #
def _open_async(box: QMessageBox) -> None:
    """Show a message box as a non-modal window.

    Deliberately **not** ``exec()``: a nested event loop would freeze the
    animation while the box is on screen. The notice appears next to the
    robot, the robot keeps blinking behind it, and the user dismisses it
    whenever they like.
    """
    box.setModal(False)
    box.setAttribute(Qt.WA_DeleteOnClose, True)
    box.show()
