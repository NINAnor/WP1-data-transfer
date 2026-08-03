#!/bin/bash
set -e

# Create log file if it doesn't exist
touch /var/log/pipeline.log
chmod 644 /var/log/pipeline.log

# Write environment variables to a file that cron can source
cat > /app/.env-cron << EOF
export RCLONE_CONFIG="${RCLONE_CONFIG}"
export GOOGLE_APPLICATION_CREDENTIALS="${GOOGLE_APPLICATION_CREDENTIALS}"
export PARQUET_FILE_URL="${PARQUET_FILE_URL}"
export USERNAME="${USERNAME}"
export PASSWORD="${PASSWORD}"
export BASE_DIR="${BASE_DIR}"
export SITE_CSV_URL="${SITE_CSV_URL}"
EOF

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
