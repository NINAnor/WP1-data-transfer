#!/bin/bash
set -euo pipefail

REMOTE="nirds3:bencretois-ns8129k-proj-tabmon/"
WORKERS=8
TEMP_DIR="./tmp"
DUCKDB="/usr/local/bin/duckdb"
LOCAL_INDEX="/app/index.parquet"

PREFIXES=(
  "proj_tabmon_NINA_ES"
  "proj_tabmon_NINA"
  "proj_tabmon_NINA_NL"
  "proj_tabmon_NINA_FR"
)

validate_parquet() {
  local f="$1"
  [ -s "$f" ] || { echo "ERROR: $f is empty or missing" >&2; return 1; }
  [ "$(head -c 4 "$f")" = "PAR1" ] || { echo "ERROR: $f missing leading PAR1 magic" >&2; return 1; }
  [ "$(tail -c 4 "$f")" = "PAR1" ] || { echo "ERROR: $f missing trailing PAR1 magic" >&2; return 1; }
  "$DUCKDB" -csv -noheader -c "SELECT COUNT(*) FROM read_parquet('$f');" >/dev/null 2>&1 \
    || { echo "ERROR: $f cannot be read by duckdb" >&2; return 1; }
}

upload_atomic() {
  local src="$1"
  rclone copyto "$src" "$REMOTE"index.parquet.new
  rclone moveto "$REMOTE"index.parquet.new "$REMOTE"index.parquet
}

mkdir -p "$TEMP_DIR"
: > "$TEMP_DIR/errors.log"

echo "=== Reindex: full parallel rebuild ==="

echo "Prefixes: ${PREFIXES[*]}"

echo "Discovering recorders..."
RECORDER_PATHS=()
for prefix in "${PREFIXES[@]}"; do
  while IFS= read -r dir; do
    [ -n "$dir" ] && RECORDER_PATHS+=("${prefix}/${dir%/}")
  done < <(rclone lsf --dirs-only "$REMOTE$prefix" 2>>"$TEMP_DIR/errors.log" || true)
done
echo "Found ${#RECORDER_PATHS[@]} recorders"

if [ ${#RECORDER_PATHS[@]} -eq 0 ]; then
  echo "ERROR: No recorders found. Cannot build index."
  cat "$TEMP_DIR/errors.log"
  rm -rf "$TEMP_DIR"
  exit 1
fi

echo "Listing files in parallel ($WORKERS workers)..."

export REMOTE TEMP_DIR WORKERS
printf "%s\n" "${RECORDER_PATHS[@]}" | xargs -P "$WORKERS" -I {} sh -c '
  clean=$(echo "{}" | tr "/" "_")
  rclone lsjson --recursive --fast-list "${REMOTE}{}" > "$TEMP_DIR/index_${clean}.json" \
    2>>"$TEMP_DIR/errors.log" || echo "WARN: Failed to list {}" >> "$TEMP_DIR/errors.log"
'

JSON_COUNT=$(ls "$TEMP_DIR"/index_*.json 2>/dev/null | wc -l)
if [ "$JSON_COUNT" -eq 0 ]; then
  echo "ERROR: No file listings generated."
  cat "$TEMP_DIR/errors.log"
  rm -rf "$TEMP_DIR"
  exit 1
fi
echo "Generated $JSON_COUNT listing files"

echo "Merging into parquet..."
"$DUCKDB" -c "
  COPY (
    SELECT
      Path, Name, Size, MimeType, ModTime, IsDir, Tier,
      split_part(Path, '/', 1) AS country,
      split_part(Path, '/', 2) AS device
    FROM read_json('$TEMP_DIR/index_*.json')
    WHERE IsDir = false
  ) TO '$TEMP_DIR/index.parquet' (FORMAT 'parquet');
"

if ! validate_parquet "$TEMP_DIR/index.parquet"; then
  echo "ERROR: built index failed validation; NOT uploading. Remote index.parquet left unchanged."
  rm -rf "$TEMP_DIR"
  exit 1
fi

cp "$TEMP_DIR/index.parquet" "$LOCAL_INDEX"
echo "Validated index.parquet (duckdb row count check passed)."

echo "Uploading index.parquet to NIRD (atomic)..."
upload_atomic "$TEMP_DIR/index.parquet"

echo "Cleaning up..."
rm -rf "$TEMP_DIR"

echo "=== Reindex complete ==="
