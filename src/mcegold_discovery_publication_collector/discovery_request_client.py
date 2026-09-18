from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .connection_settings import (
    PortalConnectionSettingsReader,
    load_required_connection_environment,
    merge_connection_environment,
)
from .configuration import CollectorConfig
from .publication_client import ConnectorCliError, Runner, _fault, _get_path, redact_command


LOGGER = logging.getLogger(__name__)


class DiscoveryRequestError(RuntimeError):
    pass


class DiscoveryRequestClient:
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

    def open_session(self) -> str:
        connection_environment = load_required_connection_environment(self.connection_settings_reader)
        envelope = self._run(self._request_command("open-session"), connection_environment)
        session_id = _get_path(envelope, ["data", "sessionId"])
        if not session_id:
            raise DiscoveryRequestError("Connector CLI opened a request session without returning data.sessionId.")
        self._session_connection_environment = connection_environment
        return str(session_id)

    def post_request(self, session_id: str, request: dict[str, Any]) -> str:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as file:
            json.dump(request, file)
            input_path = file.name
        try:
            command = self._request_command("post", session_id)
            command.extend(["--input", input_path, "--payload-profile", "Full"])
            envelope = self._run(command)
        finally:
            Path(input_path).unlink(missing_ok=True)
        request_message_id = _get_path(envelope, ["data", "requestMessageId"])
        if not request_message_id:
            raise DiscoveryRequestError("Connector CLI posted a request without returning data.requestMessageId.")
        return str(request_message_id)

    def read_response(self, session_id: str, request_message_id: str) -> Any:
        envelope = self._run(self._request_message_command("read-response", session_id, request_message_id))
        return _get_path(envelope, ["data", "payload"])

    def remove_response(self, session_id: str, request_message_id: str) -> None:
        self._run(self._request_message_command("remove-response", session_id, request_message_id))

    def close_session(self, session_id: str) -> None:
        try:
            self._run(self._request_command("close-session", session_id))
        finally:
            self._session_connection_environment = None

    def execute_request(self, session_id: str, request: dict[str, Any]) -> Any:
        request_message_id = self.post_request(session_id, request)
        try:
            return self.read_response(session_id, request_message_id)
        finally:
            self.remove_response(session_id, request_message_id)

    def _request_command(self, verb: str, session_id: str | None = None) -> list[str]:
        command = self._base_command()
        command.extend(["request", verb, "--config", self.config.connector_config_path])
        if session_id is not None:
            command.extend(["--session-id", session_id])
        command.extend(["--output", "json"])
        if self.config.include_raw:
            command.append("--include-raw")
        return command

    def _request_message_command(self, verb: str, session_id: str, request_message_id: str) -> list[str]:
        command = self._request_command(verb, session_id)
        command.extend(["--request-id", request_message_id])
        return command

    def _base_command(self) -> list[str]:
        return ["dotnet", self.config.cli_path] if self.config.cli_path.lower().endswith(".dll") else [self.config.cli_path]

    def _run(self, command: list[str], connection_environment: dict[str, str] | None = None) -> dict[str, Any]:
        LOGGER.debug("Running Connector CLI request command: %s", redact_command(command))
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
            raise DiscoveryRequestError("Connector CLI request command timed out.") from exc
        except OSError as exc:
            raise DiscoveryRequestError(f"Connector CLI request command could not be started: {exc}") from exc

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise DiscoveryRequestError("Connector CLI request command did not return valid JSON.") from exc
        if not isinstance(envelope, dict):
            raise DiscoveryRequestError("Connector CLI request JSON root was not an object.")
        if completed.returncode != 0 or envelope.get("success") is not True:
            fault = _fault(envelope.get("fault"))
            raise ConnectorCliError(
                "Connector CLI request command failed.",
                command,
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                fault=fault,
            )
        return envelope
