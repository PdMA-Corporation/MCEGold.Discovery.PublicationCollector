import sqlite3
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_DATABASE = REPO_ROOT / "data" / "discovery.seed.db"


def test_public_compose_is_collector_only_example() -> None:
    compose = (REPO_ROOT / "compose.example.yaml").read_text(encoding="utf-8")

    assert "publication-collector:" in compose
    assert "portal:" not in compose
    assert "grafana:" not in compose
    assert "../MCEGold.Discovery.Portal" not in compose
    assert "restart: unless-stopped" in compose


def test_public_compose_documents_external_runtime_inputs() -> None:
    compose = (REPO_ROOT / "compose.example.yaml").read_text(encoding="utf-8")

    assert "MCEGOLD_CLI_PATH: /opt/mcegold-cli/MCEGold.Data.Services.Connector.Cli" in compose
    assert "MCEGold.Data.Services.Connector.Cli.dll" not in compose
    assert "MCEGOLD_DISCOVERY_DATABASE_PATH: /data/discovery_portal.db" in compose
    assert "MCEGOLD_CONNECTOR_CONFIG_PATH: /config/connector.config.json" in compose
    assert "MCEGOLD_CLI_HOST_PATH" in compose
    assert "./data:/data" in compose
    assert "copy ./data/discovery.seed.db" in compose
    assert "./data/discovery_portal.db" in compose
    assert "./config:/config:ro" in compose
    assert ":/opt/mcegold-cli:ro" in compose


def test_compose_keeps_connector_credentials_in_environment_without_values() -> None:
    compose = (REPO_ROOT / "compose.example.yaml").read_text(encoding="utf-8")

    for name in ("MCEGOLD_HOST", "MCEGOLD_USERNAME", "MCEGOLD_PASSWORD", "MCEGOLD_API_KEY"):
        assert f"{name}: ${{{name}:-}}" in compose


def test_dockerfile_does_not_copy_bundled_connector_cli() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY docker/mcegold-cli" not in dockerfile
    assert "MCEGOLD_CLI_PATH=/opt/mcegold-cli/MCEGold.Data.Services.Connector.Cli" in dockerfile
    assert "MCEGold.Data.Services.Connector.Cli.dll" not in dockerfile
    assert "external runtime dependency" in dockerfile
    assert "mkdir -p /config /data /opt/mcegold-cli" in dockerfile


def test_dockerfile_does_not_install_dotnet_runtime_for_self_contained_cli() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "dotnet-install.sh" not in dockerfile
    assert "--runtime dotnet" not in dockerfile
    assert "DOTNET_ROOT" not in dockerfile


def test_public_collector_example_uses_self_contained_linux_cli_path() -> None:
    example = (REPO_ROOT / "config" / "collector.example.json").read_text(encoding="utf-8")

    assert '"/opt/mcegold-cli/MCEGold.Data.Services.Connector.Cli"' in example


def test_deployment_documentation_reflects_collector_role_and_discovery_scope() -> None:
    deployment = (REPO_ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")

    assert "continuous publication-processing service" in deployment
    assert "bottom-up discovery automatically and on demand during publication processing" in deployment
    assert "triggering measurement location, its segment, associated site information" in deployment
    assert "does not implement a separate inventory-wide discovery or historical replay workflow" in deployment
    assert "/opt/mcegold-cli/MCEGold.Data.Services.Connector.Cli" in deployment
    assert "/opt/mcegold-cli/MCEGold.Data.Services.Connector.Cli.dll" not in deployment
    assert "does not need a system .NET runtime solely to execute that CLI" in deployment


def test_quick_start_seed_database_exists_and_is_valid_sqlite() -> None:
    assert SEED_DATABASE.is_file()

    with sqlite3.connect(f"file:{SEED_DATABASE.as_posix()}?mode=ro", uri=True) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        table_names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {
            "ApplicationSetting",
            "CollectorRuntimeStatus",
            "DimMeasurementLocation",
            "DimSegment",
            "DimSite",
            "FactMeasurement",
            "SchemaVersion",
        }.issubset(table_names)
        assert connection.execute("SELECT MAX(version) FROM SchemaVersion").fetchone()[0] >= 17


def test_quick_start_documentation_uses_seed_copy_and_runtime_database() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    deployment = (REPO_ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")

    for text in (readme, deployment):
        assert "data/discovery.seed.db" in text
        assert "data/discovery_portal.db" in text
        assert "Portal-generated" in text
        assert "pristine" in text
        assert "writable runtime" in text
    assert "copy data\\discovery.seed.db data\\discovery_portal.db" in readme
    assert "cp data/discovery.seed.db data/discovery_portal.db" in readme
    assert "copy data\\discovery.seed.db data\\discovery_portal.db" in deployment
    assert "cp data/discovery.seed.db data/discovery_portal.db" in deployment


def test_license_file_is_present_with_public_release_copyright() -> None:
    license_text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")

    assert "MIT License" in license_text
    assert "Copyright (c) 2026 PdMA Corporation" in license_text


def test_docker_build_context_excludes_local_configs_databases_and_binaries() -> None:
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")

    for pattern in (
        "config/collector.local.json",
        "config/connector.config.json",
        "config/portal.local.json",
        "*.db",
        "*.sqlite",
        "*.sqlite3",
        "docker/mcegold-cli/",
        "*.dll",
        "*.exe",
        "*.so",
        "*.so.*",
        "*.dylib",
        "*.pdb",
        ".pytest_tmp/",
        "*.egg-info/",
    ):
        assert pattern in dockerignore


def test_gitignore_keeps_runtime_database_ignored_but_allows_seed_database() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "data/*" in gitignore
    assert "*.db" in gitignore
    assert "*.db-*" in gitignore
    assert "!data/discovery.seed.db" in gitignore
    assert "data/discovery_portal.db" not in gitignore

