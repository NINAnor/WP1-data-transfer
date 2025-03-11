#!/bin/bash

for prefix in "proj_tabmon_NINA_ES" "proj_tabmon_NINA" "proj_tabmon_NINA_NL" "proj_tabmon_NINA_FR"; do
  rclone move gcs_tabmon:tabmon_data/"$prefix" nird:/datalake/NS8129K/proj_tabmon/"$prefix" \
    --transfers=6 \
    --retries=3 \
    --low-level-retries=10 \
    --log-file=rclone_transfer.log \
    --log-level INFO \
    --progress
done
