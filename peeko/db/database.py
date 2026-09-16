"""Database subsystem: SQLite persistence layer.

Stage 0 provides the connection management and a minimal schema
(``meta`` table with a schema version) so later stages (memory at
Stage 4, needs/state persistence at Stage 7+) can add tables without
reworking the plumbing. Peeko's database lives in the user data
directory (``<data_dir>/peeko.db``).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

LOG = logging.getLogger("peeko.db")

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    """Thin, safe wrapper around a SQLite connection.

    Usage::

        db = Database(path)
        db.connect()
        db.initialize()
        ...
        db.close()
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        """Open the connection (creates parent directories)."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        LOG.debug("Database connected: %s", self.db_path)

    def initialize(self) -> None:
        """Create the schema and record the schema version."""
        if self._conn is None:
            raise RuntimeError("Database.connect() must be called first")
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        self._conn.commit()

    def connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() must be called first")
        return self._conn

    def meta(self, key: str) -> str | None:
        """Read a value from the ``meta`` table, or ``None``."""
        row = self.connection().execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Database":
        self.connect()
        self.initialize()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()