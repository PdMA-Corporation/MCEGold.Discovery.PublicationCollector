# MCEGold Discovery Publication Collector

MCEGold.Discovery.PublicationCollector is a Python console collector for MCEGold measurement-data publications. It opens a consumer publication subscription through `MCEGold.Data.Services.Connector.Cli`, reads staged publication messages, parses `syncMeasurements` payloads, classifies measurement locations against the Discovery database, persists known measurements, and removes publications only after successful handling.

This repository is intended to contain the collector source, tests, configuration templates, and public deployment examples. It does not include internal Git history or compiled Connector CLI output.

## Architecture Role

PublicationCollector is one component in the MCEGold Discovery architecture:

- `MCEGold.Discovery.Portal` owns and initializes the SQLite application database and schema.
- `MCEGold.Discovery.PublicationCollector` consumes a compatible Portal-created database.
- `MCEGold.Data.Services.Connector.Cli` owns communication with MCEGold Data Services and ISBM-facing publication commands.

PublicationCollector does not call MCEGold or ISBM HTTP endpoints directly. It invokes Connector CLI staged publication commands as a subprocess and parses the CLI JSON envelope.

## Current Public-Release Status

This public-release candidate intentionally does not bundle `MCEGold.Data.Services.Connector.Cli` binaries. The Connector CLI is a required runtime dependency, but its final public distribution mechanism is not defined in this repository.

Until that is resolved, an external developer can inspect, test, and build the Python package, but a live collector run requires separately obtaining the Connector CLI and a compatible Portal-created SQLite database.

## Prerequisites

For source development and tests:

- Python 3.11 or newer
- `pip`
- `pytest`, installed through the project test extra

For live collection:

