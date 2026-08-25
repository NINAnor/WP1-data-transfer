#!/bin/bash
set -uo pipefail

LOG="/var/log/pipeline.log"

log() { echo "$(date): $*" | tee -a "$LOG"; }

run_step() {
  local name="$1"; shift
  log "Starting ${name}..."
  "$@" >> "$LOG" 2>&1
  local status=$?
  if [ "$status" -ne 0 ]; then
    log "❌ ${name} FAILED (exit ${status})"
    exit 1
  fi
  log "✅ ${name} completed"
}

# Load environment variables written at container startup
[ -f /app/.env-cron ] || { log "❌ .env-cron not found"; exit 1; }
source /app/.env-cron

mkdir -p /app/output

run_step "01 transfer (GCS → NIRD)"  /app/01_transfer/move.sh
run_step "02 indexing"               /app/02_indexing/make_parquet.sh
run_step "03 preprocessing"          bash -c 'cd /app/03_preprocessing && uv run --project /app python preprocess_all.py'
run_step "04 upload (CSVs → NIRD)"  /app/04_upload/copy_to_nird.sh

log "🎉 Pipeline completed successfully"
