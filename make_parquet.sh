#!/bin/sh
set -e

REMOTE="nirds3:bencretois-ns8129k-proj-tabmon/"
DEST="$REMOTE/index.parquet"

# first list the files of the remove aas index.json
rclone lsjson $REMOTE --recursive > index.json

# the create the parquet file and send it to the S3 bucket
duckdb -c "COPY (
  SELECT
    Path,
    Name,
    Size,
    MimeType,
    ModTime,
    IsDir,
    Tier,
    split_part(Path, '/', 1) AS country,
    split_part(Path, '/', 2) AS device
  FROM read_json('index.json')
  WHERE IsDir = false
) TO STDOUT (FORMAT 'parquet');" | rclone rcat $DEST
