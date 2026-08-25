#!/bin/bash
# Step 4: Upload generated CSVs to NIRD S3.
set -euo pipefail

REMOTE="nirds3:bencretois-ns8129k-proj-tabmon/data/preprocessed"
OUTPUT_DIR="/app/output"

echo "Uploading CSVs to NIRD..."
rclone copy "$OUTPUT_DIR" "$REMOTE" --include "*.csv"
echo "Done."
