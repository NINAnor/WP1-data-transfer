#!/bin/bash
# Step 2b: Full rebuild of index.parquet from scratch.
# Use this for remediation when the incremental index is stale or corrupt.
set -euo pipefail

REMOTE="nirds3:bencretois-ns8129k-proj-tabmon"
PREFIXES=("proj_tabmon_NINA_ES" "proj_tabmon_NINA" "proj_tabmon_NINA_NL" "proj_tabmon_NINA_FR")
WORKERS=8
DUCKDB="/usr/local/bin/duckdb"
LOCAL_INDEX="/app/index.parquet"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT

validate_parquet() {
  local f="$1"
  [ -s "$f" ]                                   || { echo "ERROR: $f is empty" >&2; return 1; }
  [ "$(head -c4 "$f")" = "PAR1" ]               || { echo "ERROR: $f missing PAR1 header" >&2; return 1; }
  [ "$(tail -c4 "$f")" = "PAR1" ]               || { echo "ERROR: $f missing PAR1 footer" >&2; return 1; }
  "$DUCKDB" -csv -noheader -c "SELECT COUNT(*) FROM read_parquet('$f');" >/dev/null 2>&1 \
                                                 || { echo "ERROR: $f unreadable by duckdb" >&2; return 1; }
}

upload_atomic() {
  rclone copyto "$1" "$REMOTE/index.parquet.new"
  rclone moveto "$REMOTE/index.parquet.new" "$REMOTE/index.parquet"
}

# --- Discover recorder directories per prefix ---
echo "Discovering recorders..."
RECORDER_PATHS=()
for prefix in "${PREFIXES[@]}"; do
  while IFS= read -r dir; do
    [ -n "$dir" ] && RECORDER_PATHS+=("${prefix}/${dir%/}")
  done < <(rclone lsf --dirs-only "$REMOTE/$prefix" 2>/dev/null || true)
done
echo "Found ${#RECORDER_PATHS[@]} recorders"

# --- List files per recorder in parallel ---
# Each JSON path is relative to the recorder dir; prepend the recorder path so the
# final Path is fully qualified (prefix/recorder/conf/file.mp3).
echo "Listing files in parallel ($WORKERS workers)..."
: > "$TEMP_DIR/errors.log"
export REMOTE TEMP_DIR
printf "%s\n" "${RECORDER_PATHS[@]}" | xargs -P "$WORKERS" -I {} sh -c '
  clean=$(echo "{}" | tr "/" "_")
  rclone lsjson --recursive --fast-list "${REMOTE}/{}" | \
    sed "s|\"Path\":\"|\"Path\":\"{}/|g" \
    > "$TEMP_DIR/index_${clean}.json" \
    2>>"$TEMP_DIR/errors.log" || echo "WARN: Failed to list {}" >> "$TEMP_DIR/errors.log"
'
echo "Listings complete"

JSON_COUNT=$(ls "$TEMP_DIR"/index_*.json 2>/dev/null | wc -l)
if [ "$JSON_COUNT" -eq 0 ]; then
  echo "ERROR: No file listings generated." >&2
  cat "$TEMP_DIR/errors.log" >&2
  exit 1
fi

# --- Build full index ---
# Paths are fully qualified, so country/device come from the first two segments.
echo "Building full index..."
INDEX="$TEMP_DIR/index.parquet"
"$DUCKDB" -c "COPY (
    SELECT Path, Name, Size, MimeType, ModTime, IsDir, Tier,
           split_part(Path, '/', 1) AS country,
           split_part(Path, '/', 2) AS device
    FROM read_json('$TEMP_DIR/index_*.json')
    WHERE IsDir = false
  ) TO '$INDEX' (FORMAT parquet);"

# --- Validate, copy locally, upload ---
validate_parquet "$INDEX"
cp "$INDEX" "$LOCAL_INDEX"
echo "Index written to $LOCAL_INDEX"

echo "Uploading to NIRD..."
upload_atomic "$INDEX"
echo "Done."
