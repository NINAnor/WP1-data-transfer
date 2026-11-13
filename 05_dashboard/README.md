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

## Confidence thresholds

`calibration.py` picks a BirdNET confidence threshold per species from the expert
and citizen validations, following the BirdNET guidelines (Wood & Kahl 2024):

1. model P(correct | score) = u · sigmoid(a + b · logit(score)) on the checks
   scoring 0.10 or more (BirdNET reports no detections below). u = 1 is plain
   logistic regression; a ceiling u < 1 is kept when it predicts held-out checks
   better (5-fold cross-validation), for species that never get close to 100%
   correct;
2. the threshold is the lowest score where the curve reaches 90% (`calibrated`),
   otherwise 70% (`calibrated_low`), otherwise the species is `unreliable`. At
   least 10 checks must lie at or above the threshold;
3. 200 bootstrap refits give a 95% band for the curve and a range for the
   threshold.

Species need 20 checks scoring 0.10 or more; a species checked 20+ times but
mostly below 0.10 stays `unreliable`. Validated clips were sampled evenly across
confidence bins, which does not bias P(correct | score). Curves, observed
accuracy per 0.05 bin (with Wilson 95% intervals) and the number of detections
above each threshold are written to `calibration.parquet` for the dashboard.

## Species names

`birdnet_multilingual.csv` has one column per language (ISO 639-1 code; `en_uk`
is stored as `en`). Add a column to add a language: it ends up in
`species_names.parquet` and in the dashboard's language menu without code changes.
