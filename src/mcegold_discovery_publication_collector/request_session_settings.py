from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


class RequestSessionLookupError(RuntimeError):
    pass


class PortalRequestSessionReader:
    def __init__(self, database_path: str, busy_timeout_ms: int = 5000) -> None:
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must not be negative.")
        self.database_path = database_path
        self.busy_timeout_ms = busy_timeout_ms

    def read(self) -> str | None:
        if not self.database_path:
            raise RequestSessionLookupError("discoveryDatabasePath is required for shared request sessions.")
        try:
            uri = Path(self.database_path).resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=self.busy_timeout_ms / 1000)) as connection:
                row = connection.execute(
                    "SELECT SettingValue FROM ApplicationSetting WHERE SettingKey = ?",
                    ("Discovery.RequestSessionId",),
                ).fetchone()
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise RequestSessionLookupError("Could not read shared request session from Portal database.") from exc
        if row is None or row[0] is None:
            return None
        if not isinstance(row[0], str):
            raise RequestSessionLookupError("Shared request session ID must be text.")
        return row[0].strip() or None
