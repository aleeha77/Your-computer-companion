"""Awareness subsystem: know what the user is doing.

Stage 9: NOT IMPLEMENTED YET.

Planned behaviour: query the OS for the currently active window / app so
Peeko can react contextually ("working in VS Code..."). This is
inherently platform-specific (Windows: Win32 APIs, macOS: AppKit,
Linux: X11/Wayland protocols), so it is deferred and will ship with a
platform-abstracted interface. Nothing here triggers side effects at
Stage 0.
"""

from __future__ import annotations


class ActiveWindowTracker:
    """Planned cross-platform tracker for the active application window."""

    def get_active_window_title(self) -> str:
        """Return the title of the currently focused window.

        .. note:: Stage 9 — not implemented yet.
        """
        raise NotImplementedError(
            "ActiveWindowTracker is not implemented until Stage 9 "
            "(app awareness)."
        )