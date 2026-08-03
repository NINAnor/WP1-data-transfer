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

# Validate that a parquet file is fully readable before it is uploaded or used.
validate_parquet() {
  local f="$1"
  [ -s "$f" ] || { echo "ERROR: $f is empty or missing" >&2; return 1; }
  [ "$(head -c 4 "$f")" = "PAR1" ] || { echo "ERROR: $f missing leading PAR1 magic" >&2; return 1; }
  [ "$(tail -c 4 "$f")" = "PAR1" ] || { echo "ERROR: $f missing trailing PAR1 magic" >&2; return 1; }
  "$DUCKDB" -csv -noheader -c "SELECT COUNT(*) FROM read_parquet('$f');" >/dev/null 2>&1 \
    || { echo "ERROR: $f cannot be read by duckdb" >&2; return 1; }
}

# Atomic upload: write to a temp object, then rename into place so readers never
# observe a partial index.parquet.
upload_atomic() {
  local src="$1"
  rclone copyto "$src" "$REMOTE"index.parquet.new
  rclone moveto "$REMOTE"index.parquet.new "$REMOTE"index.parquet
}

mkdir -p "$TEMP_DIR"
: > "$TEMP_DIR/errors.log"

echo "=== Incremental index ==="

echo "Downloading existing index.parquet from NIRD..."
rclone copy "$REMOTE"index.parquet "$TEMP_DIR/" 2>>"$TEMP_DIR/errors.log" || true

HAS_EXISTING=false
LAST_MODTIME=""
if [ -f "$TEMP_DIR/index.parquet" ]; then
  LAST_MODTIME=$("$DUCKDB" -csv -noheader -c "
    SELECT MAX(ModTime)::VARCHAR FROM '$TEMP_DIR/index.parquet';
  " 2>/dev/null || echo "")
  if [ -n "$LAST_MODTIME" ]; then
    HAS_EXISTING=true
    echo "Last indexed: $LAST_MODTIME"
  fi
fi

if [ "$HAS_EXISTING" = false ]; then
  echo "No existing valid index found. Will perform full build."
fi

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
  echo "ERROR: No recorders found."
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

if [ "$HAS_EXISTING" = true ]; then
  echo "Checking for new files since last index..."
  NEW_COUNT=$("$DUCKDB" -csv -noheader -c "
    SELECT COUNT(*) FROM read_json('$TEMP_DIR/index_*.json')
    WHERE ModTime > '$LAST_MODTIME' AND IsDir = false;
  " 2>/dev/null || echo "0")
  echo "New files: $NEW_COUNT"

  if [ "$NEW_COUNT" -eq 0 ]; then
    echo "No new files. Skipping rebuild."
    if [ -f "$TEMP_DIR/index.parquet" ]; then
      cp "$TEMP_DIR/index.parquet" "$LOCAL_INDEX"
    fi
    rm -rf "$TEMP_DIR"
    exit 0
  fi
fi

echo "Building new index.parquet..."
BUILD_OK=false
if [ "$HAS_EXISTING" = true ]; then
  if "$DUCKDB" -c "
    COPY (
      SELECT Path, Name, Size, MimeType, ModTime, IsDir, Tier,
        split_part(Path, '/', 1) AS country,
        split_part(Path, '/', 2) AS device
      FROM '$TEMP_DIR/index.parquet'
      UNION ALL
      SELECT Path, Name, Size, MimeType, ModTime, IsDir, Tier,
        split_part(Path, '/', 1) AS country,
        split_part(Path, '/', 2) AS device
      FROM read_json('$TEMP_DIR/index_*.json')
      WHERE ModTime > '$LAST_MODTIME' AND IsDir = false
    ) TO '$TEMP_DIR/index_new.parquet' (FORMAT 'parquet');
  " 2>>"$TEMP_DIR/errors.log"; then
    BUILD_OK=true
  else
    echo "Incremental build failed; falling back to full rebuild."
  fi
fi

if [ "$BUILD_OK" = false ]; then
  "$DUCKDB" -c "
    COPY (
      SELECT Path, Name, Size, MimeType, ModTime, IsDir, Tier,
        split_part(Path, '/', 1) AS country,
        split_part(Path, '/', 2) AS device
      FROM read_json('$TEMP_DIR/index_*.json')
      WHERE IsDir = false
    ) TO '$TEMP_DIR/index_new.parquet' (FORMAT 'parquet');
  "
fi

if ! validate_parquet "$TEMP_DIR/index_new.parquet"; then
  echo "ERROR: built index failed validation; NOT uploading. Remote index.parquet left unchanged."
  rm -rf "$TEMP_DIR"
  exit 1
fi

cp "$TEMP_DIR/index_new.parquet" "$LOCAL_INDEX"
echo "Validated index.parquet (duckdb row count check passed)."

echo "Uploading index.parquet to NIRD (atomic)..."
upload_atomic "$TEMP_DIR/index_new.parquet"

echo "Cleaning up..."
rm -rf "$TEMP_DIR"

echo "=== Incremental index complete ==="
