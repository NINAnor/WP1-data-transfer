#!/bin/bash
# Step 2a: Incrementally update index.parquet from NIRD S3.
# Lists each recorder directory (8 in parallel) so listings stay bounded and fast,
# merges new files with the existing index, validates, and uploads atomically.
# Run reindex.sh for a full rebuild from scratch.
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

# --- Download existing index ---
echo "Downloading existing index.parquet..."
rclone copy "$REMOTE/index.parquet" "$TEMP_DIR/" || true

LAST_MODTIME=""
if [ -f "$TEMP_DIR/index.parquet" ]; then
  DUCKDB_ERR="$TEMP_DIR/duckdb_maxmodtime.err"
  LAST_MODTIME=$("$DUCKDB" -csv -noheader -c \
    "SELECT MAX(ModTime)::VARCHAR FROM '$TEMP_DIR/index.parquet';" 2>"$DUCKDB_ERR")
  DUCKDB_STATUS=$?
  if [ "$DUCKDB_STATUS" -ne 0 ]; then
    echo "ERROR: Failed to read MAX(ModTime) from existing index — falling back to full rebuild." >&2
    cat "$DUCKDB_ERR" >&2
    LAST_MODTIME=""
  fi

  # Sanity-check the baseline: reject anything not parseable or beyond now().
  # A corrupt/poisoned ModTime (e.g. a bogus future timestamp) would otherwise
  # make every future run believe there are "0 new files" forever, silently
  # skipping the upload while still exiting 0.
  if [ -n "$LAST_MODTIME" ]; then
    NOW_CHECK=$("$DUCKDB" -csv -noheader -c \
      "SELECT CASE WHEN TRY_CAST('$LAST_MODTIME' AS TIMESTAMP) IS NULL THEN 'invalid'
                    WHEN TRY_CAST('$LAST_MODTIME' AS TIMESTAMP) > NOW() THEN 'future'
                    ELSE 'ok' END;" 2>/dev/null || echo "invalid")
    if [ "$NOW_CHECK" != "ok" ]; then
      echo "ERROR: Existing index has a suspicious LAST_MODTIME='$LAST_MODTIME' ($NOW_CHECK) — falling back to full rebuild." >&2
      LAST_MODTIME=""
    fi
  fi
fi

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

# Paths are now fully qualified, so country/device come from the first two segments.
# --- Incremental or full build ---
NEW_INDEX="$TEMP_DIR/index_new.parquet"
BUILD_OK=false

if [ -n "$LAST_MODTIME" ]; then
  echo "Last indexed: $LAST_MODTIME"
  COUNT_ERR="$TEMP_DIR/duckdb_newcount.err"
  NEW_COUNT=$("$DUCKDB" -csv -noheader -c \
    "SELECT COUNT(*) FROM read_json('$TEMP_DIR/index_*.json') WHERE IsDir = false AND ModTime > '$LAST_MODTIME';" \
    2>"$COUNT_ERR")
  COUNT_STATUS=$?
  if [ "$COUNT_STATUS" -ne 0 ]; then
    echo "ERROR: Failed to count new files against LAST_MODTIME — falling back to full rebuild." >&2
    cat "$COUNT_ERR" >&2
    LAST_MODTIME=""
    NEW_COUNT=""
  fi

  if [ -n "$NEW_COUNT" ]; then
    echo "New files since last index: $NEW_COUNT"

    if [ "$NEW_COUNT" -eq 0 ]; then
      echo "Nothing to do. Copying existing index to $LOCAL_INDEX."
      cp "$TEMP_DIR/index.parquet" "$LOCAL_INDEX"
      exit 0
    fi
  fi
fi

if [ -n "$LAST_MODTIME" ]; then
  echo "Building incremental index..."
  BUILD_ERR="$TEMP_DIR/duckdb_build.err"
  if "$DUCKDB" -c "COPY (
      SELECT Path, Name, Size, MimeType, ModTime, IsDir, Tier, country, device
      FROM '$TEMP_DIR/index.parquet'
      UNION ALL
      SELECT Path, Name, Size, MimeType, ModTime, IsDir, Tier,
             split_part(Path, '/', 1) AS country,
             split_part(Path, '/', 2) AS device
      FROM read_json('$TEMP_DIR/index_*.json')
      WHERE IsDir = false AND ModTime > '$LAST_MODTIME'
    ) TO '$NEW_INDEX' (FORMAT parquet);" 2>"$BUILD_ERR"; then
    BUILD_OK=true
  else
    echo "Incremental build failed — falling back to full rebuild." >&2
    cat "$BUILD_ERR" >&2
  fi
fi

if [ "$BUILD_OK" = false ]; then
  echo "Building full index..."
  "$DUCKDB" -c "COPY (
      SELECT Path, Name, Size, MimeType, ModTime, IsDir, Tier,
             split_part(Path, '/', 1) AS country,
             split_part(Path, '/', 2) AS device
      FROM read_json('$TEMP_DIR/index_*.json')
      WHERE IsDir = false
    ) TO '$NEW_INDEX' (FORMAT parquet);"
fi

# --- Validate, copy locally, upload ---
validate_parquet "$NEW_INDEX"
cp "$NEW_INDEX" "$LOCAL_INDEX"
echo "Index written to $LOCAL_INDEX"

echo "Uploading to NIRD..."
upload_atomic "$NEW_INDEX"
echo "Done."
