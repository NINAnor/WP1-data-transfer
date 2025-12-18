FROM debian:12-slim

RUN --mount=target=/var/lib/apt/lists,type=cache,sharing=locked \
    --mount=target=/var/cache/apt,type=cache,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update \
    && apt-get -y --no-install-recommends install \
    wget ca-certificates rclone cron

RUN wget -q https://github.com/duckdb/duckdb/releases/download/v1.2.1/duckdb_cli-linux-amd64.gz -O- | \
    gzip -d > /usr/local/bin/duckdb && \
    chmod +x /usr/local/bin/duckdb

WORKDIR /app
COPY move.sh make_parquet.sh cron-wrapper.sh ./

# Copy and set up cron job
COPY duckdbcron /etc/cron.d/duckdbcron
RUN chmod 0644 /etc/cron.d/duckdbcron

# Setup the entrypoint
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

CMD ["docker-entrypoint.sh"]
