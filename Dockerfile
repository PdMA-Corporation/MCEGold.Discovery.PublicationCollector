FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MCEGOLD_COLLECTOR_CONFIG=/app/config/collector.example.json \
    MCEGOLD_CLI_PATH=/opt/mcegold-cli/MCEGold.Data.Services.Connector.Cli \
    MCEGOLD_CONNECTOR_CONFIG_PATH=/config/connector.config.json \
    MCEGOLD_DISCOVERY_DATABASE_PATH=/data/discovery_portal.db

WORKDIR /app

RUN mkdir -p /config /data /opt/mcegold-cli

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config

RUN pip install --no-cache-dir .

# The Connector CLI is an external runtime dependency and is intentionally not
# bundled in this public-release candidate. Mount the separately obtained
# self-contained Linux MCEGold.Data.Services.Connector.Cli executable at
# /opt/mcegold-cli/MCEGold.Data.Services.Connector.Cli.

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

CMD ["mcegold-publication-collector"]
