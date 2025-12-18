#!/bin/bash

# Script to handle the container setup

set -e

# Create log files if they don't exist
touch /var/log/move.log /var/log/indexing.log
chmod 644 /var/log/move.log /var/log/indexing.log

# Write environment variables to a file that cron can source
cat > /app/.env-cron << EOF
export RCLONE_CONFIG="/run/secrets/tabmon_rclone"
export GOOGLE_APPLICATION_CREDENTIALS="/run/secrets/gcs_key"
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

# Create initial log entries
echo "$(date): Container started, waiting for first cron execution at Midnight..." >> /var/log/preprocess.log

# Tail logs to keep container running and show output
tail -f /var/log/move.log /var/log/indexing.log