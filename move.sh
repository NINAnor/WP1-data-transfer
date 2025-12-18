#!/bin/sh

for prefix in "proj_tabmon_NINA_ES" "proj_tabmon_NINA" "proj_tabmon_NINA_NL" "proj_tabmon_NINA_FR"; do
  rclone move gcs_tabmon:tabmon_data/"$prefix" nirds3:bencretois-ns8129k-proj-tabmon/"$prefix" \
    --transfers=6 \
    --retries=3 \
    --low-level-retries=10 \
    --log-level INFO \
    --progress
done
