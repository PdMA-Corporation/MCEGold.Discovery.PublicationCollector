from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

from .connection_settings import (
    PortalConnectionSettingsReader,
    load_optional_connection_environment,
    merge_connection_environment,
)
from .configuration import CollectorConfig


LOGGER = logging.getLogger(__name__)
SENSITIVE_PATTERNS = [
    re.compile(r"(password\s*[=:]\s*)[^\s,;]+", re.IGNORECASE),
    re.compile(r"(apiKey\s*[=:]\s*)[^\s,;]+", re.IGNORECASE),
    re.compile(r"(Authorization\s*:\s*)[^\r\n]+", re.IGNORECASE),
    re.compile(r"(Basic\s+)[A-Za-z0-9+/=._:-]+", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9+/=._:-]+", re.IGNORECASE),
]


@dataclass(frozen=True)
class CliFault:
    category: str | None
    code: str | None
    message: str | None
    status_code: int | None
    details: Any = None


@dataclass(frozen=True)
class Publication:
    session_id: str
    message_id: str
    payload: Any
    envelope: dict[str, Any]


class ConnectorCliError(RuntimeError):
    def __init__(
        self,
        message: str,
        command: list[str],
        exit_code: int | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
        fault: CliFault | None = None,
    ) -> None:
        super().__init__(message)
        self.command = command
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.fault = fault


class NoPublicationAvailable(Exception):
    pass


class InvalidSessionError(ConnectorCliError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]