- A separately obtained `MCEGold.Data.Services.Connector.Cli`
- A .NET runtime compatible with that Connector CLI. The current Dockerfile installs the .NET 8 runtime because the inspected bundled CLI metadata targeted .NET 8.
- A compatible SQLite database initialized by `MCEGold.Discovery.Portal` schema v17 or later
- MCEGold Data Services connection settings supplied through the Portal local config or process environment

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[test]
copy config\collector.example.json config\collector.local.json
$env:MCEGOLD_COLLECTOR_CONFIG = "config\collector.local.json"
python -m mcegold_discovery_publication_collector
```

Before a live run, edit `config\collector.local.json` for your local paths. Do not commit local configuration files or credentials.

## Configuration

The collector loads JSON configuration from `MCEGOLD_COLLECTOR_CONFIG` when set. `config/collector.example.json` is a safe template using Docker-oriented example paths:

- `cliPath`: path to `MCEGold.Data.Services.Connector.Cli`; may point to an executable or a `.dll`
- `connectorConfigPath`: Connector CLI configuration file path
- `discoveryDatabasePath`: Portal-created SQLite database path
- `pollingIntervalSeconds`, default `15`
- `logLevel`, default `INFO`
- `cliTimeoutSeconds`, default `60`
- `includeRaw`, default `false`

Environment overrides:

- `MCEGOLD_COLLECTOR_CONFIG`
- `MCEGOLD_CLI_PATH`
- `MCEGOLD_CONNECTOR_CONFIG_PATH`
- `MCEGOLD_DISCOVERY_DATABASE_PATH`
- `MCEGOLD_POLL_INTERVAL_SECONDS`
- `MCEGOLD_LOG_LEVEL`
- `MCEGOLD_CLI_TIMEOUT_SECONDS`
- `MCEGOLD_INCLUDE_RAW`
- `MCEGOLD_PORTAL_LOCAL_CONFIG_PATH`

For Portal-aligned deployments, the collector can load MCEGold Data Services connection settings from a Portal local config file containing:

- `mcegoldDataServicesUrl`
- `mcegoldUsername`
- `mcegoldPassword`
- `mcegoldApiKey`

The collector passes those values to the Connector CLI as:

- `MCEGOLD_HOST`
- `MCEGOLD_USERNAME`
- `MCEGOLD_PASSWORD`
- `MCEGOLD_API_KEY`

Do not commit real connector credentials, API keys, `.env` files, `connector.config.json`, or `collector.local.json`.

## Connector CLI Dependency

The collector uses these Connector CLI staged publication commands:

```text
MCEGold.Data.Services.Connector.Cli publication open-subscription --config <config> --output json
MCEGold.Data.Services.Connector.Cli publication read --config <config> --session-id <sessionId> --output json
MCEGold.Data.Services.Connector.Cli publication remove --config <config> --session-id <sessionId> --output json
MCEGold.Data.Services.Connector.Cli publication close-subscription --config <config> --session-id <sessionId> --output json
```

When `cliPath` points to a `.dll`, the collector invokes it as `dotnet <path>`. Otherwise it invokes the configured executable path directly.

This repository does not currently provide a verified public download, package feed, or release channel for the Connector CLI. That remains a public-release blocker for complete runtime use.

## Database Dependency

PublicationCollector does not create or migrate the application database schema. The SQLite database is owned and initialized by `MCEGold.Discovery.Portal`.

The collector expects a compatible Portal-created database, currently documented by the source as schema v17 or later for runtime status support. The Portal database should be initialized before the collector runs. If the collector starts before required tables are available, it logs telemetry/control warnings and retries where supported, but it still depends on the Portal-owned schema.

SQLite schemas created inside `tests/` are test fixtures only. They are not a supported production database creation path and should not be copied into this repository as product database scripts.

## Runtime Behavior

The worker opens one subscription session, reads publications without overlapping read calls, parses `syncMeasurements`, checks `DimMeasurementLocation.MeasurementLocationUuid`, persists known measurements to `FactMeasurement`, and removes a publication only after successful handling.

Unknown measurement locations are retained for later retry and may trigger bottom-up discovery behavior where implemented. The collector also writes heartbeat/runtime status to `CollectorRuntimeStatus`, reads control settings from `ApplicationSetting`, and can run measurement retention cleanup based on Portal-owned settings.

## Docker

The Dockerfile installs the Python package and prepares expected directories under `/app`, `/config`, `/data`, and `/opt/mcegold-cli`.

The image intentionally does not copy `docker/mcegold-cli/` or any compiled Connector CLI payload. To run the container, provide the Connector CLI separately at `/opt/mcegold-cli` and provide a Portal-created database at `/data/discovery_portal.db`.

Because the Connector CLI public distribution mechanism is unresolved, Docker source structure is prepared, but full runtime image use remains blocked until a legitimate Connector CLI acquisition path is selected.

## Compose Example

`compose.example.yaml` contains a collector-only example. It does not build or mount sibling Portal source trees, Portal-owned Grafana dashboards, or internal deployment paths.

Typical local use requires:

- `./data/discovery_portal.db`: a compatible Portal-created SQLite database
- `./config/connector.config.json`: local Connector CLI config, excluded from Git
- `MCEGOLD_CLI_HOST_PATH`: host directory containing the separately obtained Connector CLI
- MCEGold connection settings supplied through environment, `.env`, Docker secrets, or another local injection mechanism

Example:

```powershell
$env:MCEGOLD_CLI_HOST_PATH = "C:\path\to\mcegold-cli"
docker compose -f compose.example.yaml up --build
```

Do not commit `.env`, connector configuration, runtime databases, logs, or local CLI payloads.

## Tests

```text
pytest
```

The test suite uses synthetic credential-looking strings such as `secret`, `super-secret`, and `api-secret` to verify redaction and environment passing. These are test fixtures, not real credentials.

## Troubleshooting

- `Missing required configuration`: confirm `MCEGOLD_COLLECTOR_CONFIG` or the individual environment overrides point to valid paths.
- Connector command fails: confirm `cliPath`/`MCEGOLD_CLI_PATH` points to the Connector CLI and that .NET is available when using a `.dll`.
- Database table errors: confirm the SQLite database was initialized by a compatible Portal version before starting the collector.
- No publications are removed: the collector intentionally removes publications only after successful handling.

## Licensing

`pyproject.toml` currently declares MIT license metadata. A `LICENSE` file has not been added in this candidate because the authoritative copyright holder/year must be confirmed before publishing.
