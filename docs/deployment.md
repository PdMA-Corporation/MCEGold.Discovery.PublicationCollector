# Deployment

This guide describes deploying `MCEGold.Discovery.PublicationCollector` v1.0.0 as a standalone Python process, with Docker as an optional deployment mechanism. Standalone means the Collector has its own process; it still depends on a compatible Discovery database, MCEGold Data Services configuration, and a separately obtained Connector CLI.

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

The Collector repository does not contain a bundled Connector CLI, prebuilt Docker image, Docker registry publication, credentials, or generated local runtime files. A public release package can be assembled from the source, documentation, examples, license, Python packaging metadata, and quick-start seed database while keeping the Connector CLI as a separately distributed prerequisite.

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

- Python 3.11 or newer for direct source deployment. Docker is optional for container deployment.
- A self-contained Linux x64 `MCEGold.Data.Services.Connector.Cli` executable obtained separately from `MCEGold.Data.Services.Connector.Toolkit`.
- A compatible SQLite database created and migrated by `MCEGold.Discovery.Portal`, or a writable quick-start copy made from the included Portal-generated seed database.
- MCEGold Data Services connection settings in the Connector CLI configuration file or supplied through the Connector CLI's supported environment/secret mechanisms.
- Local filesystem permissions allowing the Collector process or container to read its config files, execute the Connector CLI, and read/write the SQLite database as required by the Portal schema and Collector runtime behavior.

The supported Linux Connector CLI package is self-contained. The Collector Docker image does not need a system .NET runtime solely to execute that CLI.

## Validated Standalone Linux Layout

The validated v1.0.0 standalone Linux workflow used this repository as the Collector source and placed local runtime inputs beside it:

```text
config/
  collector.local.json
  connector.config.json
data/
  discovery.seed.db
  discovery_portal.db
external/
  mcegold-cli/
    MCEGold.Data.Services.Connector.Cli
    configs/
      connector.config.example.json
    ...
```

`external/mcegold-cli/` should contain the complete separately distributed self-contained Linux x64 Connector CLI package. Keep the package files together; do not copy only `MCEGold.Data.Services.Connector.Cli`, because the self-contained distribution includes runtime/supporting files that must remain with it.

The direct Python/Linux deployment path is the validated standalone path for v1.0.0. Docker remains optional.

## Connector CLI Placement

The validated standalone Linux layout expects this executable:

```text
external/mcegold-cli/MCEGold.Data.Services.Connector.Cli
```

The public Docker examples mount the same CLI package at this path inside the container:

```text
/opt/mcegold-cli/MCEGold.Data.Services.Connector.Cli
```

On Linux hosts, confirm executable permissions before starting the Collector:

```bash
chmod +x ./external/mcegold-cli/MCEGold.Data.Services.Connector.Cli
test -x ./external/mcegold-cli/MCEGold.Data.Services.Connector.Cli
```

The Collector application still supports `.dll` paths for source-build or development scenarios by invoking them through `dotnet`, but `.dll` invocation is not the supported public Linux deployment path for the self-contained Toolkit package.

## Connector Configuration

Create the Connector CLI configuration from the example included with the separately distributed Connector package:

```bash
cp external/mcegold-cli/configs/connector.config.example.json config/connector.config.json
```

Edit `config/connector.config.json` with site-appropriate placeholder-derived values before a live run:

```json
{
  "host": "https://your-server/connector/1.0",
  "authenticationScheme": "BasicApi",
  "apiKey": "",
  "userName": "",
  "password": "",
  "publication": {
    "channel": ""
  },
  "request": {
    "channel": ""
  }
}
```

`connector.config.json` owns MCEGold Data Services connectivity:

- host;
- authentication scheme;
- credentials;
- optional publication channel override;
- optional request channel override.

Publication and request channel values can remain blank when the server/default behavior should determine the channel. Do not commit this file with real values.

Validate the Connector configuration before starting the Collector:

```bash
external/mcegold-cli/MCEGold.Data.Services.Connector.Cli \
  config validate \
  --config config/connector.config.json
```

## Collector Configuration

Copy the public template to a local file:

```powershell
copy config\collector.example.json config\collector.local.json
```

The distributed `config/collector.example.json` uses container-oriented paths. For the validated standalone Linux layout, copy it and update the important local paths:

```json
{
  "cliPath": "external/mcegold-cli/MCEGold.Data.Services.Connector.Cli",
  "connectorConfigPath": "config/connector.config.json",
  "discoveryDatabasePath": "data/discovery_portal.db",
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

## Standalone Configuration Model

Standalone deployments use two local configuration files with separate ownership:

```text
config/collector.local.json
  Collector runtime paths and behavior:
  cliPath, connectorConfigPath, discoveryDatabasePath, pollingIntervalSeconds,
  logLevel, cliTimeoutSeconds, includeRaw

config/connector.config.json
  Connector CLI / MCEGold Data Services settings:
  host, authenticationScheme, apiKey, userName, password,
  publication.channel, request.channel