class PublicationClient:
    def __init__(
        self,
        config: CollectorConfig,
        runner: Runner = subprocess.run,
        connection_settings_reader: PortalConnectionSettingsReader | None = None,
    ) -> None:
        self.config = config
        self.runner = runner
        self.connection_settings_reader = connection_settings_reader or PortalConnectionSettingsReader()
        self._session_connection_environment: dict[str, str] | None = None

    def open_subscription(self) -> str:
        connection_environment = load_optional_connection_environment(self.connection_settings_reader)
        envelope = self._run(self.build_open_subscription_command(), connection_environment)
        session_id = _get_path(envelope, ["data", "sessionId"])
        if not session_id:
            raise ConnectorCliError("CLI opened a subscription without returning data.sessionId.", [])
        self._session_connection_environment = connection_environment
        return str(session_id)

    def read_publication(self, session_id: str) -> Publication | None:
        try:
            envelope = self._run(self.build_read_publication_command(session_id))
        except NoPublicationAvailable:
            return None
        message_id = _get_path(envelope, ["data", "messageId"])
        if not message_id:
            raise ConnectorCliError("CLI read succeeded without returning data.messageId.", [])
        return Publication(
            session_id=str(_get_path(envelope, ["data", "sessionId"]) or session_id),
            message_id=str(message_id),
            payload=_get_path(envelope, ["data", "payload"]),
            envelope=envelope,
        )

    def remove_publication(self, session_id: str) -> None:
        self._run(self.build_remove_publication_command(session_id))

    def close_subscription(self, session_id: str) -> None:
        try:
            self._run(self.build_close_subscription_command(session_id))
        finally:
            self._session_connection_environment = None

    def build_open_subscription_command(self) -> list[str]:
        return self._publication_command("open-subscription")

    def build_read_publication_command(self, session_id: str) -> list[str]:
        return self._publication_command("read", session_id)

    def build_remove_publication_command(self, session_id: str) -> list[str]:
        return self._publication_command("remove", session_id)

    def build_close_subscription_command(self, session_id: str) -> list[str]:
        return self._publication_command("close-subscription", session_id)

    def documented_short_json_command(self, operation: str, session_id: str | None = None) -> dict[str, Any]:
        command_names = {
            "open-subscription": "OpenSubscription",
            "read": "ReadPublication",
            "remove": "RemovePublication",
            "close-subscription": "CloseSubscription",
        }
        if operation not in command_names:
            raise ValueError(f"Unsupported publication operation: {operation}")
        result = {
            "command": command_names[operation],
            "host": "<loaded by Connector CLI config>",
            "authenticationScheme": "<loaded by Connector CLI config>",
            "apiKey": "<loaded by Connector CLI config>",
            "userName": "<loaded by Connector CLI config>",
            "password": "<loaded by Connector CLI config>",
            "includeRawResponse": self.config.include_raw,
        }
        if session_id is not None:
            result["sessionId"] = session_id
        return result

    def _publication_command(self, verb: str, session_id: str | None = None) -> list[str]:
        command = self._base_command()
        command.extend(["publication", verb, "--config", self.config.connector_config_path])
        if session_id is not None:
            command.extend(["--session-id", session_id])
        command.extend(["--output", "json"])
        if self.config.include_raw:
            command.append("--include-raw")
        return command

    def _base_command(self) -> list[str]:
        return ["dotnet", self.config.cli_path] if self.config.cli_path.lower().endswith(".dll") else [self.config.cli_path]

    def _run(self, command: list[str], connection_environment: dict[str, str] | None = None) -> dict[str, Any]:
        LOGGER.debug("Running Connector CLI command: %s", redact_command(command))
        effective_connection_environment = connection_environment or self._session_connection_environment
        try:
            completed = self.runner(
                command,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.config.cli_timeout_seconds,
                check=False,
                env=merge_connection_environment(effective_connection_environment) if effective_connection_environment else None,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConnectorCliError(
                "Connector CLI timed out.",
                command,
                stdout=exc.stdout if isinstance(exc.stdout, str) else None,
                stderr=exc.stderr if isinstance(exc.stderr, str) else None,
            ) from exc
        except OSError as exc:
            raise ConnectorCliError(f"Connector CLI could not be started: {exc}", command) from exc

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ConnectorCliError(
                "Connector CLI did not return valid JSON.",
                command,
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
            ) from exc

        if not isinstance(envelope, dict):
            raise ConnectorCliError("Connector CLI JSON root was not an object.", command, completed.returncode, stdout, stderr)

        if completed.returncode != 0 or envelope.get("success") is not True:
            fault = _fault(envelope.get("fault"))
            if _is_no_publication(envelope, fault):
                raise NoPublicationAvailable()
            error_type = InvalidSessionError if _is_invalid_session(fault) else ConnectorCliError
            raise error_type(
                "Connector CLI command failed.",
                command,
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                fault=fault,
            )

        return envelope


def redact_sensitive(value: Any) -> str:
    text = "" if value is None else str(value)
    for pattern in SENSITIVE_PATTERNS:
        text = pattern.sub(r"\1***REDACTED***", text)
    return text


def redact_command(command: list[str]) -> list[str]:
    return [redact_sensitive(part) for part in command]


def safe_session_id(session_id: str | None) -> str:
    if not session_id:
        return "<none>"
    return session_id if len(session_id) <= 8 else f"{session_id[:4]}...{session_id[-4:]}"


def _fault(value: Any) -> CliFault | None:
    if not isinstance(value, dict):
        return None
    return CliFault(
        category=value.get("category"),
        code=value.get("code"),
        message=value.get("message"),
        status_code=value.get("statusCode"),
        details=value.get("details"),
    )


def _is_no_publication(envelope: dict[str, Any], fault: CliFault | None) -> bool:
    if envelope.get("command") != "publication.read" or fault is None:
        return False
    if _code_is(fault, "NoPublicationAvailable"):
        return True
    text = f"{fault.code or ''} {fault.message or ''}".casefold()
    return fault.status_code == 404 and not any(token in text for token in ("session", "expired", "invalid"))


def _is_invalid_session(fault: CliFault | None) -> bool:
    if fault is None:
        return False
    if _code_is(fault, "SessionNotFound", "SessionInvalid", "SessionClosed", "SessionExpired"):
        return True
    text = f"{fault.code or ''} {fault.message or ''}".casefold()
    return fault.status_code in {401, 403, 410} or any(token in text for token in ("session", "expired", "invalid"))


def _code_is(fault: CliFault, *codes: str) -> bool:
    return any((fault.code or "").casefold() == code.casefold() for code in codes)


def _get_path(value: Any, path: list[str]) -> Any:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
