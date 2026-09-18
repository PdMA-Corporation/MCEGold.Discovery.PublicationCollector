from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_PORTAL_LOCAL_CONFIG_PATH = "/config/portal.local.json"
PORTAL_LOCAL_CONFIG_PATH_ENV = "MCEGOLD_PORTAL_LOCAL_CONFIG_PATH"


class ConnectionSettingsUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class PortalConnectionSettings:
    host: str
    username: str
    password: str
    api_key: str

    def as_environment(self) -> dict[str, str]:
        return {
            "MCEGOLD_HOST": self.host,
            "MCEGOLD_USERNAME": self.username,
            "MCEGOLD_PASSWORD": self.password,
            "MCEGOLD_API_KEY": self.api_key,
        }


@dataclass(frozen=True)
class ConnectionSettingsReadResult:
    settings: PortalConnectionSettings | None
    error_message: str | None = None

    @property
    def is_complete(self) -> bool:
        return self.settings is not None and self.error_message is None


class PortalConnectionSettingsReader:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.getenv(PORTAL_LOCAL_CONFIG_PATH_ENV, DEFAULT_PORTAL_LOCAL_CONFIG_PATH))

    def read(self) -> ConnectionSettingsReadResult:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ConnectionSettingsReadResult(None, "Portal local config file is missing.")
        except (OSError, json.JSONDecodeError):
            return ConnectionSettingsReadResult(None, "Portal local config file could not be read.")

        if not isinstance(data, dict):
            return ConnectionSettingsReadResult(None, "Portal local config root must be a JSON object.")

        values = {
            "mcegoldDataServicesUrl": _string_value(data.get("mcegoldDataServicesUrl")),
            "mcegoldUsername": _string_value(data.get("mcegoldUsername")),
            "mcegoldPassword": _string_value(data.get("mcegoldPassword")),
            "mcegoldApiKey": _string_value(data.get("mcegoldApiKey")),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            return ConnectionSettingsReadResult(None, "MCEGold Data Services connection settings are incomplete.")
        if not _is_https_url(values["mcegoldDataServicesUrl"]):
            return ConnectionSettingsReadResult(None, "MCEGold Data Services URL must be an absolute HTTPS URL.")

        return ConnectionSettingsReadResult(
            PortalConnectionSettings(
                host=values["mcegoldDataServicesUrl"],
                username=values["mcegoldUsername"],
                password=values["mcegoldPassword"],
                api_key=values["mcegoldApiKey"],
            )
        )


def load_required_connection_environment(reader: PortalConnectionSettingsReader) -> dict[str, str]:
    result = reader.read()
    if result.settings is None:
        raise ConnectionSettingsUnavailable(result.error_message or "MCEGold Data Services connection settings are incomplete.")
    return result.settings.as_environment()


def merge_connection_environment(connection_environment: dict[str, str]) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(connection_environment)
    return environment


def _string_value(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _is_https_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)
