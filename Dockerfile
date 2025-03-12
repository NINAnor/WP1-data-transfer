FROM alpine:3.15

RUN apk add --no-cache bash rclone duckdb

WORKDIR /app
COPY move.sh make_parquet.sh ./
RUN chmod +x move.sh make_parquet.sh

RUN echo "0 0 * * * /app/move.sh && /app/make_parquet.sh >> /var/log/cron.log 2>&1" > /etc/crontabs/root

# creates the log file so that cron can write to it
RUN touch /var/log/cron.log

# run the cron job. We set:
# -f: Runs cron in the foreground, -l 8: Sets the log level to 8, which is a high verbosity
CMD ["crond", "-f", "-l", "8"]
