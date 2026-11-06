# Dashboard data jobs

This job prepares the parquet datasets consumed by the TABMON dashboard repo
([data contract](https://github.com/NINAnor/tabmon_dashboard/blob/main/docs/data-contract.md)):

`build_tables.py` reads the indexed recordings and related data from NIRD S3,
builds the dashboard parquet tables, and uploads them under
`data/preprocessed/dashboard/`. It also fetches species information from
Wikipedia/Wikidata, so it needs internet access. The dashboard API cuts requested
audio clips on demand directly from source recordings in S3; this job does not
pre-generate MP3 clip files.

The job uses S3 credentials from the `nirds3` section of the rclone config
(unless the corresponding `S3_*` environment variables are already set). The
required reference datasets and multilingual species names are included here.
