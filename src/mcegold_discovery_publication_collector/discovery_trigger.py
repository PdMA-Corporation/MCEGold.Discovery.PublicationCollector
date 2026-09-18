from __future__ import annotations

import logging
from typing import Protocol

from .bottom_up_discovery import BottomUpDiscoveryError, BottomUpDiscoveryService
from .measurement_publication import MeasurementLocationIdentity
from .publication_client import Publication


LOGGER = logging.getLogger(__name__)


class DiscoveryTrigger(Protocol):
    def on_unknown_measurement_location(
        self,
        identity: MeasurementLocationIdentity,
        publication: Publication,
        segment_uuid_if_known: str | None = None,
    ) -> bool:
        ...


class LoggingDiscoveryTrigger:
    def on_unknown_measurement_location(
        self,
        identity: MeasurementLocationIdentity,
        publication: Publication,
        segment_uuid_if_known: str | None = None,
    ) -> bool:
        LOGGER.info(
            "Unknown measurement location detected; discovery trigger not implemented measurementLocation=%s messageId=%s",
            identity.log_label,
            publication.message_id,
        )
        return False


class BottomUpDiscoveryTrigger:
    def __init__(self, service: BottomUpDiscoveryService) -> None:
        self.service = service

    def on_unknown_measurement_location(
        self,
        identity: MeasurementLocationIdentity,
        publication: Publication,
        segment_uuid_if_known: str | None = None,
    ) -> bool:
        try:
            self.service.discover_measurement_location(identity.uuid, segment_uuid_if_known)
        except BottomUpDiscoveryError as exc:
            LOGGER.warning("Bottom-up discovery failed; publication retained. error=%s", exc)
            return False
        return True
