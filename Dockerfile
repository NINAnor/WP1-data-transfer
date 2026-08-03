FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Install system dependencies
RUN apt-get update \
    && apt-get -y --no-install-recommends install \
    wget ca-certificates rclone cron \
    && rm -rf /var/lib/apt/lists/*

# Install the DuckDB CLI (used by make_parquet.sh / reindex.sh)
RUN wget -q https://github.com/duckdb/duckdb/releases/download/v1.2.1/duckdb_cli-linux-amd64.gz -O- | \
    gzip -d > /usr/local/bin/duckdb && \
    chmod +x /usr/local/bin/duckdb

WORKDIR /app

# Copy Python project files
COPY pyproject.toml uv.lock ./
COPY preprocess_func/ ./preprocess_func/

# Install Python dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

# Copy shell scripts
COPY move.sh make_parquet.sh reindex.sh copy_files_to_s3.sh cron-wrapper.sh preprocess_all.py ./

# Make the shell scripts executable
RUN chmod +x /app/move.sh /app/make_parquet.sh /app/reindex.sh /app/copy_files_to_s3.sh /app/cron-wrapper.sh /app/preprocess_all.py

# Copy and set up cron job
COPY duckdbcron /etc/cron.d/duckdbcron
RUN chmod 0644 /etc/cron.d/duckdbcron

# Create a startup script
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["docker-entrypoint.sh"]
