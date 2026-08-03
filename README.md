# TABMON data pipeline

[TABMON](https://www.nina.no/english/TABMON) uses [BUGGs acoustic devices](https://www.bugg.xyz/) which by default send data over to Google Cloud.

This repository contains the **single daily pipeline** that:

1. **Transfers** audio data from Google Cloud to NIRD S3 (`move.sh`)
2. **Indexes** all files into an `index.parquet` file (`make_parquet.sh` / `reindex.sh`)
3. **Preprocesses** the index into analysis CSVs (`preprocess_all.py`)
4. **Uploads** the CSVs to S3 for downstream consumption (`copy_files_to_s3.sh`)

Everything runs inside one container, scheduled at 06:00 UTC.

## Pipeline overview

```
Docker Compose (env + secrets)
    ↓
Container startup (docker-entrypoint.sh → writes .env-cron)
    ↓
Cron (duckdbcron → 06:00 UTC daily)
    ↓
cron-wrapper.sh
    ├── move.sh            GCS → NIRD S3 transfer
    ├── make_parquet.sh    incremental index build + validation + atomic upload
    ├── preprocess_all.py  generates the analysis CSVs
    └── copy_files_to_s3.sh uploads the CSVs to NIRD S3
```

If any step fails, the pipeline stops (no downstream step runs).

## Files

| File | Purpose |
|------|---------|
| `move.sh` | rclone move of recording data from Google Cloud to NIRD S3 |
| `make_parquet.sh` | Incremental rebuild of `index.parquet`; validates the result (magic bytes + a duckdb read) **before** uploading, and uploads atomically |
| `reindex.sh` | Full rebuild of `index.parquet` (used for remediation) |
| `preprocess_all.py` | Orchestrates the preprocessing of `index.parquet` into CSVs |
| `preprocess_func/` | Preprocessing logic + shared download helper (`download.py`) |
| `copy_files_to_s3.sh` | Uploads generated CSVs to S3 |
| `cron-wrapper.sh` | Runs the four steps in sequence with logging |
| `docker-entrypoint.sh` | Writes env vars to `.env-cron` and starts cron |
| `duckdbcron` | Cron schedule (06:00 UTC) |

## Output files

- `all_device_stats.csv` — per-device statistics
- `dataset_stats.csv` — overall dataset statistics
- `image_mapping.csv` — image file mappings
- `device_status.csv` — device online/offline status
- `recording_matrix.csv` — recording availability matrix

All generated CSVs are uploaded to `nirds3:bencretois-ns8129k-proj-tabmon/data/preprocessed/`.

## Setup

### Environment variables

Create a `.env` file (already gitignored):

```
PARQUET_FILE_URL=/app/index.parquet
USERNAME=your_username
PASSWORD=your_password
BASE_DIR=http://your-base-url/data
SITE_CSV_URL=https://your-domain.com/data/site_info.csv
```

`PARQUET_FILE_URL` points at the locally built `index.parquet` inside the container (see `make_parquet.sh`). It can also be an `http(s)://` URL; the preprocessing downloader accepts both.

### Secrets

- `tabmon_rclone` → your `rclone.conf` (defines the `nirds3` remote)
- `gcs_key` → the Google Cloud service-account key (`key-file.json`)

Both are referenced in `docker-compose.yaml`.

## Running

```bash
docker compose up -d --build
```

## Manual runs / testing

Run a full rebuild and upload (remediation), inside the image:

```bash
docker run --rm \
  -e RCLONE_CONFIG=/tmp/rclone.conf \
  -v ~/.config/rclone/rclone.conf:/tmp/rclone.conf:ro \
  data_transfer-data-transfer \
  bash -lc 'cd /app && ./reindex.sh'
```

Run only the preprocessing against the current local index:

```bash
docker compose exec data-transfer bash -lc 'cd /app && /usr/local/bin/uv run python preprocess_all.py'
```

## Monitoring

```bash
docker logs -f data_transfer-data-transfer-1
# or, inside the container:
tail -f /var/log/pipeline.log
```

## Troubleshooting

- **`duckdb: command not found`** — the cron PATH must include `/usr/local/bin`. `duckdbcron` already sets `PATH=/usr/local/bin:/usr/bin:/bin`.
- **`No magic bytes found at end of file`** — the remote `index.parquet` is corrupt/truncated. Run `reindex.sh` to rebuild it. The build scripts validate the output before uploading, so this should not recur.
- **Preprocessing reads a stale file** — the index build leaves the validated file at `/app/index.parquet`, which preprocessing reads. On the "no new files" path `make_parquet.sh` still refreshes this copy.
