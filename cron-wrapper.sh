#!/bin/bash
set -uo pipefail

LOG="/var/log/pipeline.log"

log() {
  echo "$(date): $*" >> "$LOG"
}

run_step() {
  local name="$1"
  shift
  log "Starting ${name}..."
  if ! "$@" >> "$LOG" 2>&1; then
    local rc=$?
    log "❌ ${name} FAILED (exit ${rc})"
    exit "$rc"
  fi
  log "✅ ${name} completed"
}

log "Starting pipeline..."

# Load environment variables created at container startup
if [ -f /app/.env-cron ]; then
  source /app/.env-cron
else
  log "❌ .env-cron file not found!"
  exit 1
fi

cd /app

run_step "data transfer (GCS -> NIRD)" /app/move.sh
run_step "index build" /app/make_parquet.sh
run_step "preprocessing" /usr/local/bin/uv run python preprocess_all.py
run_step "S3 upload" /app/copy_files_to_s3.sh

log "🎉 Full pipeline completed successfully"
