from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from .configuration import ConfigurationError, load_config
from .control_settings import CollectorControlSettingsReader, DataRetentionSettingsReader
from .logging_setup import configure_logging
from .publication_processor import build_publication_handler
from .retention import MeasurementRetentionService
from .runtime_status import CollectorRuntimeStatusStore
from .worker import PublicationCollectorWorker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mcegold-publication-collector",
        description="Run the MCEGold Discovery Publication Collector.",
    )
    parser.add_argument(
        "config_path",
        nargs="?",
        help="Optional Collector JSON configuration path. Defaults to MCEGOLD_COLLECTOR_CONFIG or environment overrides.",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        config = load_config(args.config_path)
    except ConfigurationError as exc:
        configure_logging("ERROR")
        logging.getLogger(__name__).error("Configuration validation failed: %s", exc)
        return 2

    configure_logging(config.log_level)
    stop_event = threading.Event()
    runtime_status = CollectorRuntimeStatusStore(config.discovery_database_path)
    control_settings_reader = CollectorControlSettingsReader(
        config.discovery_database_path,
        fallback_polling_interval_seconds=config.polling_interval_seconds,
    )
    retention_settings_reader = DataRetentionSettingsReader(config.discovery_database_path)
    retention_service = MeasurementRetentionService(config.discovery_database_path)
    worker = PublicationCollectorWorker(
        config,
        stop_event=stop_event,
        handler=build_publication_handler(config),
        runtime_status=runtime_status,
        control_settings_reader=control_settings_reader,
        retention_settings_reader=retention_settings_reader,
        retention_service=retention_service,
    )

    def stop(signum: int, _frame: object) -> None:
        logging.getLogger(__name__).info("Received signal %s.", signum)
        worker.request_stop()

    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)

    worker.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
