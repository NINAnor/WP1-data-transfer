FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Install system dependencies
RUN apt-get update \
    && apt-get -y --no-install-recommends install \
    wget ca-certificates rclone cron \
    && rm -rf /var/lib/apt/lists/*

# Install DuckDB CLI (used by 02_indexing scripts)
RUN wget -q https://github.com/duckdb/duckdb/releases/download/v1.2.1/duckdb_cli-linux-amd64.gz -O- | \
    gzip -d > /usr/local/bin/duckdb && \
    chmod +x /usr/local/bin/duckdb

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

# Copy pipeline steps
COPY 01_transfer/   ./01_transfer/
COPY 02_indexing/   ./02_indexing/
COPY 03_preprocessing/ ./03_preprocessing/
COPY 04_upload/     ./04_upload/

# Copy orchestration files
COPY cron-wrapper.sh ./
COPY docker-entrypoint.sh /usr/local/bin/

RUN chmod +x \
    /app/01_transfer/move.sh \
    /app/02_indexing/make_parquet.sh \
    /app/02_indexing/reindex.sh \
    /app/04_upload/copy_to_nird.sh \
    /app/cron-wrapper.sh \
    /usr/local/bin/docker-entrypoint.sh

# Create output directory
RUN mkdir -p /app/output

# Set up cron job
COPY duckdbcron /etc/cron.d/duckdbcron
RUN chmod 0644 /etc/cron.d/duckdbcron

ENTRYPOINT ["docker-entrypoint.sh"]