```

The Collector passes `connectorConfigPath` to `MCEGold.Data.Services.Connector.Cli` as `--config`. The Connector CLI owns validation of MCEGold Data Services endpoint, authentication, and optional channel settings.

Blank `publication.channel` and `request.channel` values are supported when the Connector environment does not require explicit channel selection.

Supported Connector CLI process environment variables can override values from `connector.config.json` through normal subprocess inheritance. This includes:

- `MCEGOLD_HOST`
- `MCEGOLD_AUTH_SCHEME`
- `MCEGOLD_API_KEY`
- `MCEGOLD_USERNAME`
- `MCEGOLD_PASSWORD`

Docker secrets, mounted secret files, or another approved local secret-injection mechanism may also be used according to Connector CLI support.

## Optional Portal Compatibility

For Portal-aligned deployments, `MCEGOLD_PORTAL_LOCAL_CONFIG_PATH` can point to a Portal local configuration file containing:

- `mcegoldDataServicesUrl`
- `mcegoldUsername`
- `mcegoldPassword`
- `mcegoldApiKey`

When supplied, the Collector passes those Portal-derived values to the Connector CLI process as:

- `MCEGOLD_HOST`
- `MCEGOLD_USERNAME`
- `MCEGOLD_PASSWORD`
- `MCEGOLD_API_KEY`

Portal local config is optional compatibility input. It is not required for standalone Collector startup.

## Discovery Database

The Collector does not create or migrate the Discovery database schema. It requires a SQLite database initialized and migrated by `MCEGold.Discovery.Portal`.

For quick-start evaluation, the repository includes:

```text
data/discovery.seed.db
```

This is a pristine, Portal-generated, preinitialized SQLite database. Treat it as read-only reference input from the user's perspective. Before running the Collector, copy it to the normal writable runtime database name.

PowerShell:

```powershell
copy data\discovery.seed.db data\discovery_portal.db
```

Linux shell:

```bash
cp data/discovery.seed.db data/discovery_portal.db
```

Use `data/discovery_portal.db` as the writable runtime copy. Never run the Collector directly against `data/discovery.seed.db`; the seed is the pristine distributable database. Normal/full deployments may continue to provide a database initialized and migrated by `MCEGold.Discovery.Portal`.

The public Docker examples expect the database at:

```text
/data/discovery_portal.db
```

Mount the host directory containing that database at `/data`. If required tables are unavailable, the Collector logs runtime/control warnings and retries where supported, but production schema creation remains the Portal's responsibility.

`config/collector.local.json`, `config/connector.config.json`, `config/portal.local.json`, `data/discovery_portal.db`, and `external/mcegold-cli/` are local deployment inputs and should remain outside Git. `data/discovery.seed.db` is the intentionally public quick-start seed database and should remain pristine.

## Direct Python Deployment

Create a virtual environment and install the Collector from source.

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
copy config\collector.example.json config\collector.local.json
copy external\mcegold-cli\configs\connector.config.example.json config\connector.config.json
copy data\discovery.seed.db data\discovery_portal.db
$env:MCEGOLD_COLLECTOR_CONFIG = "config\collector.local.json"
python -m mcegold_discovery_publication_collector
```

Linux shell:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp config/collector.example.json config/collector.local.json
cp external/mcegold-cli/configs/connector.config.example.json config/connector.config.json
cp data/discovery.seed.db data/discovery_portal.db
export MCEGOLD_COLLECTOR_CONFIG="config/collector.local.json"
python -m mcegold_discovery_publication_collector
```

Python 3.11 or newer is required. On Debian/Ubuntu systems, virtual-environment support may be packaged separately; install it when needed with:

```bash
sudo apt install python3-venv
```

Other Linux distributions may package virtual-environment support differently.

Before a live run:

1. Edit `config/connector.config.json` with MCEGold Data Services endpoint/authentication and optional channel settings.
2. Validate `config/connector.config.json` with the Connector CLI.
3. Edit `config/collector.local.json` so `cliPath`, `connectorConfigPath`, and `discoveryDatabasePath` match the standalone layout.
4. Confirm `data/discovery_portal.db` is the writable copy of `data/discovery.seed.db`, not the seed itself.

## Starting And Stopping

Start the Collector:

```bash
export MCEGOLD_COLLECTOR_CONFIG="config/collector.local.json"
python -m mcegold_discovery_publication_collector
```

At `INFO` level, users should see lifecycle events such as startup, subscription open, publication processing activity, publication removal, warnings, and shutdown. Individual polling cycles are logged at `DEBUG` level. With `"pollingIntervalSeconds": 15`, the Collector continues polling even if `INFO` logs do not print a line every 15 seconds.

The startup message `Automatic data retention is disabled.` means automatic deletion/retention cleanup is disabled. It does not mean publication polling is disabled.

Press Ctrl+C to request graceful shutdown. The worker requests shutdown and closes the Connector CLI publication subscription where possible.

## Optional Docker Deployment

Docker is optional. It was not the required path for v1.0.0 direct application validation.

### Build The Docker Image

From the repository root:

```powershell
docker build -t mcegold-discovery-publication-collector:1.0.0 .
```

The Dockerfile installs the Python Collector package only. It does not bundle, download, or redistribute the Connector CLI.

### Run With Docker

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

### Run With Docker Compose

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
