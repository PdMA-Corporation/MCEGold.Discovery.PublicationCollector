from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from .connection_settings import ConnectionSettingsUnavailable
from .configuration import CollectorConfig
from .control_settings import (
    CollectorControlSettings,
    CollectorControlSettingsReader,
    DataRetentionSettings,
    DataRetentionSettingsReader,
)
from .publication_client import (
    ConnectorCliError,
    InvalidSessionError,
    Publication,
    PublicationClient,
    safe_session_id,
)
from .retention import MeasurementRetentionService
from .runtime_status import CollectorRuntimeStatusStore, RuntimeStatusError


LOGGER = logging.getLogger(__name__)
RETENTION_INTERVAL = timedelta(hours=24)


class PublicationHandler(Protocol):
    def __call__(self, publication: Publication) -> None:
        ...


def log_publication_handler(publication: Publication) -> None:
    LOGGER.info("Publication received messageId=%s sessionId=%s", publication.message_id, safe_session_id(publication.session_id))
    LOGGER.info("Publication payload: %s", json.dumps(publication.payload, default=str, ensure_ascii=False))


class PublicationCollectorWorker:
    def __init__(
        self,
        config: CollectorConfig,
        client: PublicationClient | None = None,
        handler: PublicationHandler = log_publication_handler,
        stop_event: threading.Event | None = None,
        runtime_status: CollectorRuntimeStatusStore | None = None,
        control_settings_reader: CollectorControlSettingsReader | None = None,
        retention_settings_reader: DataRetentionSettingsReader | None = None,
        retention_service: MeasurementRetentionService | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.client = client or PublicationClient(config)
        self.handler = handler
        self.stop_event = stop_event or threading.Event()
        self.runtime_status = runtime_status
        self._runtime_status_available = runtime_status is not None
        self._runtime_status_warning_logged = False
        self.control_settings_reader = control_settings_reader
        self.retention_settings_reader = retention_settings_reader
        self.retention_service = retention_service
        self.clock = clock or _utc_now
        self.last_retention_attempt_utc: datetime | None = None
        self.last_retention_settings: DataRetentionSettings | None = None
        self.session_id: str | None = None

    def run(self) -> None:
        LOGGER.info("MCEGold publication collector startup.")
        self._heartbeat()
        self._run_startup_retention()
        try:
            while not self.stop_event.is_set():
                self._heartbeat()
                self._maybe_run_retention()
                control_settings = self._read_control_settings()
                if control_settings.enabled:
                    poll_succeeded = self.poll_once()
                    if poll_succeeded:
                        self._mark_running()
                else:
                    self._enter_idle()
                if not self.stop_event.is_set():
                    LOGGER.debug(
                        "Waiting %s seconds before next control check.",
                        _format_interval(control_settings.polling_interval_seconds),
                    )
                    self.stop_event.wait(control_settings.polling_interval_seconds)
        finally:
            self.shutdown()

    def poll_once(self) -> bool:
        return self.drain_publications()

    def drain_publications(self) -> bool:
        if not self.session_id:
            self._heartbeat()
            self._open_subscription()

        if not self.session_id:
            return False

        LOGGER.debug("Starting publication drain cycle.")
        while not self.stop_event.is_set():
            try:
                LOGGER.debug("Polling publication session...")
                self._mark_poll()
                publication = self.client.read_publication(self.session_id)
                if publication is None:
                    LOGGER.debug("No publication available; drain cycle complete.")
                    self._mark_running()
                    return True

                try:
                    self._heartbeat()
                    self.handler(publication)
                except Exception:
                    LOGGER.exception(
                        "Publication handling failed; publication will not be removed messageId=%s sessionId=%s",
                        publication.message_id,
                        safe_session_id(self.session_id),
                    )
                    self._mark_error("Publication handling failed.")
                    return False

                self._heartbeat()
                self.client.remove_publication(self.session_id)
                self._mark_publication()
                LOGGER.info(
                    "Publication removed messageId=%s sessionId=%s",
                    publication.message_id,
                    safe_session_id(self.session_id),
                )
                LOGGER.debug("Publication processed successfully; reading next available publication.")
                if not self._read_control_settings().enabled:
                    self._enter_idle()
                    return False
            except InvalidSessionError as exc:
                LOGGER.warning("Subscription session is invalid or expired; reopening. fault=%s", _safe_fault(exc))
                self._mark_error("Subscription session is invalid or expired.")
                self.session_id = None
                return False
            except ConnectorCliError as exc:
                LOGGER.warning("Connector CLI failure; will retry. fault=%s", _safe_fault(exc))
                LOGGER.debug("Read failed; ending current drain cycle.")
                self._mark_error("Connector CLI failed while polling publications.")
                return False
        return True

    def request_stop(self) -> None:
        self.stop_event.set()

    def shutdown(self) -> None:
        LOGGER.info("Publication collector shutdown requested.")
        self._heartbeat()
        if not self.session_id:
            return
        session_id = self.session_id
        self.session_id = None
        try:
            self.client.close_subscription(session_id)
            LOGGER.info("Subscription closed sessionId=%s", safe_session_id(session_id))
        except ConnectorCliError as exc:
            LOGGER.warning("Failed to close subscription sessionId=%s fault=%s", safe_session_id(session_id), _safe_fault(exc))

    def _open_subscription(self) -> None:
        try:
            self._heartbeat()
            self.session_id = self.client.open_subscription()
            self._mark_running()
            LOGGER.info("Subscription opened sessionId=%s", safe_session_id(self.session_id))
        except ConnectorCliError as exc:
            LOGGER.warning("Could not open subscription; will retry. fault=%s", _safe_fault(exc))
            self._mark_error("Failed to open subscription session.")
            self.session_id = None
        except ConnectionSettingsUnavailable as exc:
            LOGGER.info("Waiting for MCEGold Data Services connection settings. reason=%s", exc)
            self._mark_idle()
            self.session_id = None

    def _read_control_settings(self) -> CollectorControlSettings:
        if self.control_settings_reader is None:
            return CollectorControlSettings(
                enabled=True,
                polling_interval_seconds=self.config.polling_interval_seconds,
            )
        result = self.control_settings_reader.read()
        if result.error_message:
            LOGGER.warning("Collector control settings read failed; using last valid values. error=%s", result.error_message)
            self._mark_error("Failed to read collector control settings.")
        return result.settings

    def _run_startup_retention(self) -> None:
        if self.retention_settings_reader is None or self.retention_service is None:
            return
        settings = self._read_retention_settings()
        self.last_retention_settings = settings
        if not settings.enabled:
            LOGGER.info("Automatic data retention is disabled.")
            return
        LOGGER.info("Automatic data retention enabled; running startup cleanup.")
        self._run_retention_cleanup(settings)

    def _maybe_run_retention(self) -> None:
        if self.retention_settings_reader is None or self.retention_service is None:
            return
        settings = self._read_retention_settings()
        settings_changed = self.last_retention_settings is not None and settings != self.last_retention_settings
        if settings_changed:
            LOGGER.info("Data retention settings changed; scheduling cleanup.")
        self.last_retention_settings = settings
        if not settings.enabled:
            return
        now_utc = self._retention_now()
        if settings_changed or self.last_retention_attempt_utc is None:
            self._run_retention_cleanup(settings, now_utc=now_utc)
            return
        if now_utc - self.last_retention_attempt_utc >= RETENTION_INTERVAL:
            LOGGER.info("Running scheduled data retention cleanup.")
            self._run_retention_cleanup(settings, now_utc=now_utc)

    def _read_retention_settings(self) -> DataRetentionSettings:
        if self.retention_settings_reader is None:
            return DataRetentionSettings(enabled=False, days=90)
        result = self.retention_settings_reader.read()
        if result.error_message:
            LOGGER.warning("Data retention settings read failed; using last valid values. error=%s", result.error_message)
        return result.settings

    def _run_retention_cleanup(
        self,
        settings: DataRetentionSettings,
        now_utc: datetime | None = None,
    ) -> None:
        if self.retention_service is None:
            return
        effective_now = now_utc or self._retention_now()
        self.last_retention_attempt_utc = effective_now
        self._heartbeat()
        try:
            self.retention_service.run_cleanup(settings, now_utc=effective_now)
        except Exception:
            LOGGER.exception("Data retention cleanup failed unexpectedly.")
        finally:
            self._heartbeat()

    def _retention_now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("Retention clock must return a timezone-aware datetime.")
        return value.astimezone(timezone.utc)

    def _enter_idle(self) -> None:
        if self._ensure_subscription_closed_for_idle():
            self._mark_idle()

    def _ensure_subscription_closed_for_idle(self) -> bool:
        if not self.session_id:
            return True
        session_id = self.session_id
        self.session_id = None
        try:
            self._heartbeat()
            self.client.close_subscription(session_id)
            LOGGER.info("Subscription closed for disabled collector sessionId=%s", safe_session_id(session_id))
            return True
        except ConnectorCliError as exc:
            LOGGER.warning(
                "Failed to close subscription for disabled collector sessionId=%s fault=%s",
                safe_session_id(session_id),
                _safe_fault(exc),
            )
            self._mark_error("Failed to close subscription while entering Idle.")
            return False

    def _heartbeat(self) -> None:
        if not self._runtime_status_available or self.runtime_status is None:
            return
        try:
            self.runtime_status.heartbeat()
            self._runtime_status_warning_logged = False
        except RuntimeStatusError as exc:
            self._record_runtime_status_failure(exc)

    def _mark_running(self) -> None:
        if not self._runtime_status_available or self.runtime_status is None:
            return
        try:
            self.runtime_status.mark_running()
            self._runtime_status_warning_logged = False
        except RuntimeStatusError as exc:
            self._record_runtime_status_failure(exc)

    def _mark_idle(self) -> None:
        if not self._runtime_status_available or self.runtime_status is None:
            return
        try:
            self.runtime_status.mark_idle()
            self._runtime_status_warning_logged = False
        except RuntimeStatusError as exc:
            self._record_runtime_status_failure(exc)

    def _mark_poll(self) -> None:
        if not self._runtime_status_available or self.runtime_status is None:
            return
        try:
            self.runtime_status.mark_poll()
            self._runtime_status_warning_logged = False
        except RuntimeStatusError as exc:
            self._record_runtime_status_failure(exc)

    def _mark_publication(self) -> None:
        if not self._runtime_status_available or self.runtime_status is None:
            return
        try:
            self.runtime_status.mark_publication()
            self._runtime_status_warning_logged = False
        except RuntimeStatusError as exc:
            self._record_runtime_status_failure(exc)

    def _mark_error(self, message: str) -> None:
        if not self._runtime_status_available or self.runtime_status is None:
            return
        try:
            self.runtime_status.mark_error(message)
            self._runtime_status_warning_logged = False
        except RuntimeStatusError as exc:
            self._record_runtime_status_failure(exc)

    def _record_runtime_status_failure(self, exc: RuntimeStatusError) -> None:
        if self._runtime_status_warning_logged:
            LOGGER.debug("Runtime status telemetry unavailable; will retry. error=%s", exc)
            return
        LOGGER.warning("Runtime status telemetry unavailable; will retry. error=%s", exc)
        self._runtime_status_warning_logged = True


def _safe_fault(exc: ConnectorCliError) -> dict[str, object | None] | None:
    if exc.fault is None:
        return None
    return {
        "category": exc.fault.category,
        "code": exc.fault.code,
        "message": exc.fault.message,
        "statusCode": exc.fault.status_code,
    }


def _format_interval(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
