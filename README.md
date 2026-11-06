# TABMON data pipeline

[TABMON](https://www.nina.no/english/TABMON) uses [BUGGs acoustic devices](https://www.bugg.xyz/), which send recordings to Google Cloud. This repository runs the single daily pipeline that transfers and indexes those recordings, keeps the legacy CSVs used by the API, and prepares the dashboard's parquet tables.

Everything runs inside one Docker container, scheduled at 06:00 UTC. Steps run in order and the pipeline stops if a step fails.

## Pipeline overview

```
Docker Compose (environment + secrets)
    ↓
Container startup (docker-entrypoint.sh → writes .env-cron)
    ↓
Cron (duckdbcron → 06:00 UTC daily)
    ↓
cron-wrapper.sh
    ├── 01_transfer/move.sh             GCS → NIRD S3 transfer
    ├── 02_indexing/make_parquet.sh     incremental index build + validation + upload
    ├── 03_preprocessing/preprocess_all.py  legacy API CSVs
    ├── 04_upload/copy_to_nird.sh       uploads legacy CSVs
    └── 05_dashboard/build_tables.py    builds dashboard parquet tables
```

## Pipeline steps

| Step | File | Purpose |
|------|------|---------|
| 01 | `01_transfer/move.sh` | rclone move of recordings from Google Cloud to NIRD S3 |
| 02 | `02_indexing/make_parquet.sh` | Incrementally rebuilds `index.parquet`, validates the result, and uploads atomically |
| 03 | `03_preprocessing/preprocess_all.py` | Produces the legacy CSVs still consumed by the dashboard API |
| 04 | `04_upload/copy_to_nird.sh` | Uploads those CSVs to NIRD S3 |
| 05 | `05_dashboard/build_tables.py` | Builds the dashboard's parquet data contract |

Supporting files include `03_preprocessing/preprocess_func/` (legacy CSV logic), `05_dashboard/reference/` (species ecology reference data), `cron-wrapper.sh` (step orchestration), and `docker-entrypoint.sh` (cron environment setup).

## Outputs

The legacy CSVs below are uploaded to `nirds3:bencretois-ns8129k-proj-tabmon/data/preprocessed/` and are still used by the dashboard API:

- `all_device_stats.csv` — per-device statistics
- `dataset_stats.csv` — overall dataset statistics
- `device_status.csv` — device online/offline status
- `recording_matrix.csv` — recording availability matrix

Step 05 writes dashboard parquet tables under
`s3://bencretois-ns8129k-proj-tabmon/data/preprocessed/dashboard/`, including
`deployments.parquet`, `effort.parquet`, `phenology.parquet`, `species.parquet`,
`clips.parquet`, `photos.parquet`, `validations.parquet`, `species_info.parquet`,
and `species_ecology.parquet`. The dashboard API cuts requested audio clips on demand
directly from source recordings in S3; this pipeline does not pre-generate MP3 files.

These outputs are consumed by the separate
[TABMON dashboard repository](https://github.com/NINAnor/tabmon_dashboard); see
its [data contract](https://github.com/NINAnor/tabmon_dashboard/blob/main/docs/data-contract.md).
Site photos are indexed in `photos.parquet`; the obsolete `image_mapping.csv`
is no longer generated or uploaded.

## Setup

### Environment variables

Provide these in the local `.env` file or Docker environment:

```
PARQUET_FILE_URL=/app/index.parquet
USERNAME=your_username
PASSWORD=your_password
SITE_CSV_URL=https://your-domain.com/data/site_info.csv
```

`PARQUET_FILE_URL` points to the local `index.parquet` produced by step 02. The legacy preprocessing downloader also accepts an HTTP(S) URL. `SITE_CSV_URL` is used by the device-status and recording-matrix CSVs.

The entrypoint reads `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, and `S3_ENDPOINT` from the `[nirds3]` section of the rclone config at `$RCLONE_CONFIG` unless those variables are already supplied. The resulting cron environment file is private to the container; credential values are not written to logs. `DUCKDB_TEMP_DIR` is set to `/app/output/duckdb-tmp`.

### Secrets

- `tabmon_rclone` → rclone configuration (defines the `nirds3` remote and S3 credentials)
- `gcs_key` → Google Cloud service-account key (`key-file.json`)

Local credential/config files and generated data are ignored by Git and excluded from the Docker build context.

## Running

```bash
docker compose up -d --build
```

The dashboard tables job also calls Wikipedia/Wikidata, so the container needs outbound internet access. DuckDB's `httpfs` and `icu` extensions are included in the image.

## Manual runs / testing

Run a full index rebuild and upload (remediation), inside the image:

```bash
docker run --rm \
  -e RCLONE_CONFIG=/tmp/rclone.conf \
  -v ~/.config/rclone/rclone.conf:/tmp/rclone.conf:ro \
  data_transfer-data-transfer \
  bash -lc 'cd /app && ./02_indexing/reindex.sh'
```

Run only the legacy preprocessing against the current local index:

```bash
docker compose exec data-transfer bash -lc 'cd /app/03_preprocessing && uv run --project /app python preprocess_all.py'
```

## Monitoring

```bash
docker logs -f data_transfer-data-transfer-1
# or, inside the container:
tail -f /var/log/pipeline.log
```

## Troubleshooting

- **`duckdb: command not found`** — cron's `PATH` must include `/usr/local/bin`; `duckdbcron` already sets it.
- **`No magic bytes found at end of file`** — the remote `index.parquet` is corrupt/truncated. Run `02_indexing/reindex.sh` to rebuild it.
- **Preprocessing reads a stale file** — the index build leaves the validated file at `/app/index.parquet`; even the “no new files” path refreshes this copy.
- **Dashboard jobs cannot access S3** — check the `[nirds3]` section in the rclone config and ensure its `access_key_id`, `secret_access_key`, and `endpoint` entries are present (or provide the corresponding `S3_*` environment variables).
