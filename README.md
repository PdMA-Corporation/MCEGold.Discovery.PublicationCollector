# MCEGold Discovery Publication Collector

MCEGold.Discovery.PublicationCollector is a Python console collector for MCEGold measurement-data publications. It opens a consumer publication subscription through `MCEGold.Data.Services.Connector.Cli`, reads staged publication messages, parses `syncMeasurements` payloads, classifies measurement locations against the Discovery database, persists known measurements, and removes publications only after successful handling.

This repository is intended to contain the collector source, tests, configuration templates, and public deployment examples. It does not include internal Git history or compiled Connector CLI output.

For deployment details, see [docs/deployment.md](docs/deployment.md).

## Architecture Role

PublicationCollector is one component in the MCEGold Discovery architecture:

- `MCEGold.Discovery.Portal` owns and initializes the SQLite application database and schema.
- `MCEGold.Discovery.PublicationCollector` consumes a compatible Portal-created database.
- `MCEGold.Data.Services.Connector.Cli` owns communication with MCEGold Data Services and ISBM-facing publication commands.

PublicationCollector does not call MCEGold or ISBM HTTP endpoints directly. It invokes Connector CLI staged publication commands as a subprocess and parses the CLI JSON envelope.

## Current Public-Release Status

This public-release candidate intentionally does not bundle `MCEGold.Data.Services.Connector.Cli` binaries. The Connector CLI is a required runtime dependency and is distributed separately through `MCEGold.Data.Services.Connector.Toolkit`.

The Collector v1.0.0 release is source-oriented. It provides this repository, Dockerfile, Compose example, configuration examples, documentation, license, and tests. GitHub source archives are sufficient for the Collector release; no separate Collector runtime package, wheel, bundled Connector CLI, or prebuilt Docker image is produced by this repository.

## Prerequisites

For source development and tests:

- Python 3.11 or newer
- `pip`
- `pytest`, installed through the project test extra

For live collection:

- A separately obtained self-contained Linux `MCEGold.Data.Services.Connector.Cli` executable from `MCEGold.Data.Services.Connector.Toolkit`
- A compatible SQLite database initialized by `MCEGold.Discovery.Portal` schema v17 or later
- MCEGold Data Services connection settings in the Connector CLI configuration file or supplied through the Connector CLI's supported environment/secret mechanisms

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[test]
copy config\collector.example.json config\collector.local.json
copy data\discovery.seed.db data\discovery_portal.db
$env:MCEGOLD_COLLECTOR_CONFIG = "config\collector.local.json"
python -m mcegold_discovery_publication_collector
```

Before a live run, edit `config\collector.local.json` for your local paths. Do not commit local configuration files or credentials.

On Linux shells:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .[test]
cp config/collector.example.json config/collector.local.json
cp data/discovery.seed.db data/discovery_portal.db
export MCEGOLD_COLLECTOR_CONFIG="config/collector.local.json"
python -m mcegold_discovery_publication_collector
```

Python 3.11 or newer is required. On Debian/Ubuntu systems, virtual-environment support may be packaged separately; install it when needed with `sudo apt install python3-venv`.

`data/discovery.seed.db` is a Portal-generated, preinitialized quick-start database included for developers who want to try the Publication Collector before running `MCEGold.Discovery.Portal`. Treat it as pristine reference input. Copy it to `data/discovery_portal.db` and let the Collector use that writable runtime copy.

## Configuration

The collector loads JSON configuration from `MCEGOLD_COLLECTOR_CONFIG` when set. `config/collector.example.json` is a safe template using Docker-oriented example paths. The public Docker examples use `/opt/mcegold-cli/MCEGold.Data.Services.Connector.Cli`, which matches the self-contained Linux Connector CLI executable distributed through `MCEGold.Data.Services.Connector.Toolkit`.

- `cliPath`: path to `MCEGold.Data.Services.Connector.Cli`; may point to an executable or a `.dll`
- `connectorConfigPath`: Connector CLI configuration file path; this file owns MCEGold Data Services endpoint, authentication, and optional publication/request channel settings
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

Standalone deployments should put MCEGold Data Services settings in `connector.config.json` using the Connector CLI shape:

- `host`
- `authenticationScheme`
- `apiKey`
- `userName`
- `password`
- `publication.channel`
- `request.channel`

Blank `publication.channel` and `request.channel` values are supported when the Connector environment does not require explicit channel selection. The Collector passes `connectorConfigPath` to the Connector CLI as `--config`; the Connector CLI performs its normal validation. Supported Connector CLI environment variables such as `MCEGOLD_HOST`, `MCEGOLD_USERNAME`, `MCEGOLD_PASSWORD`, and `MCEGOLD_API_KEY` may override the Connector configuration through normal subprocess inheritance.

For Portal-aligned compatibility deployments, the collector can optionally load MCEGold Data Services connection settings from a Portal local config file containing:

