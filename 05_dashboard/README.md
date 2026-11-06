# Dashboard data jobs

This step prepares the parquet datasets consumed by the TABMON dashboard repo
([data contract](https://github.com/NINAnor/tabmon_dashboard/blob/main/docs/data-contract.md)):

1. `build_tables.py` reads the indexed recordings and related data from NIRD S3,
   builds the dashboard parquet tables, and uploads them under
   `data/preprocessed/dashboard/`. It also fetches species information from
   Wikipedia/Wikidata, so it needs internet access.
2. `cut_clips.py` incrementally cuts 5-second MP3 previews for rows in
   `clips.parquet` and uploads them under
   `data/preprocessed/dashboard/clips/v3/`. Existing clips are skipped;
   failures are reported and retried on later runs.

Both jobs use S3 credentials from the `nirds3` section of the rclone config
(unless the corresponding `S3_*` environment variables are already set). The
required reference datasets and multilingual species names are included here.
