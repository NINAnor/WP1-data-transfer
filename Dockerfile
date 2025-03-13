FROM alpine:3.21

RUN apk add --no-cache rclone
RUN curl https://install.duckdb.org | sh

WORKDIR /app
COPY move.sh make_parquet.sh ./
COPY --chmod=0644 duckdbcron /etc/cron.d/duckdbcron

CMD ["crond", "-f"]