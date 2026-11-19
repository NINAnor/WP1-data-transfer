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

# Like run_step, but a failure does not stop the pipeline: later, independent
# steps still run, and the run is reported as failed at the end.
FAILED_OPTIONAL=()
run_optional() {
  local name="$1"; shift
  log "Starting ${name}..."
  "$@" >> "$LOG" 2>&1
  local status=$?
  if [ "$status" -ne 0 ]; then
    log "❌ ${name} FAILED (exit ${status}), continuing"
    FAILED_OPTIONAL+=("$name")
    return 0
  fi
  log "✅ ${name} completed"
}

# Load environment variables written at container startup
[ -f /app/.env-cron ] || { log "❌ .env-cron not found"; exit 1; }
source /app/.env-cron

mkdir -p /app/output

run_step "01 transfer (GCS → NIRD)"  /app/01_transfer/move.sh
run_step "02 indexing"               /app/02_indexing/make_parquet.sh
# Team-area CSVs (device status, activity matrix). The site info is read from the
# bucket: the old public https://tabmon.nina.no/data/ route no longer exists.
run_optional "03+04 team-area CSVs"  bash -c '
  rclone copyto nirds3:bencretois-ns8129k-proj-tabmon/site_info.csv /app/site_info.csv &&
  cd /app/03_preprocessing &&
  SITE_CSV_URL=/app/site_info.csv uv run --project /app python preprocess_all.py &&
  /app/04_upload/copy_to_nird.sh'
run_step "05 dashboard tables"       bash -c 'cd /app/05_dashboard && uv run --project /app python build_tables.py --source s3://bencretois-ns8129k-proj-tabmon --out s3://bencretois-ns8129k-proj-tabmon/data/preprocessed/dashboard --names birdnet_multilingual.csv'

if [ ${#FAILED_OPTIONAL[@]} -gt 0 ]; then
  log "⚠️ Pipeline finished, but failed: ${FAILED_OPTIONAL[*]}"
  exit 1
fi
log "🎉 Pipeline completed successfully"
