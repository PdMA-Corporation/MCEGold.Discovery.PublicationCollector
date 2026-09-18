FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MCEGOLD_COLLECTOR_CONFIG=/app/config/collector.example.json \
    MCEGOLD_CLI_PATH=/opt/mcegold-cli/MCEGold.Data.Services.Connector.Cli \
    MCEGOLD_CONNECTOR_CONFIG_PATH=/config/connector.config.json \
    MCEGOLD_DISCOVERY_DATABASE_PATH=/data/discovery_portal.db \
    DOTNET_ROOT=/usr/share/dotnet \
    PATH="${PATH}:/usr/share/dotnet"

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates libicu-dev \
    && curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh \
    && chmod +x /tmp/dotnet-install.sh \
    && /tmp/dotnet-install.sh --channel 8.0 --runtime dotnet --install-dir /usr/share/dotnet \
    && rm /tmp/dotnet-install.sh \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN mkdir -p /config /data /opt/mcegold-cli

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config

RUN pip install --no-cache-dir .

# The Connector CLI is an external runtime dependency and is intentionally not
# bundled in this public-release candidate. Mount or add a separately obtained
# MCEGold.Data.Services.Connector.Cli distribution at /opt/mcegold-cli.

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

CMD ["mcegold-publication-collector"]
