# Deployment

This guide describes deploying `MCEGold.Discovery.PublicationCollector` v1.0.0 as a standalone process or container. Standalone means the Collector has its own process or container; it still depends on a Portal-created Discovery database, MCEGold Data Services configuration, and a separately obtained Connector CLI.

The Collector is a continuous publication-processing service. It opens a Connector CLI publication subscription, polls for staged `syncMeasurements` publications, persists measurements whose locations are already present in the Portal-created Discovery database, and removes each publication only after successful handling.

When a publication references an unknown `MeasurementLocationUuid`, the Collector performs bottom-up discovery automatically and on demand during publication processing. That targeted path can resolve the triggering measurement location, its segment, associated site information when required, and measurement locations associated with the relevant segment. The Collector does not implement a separate inventory-wide discovery or historical replay workflow.

## Deployment Model

The public Collector repository provides:

- source code;
- `Dockerfile`;
- `compose.example.yaml`;
- configuration examples;
- README and deployment documentation;
- license and tests.

The Collector repository does not provide a packaged Collector runtime, Python wheel for release, bundled Connector CLI, prebuilt Docker image, or Docker registry publication. GitHub source archives are sufficient for the Collector v1.0.0 release.

The separately distributed `MCEGold.Data.Services.Connector.Toolkit` provides the supported self-contained Linux x64 Connector CLI executable. The Collector invokes that executable as a subprocess.

Conceptually:

```text
Public Collector repository
  -> build Collector Docker image or run Python source
  -> provide Collector configuration
  -> provide Portal-created Discovery database
  -> provide separately obtained self-contained Connector CLI
  -> Collector uses Connector CLI to reach MCEGold Data Services
```

## Prerequisites

Live collection requires:

- Python 3.11 or newer for direct source deployment, or Docker for container deployment.
- A self-contained Linux x64 `MCEGold.Data.Services.Connector.Cli` executable obtained separately from `MCEGold.Data.Services.Connector.Toolkit`.
- A compatible SQLite database created and migrated by `MCEGold.Discovery.Portal`.
- MCEGold Data Services connection settings.
- Local filesystem permissions allowing the Collector process or container to read its config files, execute the Connector CLI, and read/write the SQLite database as required by the Portal schema and Collector runtime behavior.

The supported Linux Connector CLI package is self-contained. The Collector Docker image does not need a system .NET runtime solely to execute that CLI.

## Connector CLI Placement

The public Docker examples expect this path inside the container:

```text
/opt/mcegold-cli/MCEGold.Data.Services.Connector.Cli
```

Place the separately obtained Linux CLI executable in a host directory and mount that directory at `/opt/mcegold-cli`.

On Linux hosts, confirm executable permissions before starting the Collector:

```bash
chmod +x ./external/mcegold-cli/MCEGold.Data.Services.Connector.Cli
test -x ./external/mcegold-cli/MCEGold.Data.Services.Connector.Cli
```

The Collector application still supports `.dll` paths for source-build or development scenarios by invoking them through `dotnet`, but `.dll` invocation is not the supported public Linux deployment path for the self-contained Toolkit package.

## Collector Configuration

Copy the public template to a local file:

```powershell
copy config\collector.example.json config\collector.local.json
```

Typical Docker-oriented values:

```json
{
  "cliPath": "/opt/mcegold-cli/MCEGold.Data.Services.Connector.Cli",
  "connectorConfigPath": "/config/connector.config.json",
  "discoveryDatabasePath": "/data/discovery_portal.db",
  "pollingIntervalSeconds": 15,
  "logLevel": "INFO",
  "cliTimeoutSeconds": 60,
  "includeRaw": false
}
```

The Collector recognizes these environment overrides:

- `MCEGOLD_COLLECTOR_CONFIG`
- `MCEGOLD_CLI_PATH`
- `MCEGOLD_CONNECTOR_CONFIG_PATH`
- `MCEGOLD_DISCOVERY_DATABASE_PATH`
- `MCEGOLD_POLL_INTERVAL_SECONDS`
- `MCEGOLD_LOG_LEVEL`
- `MCEGOLD_CLI_TIMEOUT_SECONDS`
- `MCEGOLD_INCLUDE_RAW`
- `MCEGOLD_PORTAL_LOCAL_CONFIG_PATH`

Do not commit real `collector.local.json`, `connector.config.json`, `portal.local.json`, `.env` files, runtime databases, logs, or Connector CLI payloads.

## MCEGold Data Services Configuration

The Connector CLI needs MCEGold Data Services connection settings. Supply them through the Connector CLI configuration file, process environment, Docker secrets, or an approved local secret-injection mechanism.

For Portal-aligned deployments, `MCEGOLD_PORTAL_LOCAL_CONFIG_PATH` can point to a Portal local configuration file containing:

- `mcegoldDataServicesUrl`
- `mcegoldUsername`
- `mcegoldPassword`
- `mcegoldApiKey`

The Collector passes those values to the Connector CLI process as:

- `MCEGOLD_HOST`
- `MCEGOLD_USERNAME`
- `MCEGOLD_PASSWORD`
- `MCEGOLD_API_KEY`

## Discovery Database

