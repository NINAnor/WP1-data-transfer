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
COPY move.sh make_parquet.sh ./
COPY --chmod=0600 duckdbcron /var/spool/cron/crontabs/root

CMD ["cron", "-f", "-l", "-L", "8"]
