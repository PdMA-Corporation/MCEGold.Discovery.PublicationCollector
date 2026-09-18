from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


PUBLICATION_COLLECTOR_ID = "publication-collector"
SUPPORTED_RUNTIME_STATES = {"Running", "Idle", "Error"}
LAST_ERROR_MAX_LENGTH = 2048
SENSITIVE_PATTERNS = [
    re.compile(r"(password\s*[=:]\s*)[^\s,;]+", re.IGNORECASE),
    re.compile(r"(apiKey\s*[=:]\s*)[^\s,;]+", re.IGNORECASE),
    re.compile(r"(Authorization\s*:\s*)[^\r\n]+", re.IGNORECASE),
    re.compile(r"(Basic\s+)[A-Za-z0-9+/=._:-]+", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9+/=._:-]+", re.IGNORECASE),
]


class RuntimeStatusError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeStatusSnapshot:
    runtime_state: str
    last_heartbeat_utc: str
    last_poll_utc: str | None = None
    last_publication_utc: str | None = None
    last_error_utc: str | None = None
    last_error: str | None = None


class CollectorRuntimeStatusStore:
    def __init__(
        self,
        database_path: str,
        collector_id: str = PUBLICATION_COLLECTOR_ID,
        instance_id: str | None = None,
        started_utc: str | None = None,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self.database_path = database_path
        self.collector_id = collector_id
        self.instance_id = instance_id or str(uuid4())
        self.started_utc = started_utc or utc_now()
        self.busy_timeout_ms = busy_timeout_ms
        self.snapshot = RuntimeStatusSnapshot(
            runtime_state="Running",
            last_heartbeat_utc=self.started_utc,
        )

    def heartbeat(self, runtime_state: str | None = None) -> None:
        self.upsert(replace(self.snapshot, runtime_state=runtime_state or self.snapshot.runtime_state, last_heartbeat_utc=utc_now()))

    def mark_running(self) -> None:
        self.heartbeat("Running")

    def mark_idle(self) -> None:
        self.heartbeat("Idle")

    def mark_poll(self) -> None:
        now = utc_now()
        self.upsert(replace(self.snapshot, runtime_state="Running", last_heartbeat_utc=now, last_poll_utc=now))

    def mark_publication(self) -> None:
        now = utc_now()
        self.upsert(replace(self.snapshot, runtime_state="Running", last_heartbeat_utc=now, last_publication_utc=now))

    def mark_error(self, message: str) -> None:
        now = utc_now()
        self.upsert(
            replace(
                self.snapshot,
                runtime_state="Error",
                last_heartbeat_utc=now,
                last_error_utc=now,
                last_error=safe_last_error(message),
            )
        )

    def upsert(self, snapshot: RuntimeStatusSnapshot) -> None:
        if snapshot.runtime_state not in SUPPORTED_RUNTIME_STATES:
            raise RuntimeStatusError(f"Unsupported runtime state: {snapshot.runtime_state}")
        if not self.database_path:
            raise RuntimeStatusError("discoveryDatabasePath is required for runtime status.")

        path = Path(self.database_path)
        try:
            with sqlite3.connect(path) as connection:
                connection.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
                connection.execute(
                    """
                    INSERT INTO CollectorRuntimeStatus (
                        CollectorId,
                        InstanceId,
                        StartedUtc,
                        LastHeartbeatUtc,
                        RuntimeState,
                        LastPollUtc,
                        LastPublicationUtc,
                        LastErrorUtc,
                        LastError
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(CollectorId) DO UPDATE SET
                        InstanceId = excluded.InstanceId,
                        StartedUtc = excluded.StartedUtc,
                        LastHeartbeatUtc = excluded.LastHeartbeatUtc,
                        RuntimeState = excluded.RuntimeState,
                        LastPollUtc = excluded.LastPollUtc,
                        LastPublicationUtc = excluded.LastPublicationUtc,
                        LastErrorUtc = excluded.LastErrorUtc,
                        LastError = excluded.LastError
                    """,
                    (
                        self.collector_id,
                        self.instance_id,
                        self.started_utc,
                        snapshot.last_heartbeat_utc,
                        snapshot.runtime_state,
                        snapshot.last_poll_utc,
                        snapshot.last_publication_utc,
                        snapshot.last_error_utc,
                        snapshot.last_error,
                    ),
                )
                connection.commit()
        except sqlite3.Error as exc:
            raise RuntimeStatusError(
                f"Could not write CollectorRuntimeStatus. Confirm the Portal database is initialized to schema v17 or later: {path}"
            ) from exc

        self.snapshot = snapshot


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_last_error(message: str) -> str:
    text = str(message or "Publication Collector runtime error.").replace("\r", " ").replace("\n", " ")
    for pattern in SENSITIVE_PATTERNS:
        text = pattern.sub(r"\1***REDACTED***", text)
    if len(text) > LAST_ERROR_MAX_LENGTH:
        return text[: LAST_ERROR_MAX_LENGTH - 3] + "..."
    return text