The Collector does not create or migrate the Discovery database schema. It requires a SQLite database initialized and migrated by `MCEGold.Discovery.Portal`.

The public Docker examples expect the database at:

```text
/data/discovery_portal.db
```

Mount the host directory containing that database at `/data`. If required tables are unavailable, the Collector logs runtime/control warnings and retries where supported, but production schema creation remains the Portal's responsibility.

## Recommended Layout

One local Docker-oriented layout is:

```text
config/
  collector.local.json
  connector.config.json
data/
  discovery_portal.db
external/
  mcegold-cli/
    MCEGold.Data.Services.Connector.Cli
```

`config/collector.local.json`, `config/connector.config.json`, `config/portal.local.json`, `data/`, and `external/mcegold-cli/` are local deployment inputs and should remain outside Git.

## Build the Docker Image

From the repository root:

```powershell
docker build -t mcegold-discovery-publication-collector:1.0.0 .
```

The Dockerfile installs the Python Collector package only. It does not bundle, download, or redistribute the Connector CLI.

## Run with Docker

Example shape:

```powershell
docker run --rm `
  -e MCEGOLD_COLLECTOR_CONFIG=/config/collector.local.json `
  -e MCEGOLD_CLI_PATH=/opt/mcegold-cli/MCEGold.Data.Services.Connector.Cli `
  -e MCEGOLD_CONNECTOR_CONFIG_PATH=/config/connector.config.json `
  -e MCEGOLD_DISCOVERY_DATABASE_PATH=/data/discovery_portal.db `
  -v ${PWD}\config:/config:ro `
  -v ${PWD}\data:/data `
  -v ${PWD}\external\mcegold-cli:/opt/mcegold-cli:ro `
  mcegold-discovery-publication-collector:1.0.0
```

Adapt path syntax for your host shell and operating system.

## Run with Docker Compose

`compose.example.yaml` is a collector-only example. It does not build or mount sibling Portal source trees, Portal-owned dashboards, internal paths, or the Connector CLI from this repository.

Example:

```powershell
$env:MCEGOLD_CLI_HOST_PATH = ".\external\mcegold-cli"
docker compose -f compose.example.yaml up --build
```

The compose file mounts:

- `./data` at `/data`
- `./config` at `/config:ro`
- `${MCEGOLD_CLI_HOST_PATH:-./external/mcegold-cli}` at `/opt/mcegold-cli:ro`

Stop the foreground Compose deployment with Ctrl+C, or from another shell:

```powershell
docker compose -f compose.example.yaml down
```

## Direct Python Deployment

Create a virtual environment and install the Collector from source:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
copy config\collector.example.json config\collector.local.json
$env:MCEGOLD_COLLECTOR_CONFIG = "config\collector.local.json"
python -m mcegold_discovery_publication_collector
```

For Linux shells, use the equivalent virtual-environment activation and environment-variable syntax. Direct Python deployment still requires the separately obtained Connector CLI executable and a Portal-created SQLite database.

## Persistent Linux Service

This repository does not include a packaged systemd unit. A persistent Linux deployment can run the same command used for direct Python deployment or the installed console script:

```text
mcegold-publication-collector
```

Use your site's service manager to set the working directory, environment variables, mounted paths, restart policy, and secret source. The service account must be able to execute the Connector CLI and access the Discovery database.

## Runtime Behavior

- The Collector opens one publication subscription session.
- It reads publications without overlapping read calls.
- It removes a publication only after successful handling.
- Publications that cannot be parsed, discovered, or persisted are retained for retry.
- The Collector writes heartbeat/runtime status to `CollectorRuntimeStatus` and reads control settings from `ApplicationSetting` when those Portal-owned tables are available.
- Measurement retention cleanup can run according to Portal-owned settings.
- Shutdown requests trigger normal worker shutdown, including closing the Connector CLI publication subscription where possible.

## Logging

Set `logLevel` or `MCEGOLD_LOG_LEVEL` to the desired Python logging level, such as `INFO` or `DEBUG`. Container logs are emitted to standard output/error and can be collected by the container runtime.

## Upgrading

For a source deployment, review the release notes, update the repository checkout or source archive, rebuild/reinstall the Python package, and restart the Collector process.

For Docker, rebuild the Collector image from the updated source and restart the container. Keep the Connector CLI package lifecycle separate from the Collector source lifecycle, and verify compatibility before changing both at the same time.

The Portal-owned database should be migrated through `MCEGold.Discovery.Portal`, not through this Collector repository.

## Troubleshooting

- `Missing required configuration`: confirm `MCEGOLD_COLLECTOR_CONFIG` or the individual environment overrides point to valid values.
- Connector command cannot start: confirm the mounted `MCEGold.Data.Services.Connector.Cli` file exists and is executable.
- Connector command fails: confirm Connector CLI configuration and MCEGold Data Services credentials.
- Database table errors: confirm the SQLite database was initialized and migrated by a compatible Portal version before starting the Collector.
- Publications remain in the subscription: the Collector intentionally removes publications only after successful handling.
- Unknown measurement locations: confirm MCEGold request credentials/configuration allow the targeted bottom-up discovery requests needed to resolve the triggering location and related segment context.
