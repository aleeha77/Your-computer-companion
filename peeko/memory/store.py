"""Memory subsystem: persistent memory for Peeko.

Stage 4: NOT IMPLEMENTED YET.

Planned behaviour: store conversation history and durable facts in the
SQLite database (see :mod:`peeko.db`), optionally summarized over time.
The :class:`MemoryStore` interface below is the Stage 4 contract; methods
raise ``NotImplementedError`` until implemented.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class MemoryStore:
    """Planned persistent memory store backed by the SQLite database."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path

    def remember(self, kind: str, key: str, value: Any) -> None:
        """Persist a memory of ``kind`` under ``key``.

        .. note:: Stage 4 — not implemented yet.
        """
        raise NotImplementedError(
            "MemoryStore.remember is not implemented until Stage 4 "
            "(persistent memory)."
        )

    def recall(self, kind: str, key: str) -> Any | None:
        """Return a previously stored memory, or ``None``.

        .. note:: Stage 4 — not implemented yet.
        """
        raise NotImplementedError(
            "MemoryStore.recall is not implemented until Stage 4 "
            "(persistent memory)."
        )