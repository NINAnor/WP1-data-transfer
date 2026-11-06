#!/bin/bash
set -e

# Read the nirds3 S3 settings from rclone unless supplied as environment vars.
source /app/05_dashboard/s3_env.sh

# DuckDB scratch data can be large; keep it in the mounted output area.
export DUCKDB_TEMP_DIR=/app/output/duckdb-tmp
mkdir -p "$DUCKDB_TEMP_DIR"

# Create log file if it doesn't exist
touch /var/log/pipeline.log
chmod 644 /var/log/pipeline.log

# Write environment variables to a file that cron can source
umask 077
env_vars=(
  RCLONE_CONFIG GOOGLE_APPLICATION_CREDENTIALS PARQUET_FILE_URL USERNAME
  PASSWORD SITE_CSV_URL S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY S3_ENDPOINT
  S3_BUCKET S3_REGION TABLES_PREFIX DUCKDB_TEMP_DIR
)
{
  for name in "${env_vars[@]}"; do
    if [[ -v $name ]]; then
      value=${!name}
    else
      value=
    fi
    printf 'export %s=%q\n' "$name" "$value"
  done
} > /app/.env-cron
chmod 600 /app/.env-cron

# Apply the cron job from the cron.d file
echo "Installing cron job..."
crontab /etc/cron.d/duckdbcron

# Start cron service
service cron start

# Print cron status for debugging
echo "Cron service status:"
service cron status

# Show installed cron jobs
echo "Installed cron jobs:"
crontab -l || echo "No cron jobs installed"

# Show our cron file content
echo "Our cron configuration:"
cat /etc/cron.d/duckdbcron

# Keep the container running and show logs
echo "Starting cron daemon and tailing logs..."
echo "Pipeline logs will appear in /var/log/pipeline.log"

# Create initial log entry
echo "$(date): Container started, waiting for first cron execution at 06:00..." >> /var/log/pipeline.log

# Tail logs to keep container running and show output
tail -f /var/log/pipeline.log
