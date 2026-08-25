#!/bin/bash
# Step 1: Move audio recordings from Google Cloud Storage to NIRD S3.
set -euo pipefail

REMOTE_SRC="gcs_tabmon:tabmon_data"
REMOTE_DST="nirds3:bencretois-ns8129k-proj-tabmon"

PREFIXES=(
  "proj_tabmon_NINA_ES"
  "proj_tabmon_NINA"
  "proj_tabmon_NINA_NL"
  "proj_tabmon_NINA_FR"
)

for prefix in "${PREFIXES[@]}"; do
  echo "Transferring ${prefix}..."
  rclone move "${REMOTE_SRC}/${prefix}" "${REMOTE_DST}/${prefix}" \
    --transfers=6 \
    --retries=3 \
    --low-level-retries=10 \
    --log-level INFO
done
