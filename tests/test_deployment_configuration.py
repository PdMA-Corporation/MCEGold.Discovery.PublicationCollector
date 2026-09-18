from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_public_compose_is_collector_only_example() -> None:
    compose = (REPO_ROOT / "compose.example.yaml").read_text(encoding="utf-8")

    assert "publication-collector:" in compose
    assert "portal:" not in compose
    assert "grafana:" not in compose
    assert "../MCEGold.Discovery.Portal" not in compose
    assert "restart: unless-stopped" in compose


def test_public_compose_documents_external_runtime_inputs() -> None:
    compose = (REPO_ROOT / "compose.example.yaml").read_text(encoding="utf-8")

    assert "MCEGOLD_DISCOVERY_DATABASE_PATH: /data/discovery_portal.db" in compose
    assert "MCEGOLD_CONNECTOR_CONFIG_PATH: /config/connector.config.json" in compose
    assert "MCEGOLD_CLI_HOST_PATH" in compose
    assert "./data:/data" in compose
    assert "./config:/config:ro" in compose
    assert ":/opt/mcegold-cli:ro" in compose


def test_compose_keeps_connector_credentials_in_environment_without_values() -> None:
    compose = (REPO_ROOT / "compose.example.yaml").read_text(encoding="utf-8")

    for name in ("MCEGOLD_HOST", "MCEGOLD_USERNAME", "MCEGOLD_PASSWORD", "MCEGOLD_API_KEY"):
        assert f"{name}: ${{{name}:-}}" in compose


def test_dockerfile_does_not_copy_bundled_connector_cli() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY docker/mcegold-cli" not in dockerfile
    assert "MCEGold.Data.Services.Connector.Cli" in dockerfile
    assert "external runtime dependency" in dockerfile
    assert "mkdir -p /config /data /opt/mcegold-cli" in dockerfile


def test_docker_build_context_excludes_local_configs_databases_and_binaries() -> None:
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")

    for pattern in (
        "config/collector.local.json",
        "config/connector.config.json",
        "*.db",
        "*.sqlite",
        "*.sqlite3",
        "docker/mcegold-cli/",
        "*.dll",
        "*.exe",
        "*.pdb",
        ".pytest_tmp/",
        "*.egg-info/",
    ):
        assert pattern in dockerignore