- `mcegoldDataServicesUrl`
- `mcegoldUsername`
- `mcegoldPassword`
- `mcegoldApiKey`

The collector passes those optional Portal-derived values to the Connector CLI as:

- `MCEGOLD_HOST`
- `MCEGOLD_USERNAME`
- `MCEGOLD_PASSWORD`
- `MCEGOLD_API_KEY`

Portal local config is optional compatibility input and is not required for standalone Collector startup.

Do not commit real connector credentials, API keys, `.env` files, `connector.config.json`, `collector.local.json`, or `portal.local.json`.

## Connector CLI Dependency

The collector uses these Connector CLI staged publication commands:

```text
MCEGold.Data.Services.Connector.Cli publication open-subscription --config <config> --output json
MCEGold.Data.Services.Connector.Cli publication read --config <config> --session-id <sessionId> --output json
MCEGold.Data.Services.Connector.Cli publication remove --config <config> --session-id <sessionId> --output json
MCEGold.Data.Services.Connector.Cli publication close-subscription --config <config> --session-id <sessionId> --output json
```

For supported public Linux deployment, set `cliPath` or `MCEGOLD_CLI_PATH` to the self-contained executable:

```text
/opt/mcegold-cli/MCEGold.Data.Services.Connector.Cli
```

The Collector invokes that executable directly. The application still supports `.dll` paths for source-build or development scenarios by invoking them as `dotnet <path>`, but the published Linux deployment model should not require a system .NET runtime solely for the Connector CLI.

## Database Dependency

PublicationCollector does not create or migrate the application database schema. The SQLite database is owned and initialized by `MCEGold.Discovery.Portal`.

The collector expects a compatible Portal-created database, currently documented by the source as schema v17 or later for runtime status support. The Portal database should be initialized before the collector runs. If the collector starts before required tables are available, it logs telemetry/control warnings and retries where supported, but it still depends on the Portal-owned schema.

For quick-start use only, this repository includes `data/discovery.seed.db`, a pristine Portal-generated schema v17 database. Copy it to `data/discovery_portal.db` before running the Collector. Normal deployments may continue to use a database initialized and migrated by `MCEGold.Discovery.Portal`.

SQLite schemas created inside `tests/` are test fixtures only. They are not a supported production database creation path and should not be copied into this repository as product database scripts.

## Runtime Behavior

The worker opens one subscription session, reads publications without overlapping read calls, parses `syncMeasurements`, checks `DimMeasurementLocation.MeasurementLocationUuid`, persists known measurements to `FactMeasurement`, and removes a publication only after successful handling.

Unknown measurement locations trigger automatic, on-demand bottom-up discovery during publication processing. The targeted discovery path can resolve the triggering measurement location, its segment, associated site information when required, and measurement locations associated with the relevant segment. The collector does not run a separate inventory-wide discovery or historical replay workflow. If bottom-up discovery or the follow-up lookup/persistence step fails, the original publication is retained for retry.

The collector also writes heartbeat/runtime status to `CollectorRuntimeStatus`, reads control settings from `ApplicationSetting`, and can run measurement retention cleanup based on Portal-owned settings.

## Docker

The Dockerfile installs the Python package and prepares expected directories under `/app`, `/config`, `/data`, and `/opt/mcegold-cli`.

The image intentionally does not copy `docker/mcegold-cli/`, download the Connector CLI, or include any compiled Connector CLI payload. The Collector image also does not install .NET solely for the Connector CLI because the supported Linux Toolkit CLI is self-contained. To run the container, provide the Connector CLI separately at `/opt/mcegold-cli/MCEGold.Data.Services.Connector.Cli` and provide a Portal-created database at `/data/discovery_portal.db`.

Ensure the mounted Linux CLI file is executable on the host before starting the container.

## Compose Example

`compose.example.yaml` contains a collector-only example. It does not build or mount sibling Portal source trees, Portal-owned Grafana dashboards, or internal deployment paths.

Typical local use requires:

- `./data/discovery_portal.db`: a writable runtime database, either copied from `./data/discovery.seed.db` for quick-start use or initialized by `MCEGold.Discovery.Portal`
- `./config/connector.config.json`: local Connector CLI config for endpoint/authentication and optional channel selection, excluded from Git
- `MCEGOLD_CLI_HOST_PATH`: host directory containing the separately obtained self-contained Linux Connector CLI executable
- MCEGold connection settings supplied through `connector.config.json`, environment, `.env`, Docker secrets, or another local injection mechanism

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
- Connector command fails: confirm `cliPath`/`MCEGOLD_CLI_PATH` points to the mounted self-contained Linux Connector CLI executable and that the file is executable.
- Database table errors: confirm the SQLite database was initialized by a compatible Portal version before starting the collector.
- No publications are removed: the collector intentionally removes publications only after successful handling.

## Licensing

This project is licensed under the MIT License. See [LICENSE](LICENSE).
