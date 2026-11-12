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
and citizen validations (species with at least 20 checks). Validated clips were
sampled evenly across confidence bins, so the share of correct clips above a
threshold is not the precision of the real detections. Instead:

1. fit P(correct | score) = sigmoid(a + b · logit(score)) on the validations;
2. in each 0.05-wide score bin, pull that curve towards the observed share of
   correct clips, weighted n / (n + 20);
3. weight it by the scores of all BirdNET predictions of the species to get the
   expected precision of everything kept above each threshold;
4. take the lowest threshold reaching 90% (`calibrated`); otherwise the lowest one
   within 2 points of the best precision, if that is at least 70%
   (`calibrated_low`); otherwise the species is `unreliable`.

At least 10 checked clips must lie above the threshold. If the fit fails (no
increase of correctness with score), the plain share of correct checks is used.
The curves are written to `calibration.parquet` for the dashboard's chart.

## Species names

`birdnet_multilingual.csv` has one column per language (ISO 639-1 code; `en_uk`
is stored as `en`). Add a column to add a language: it ends up in
`species_names.parquet` and in the dashboard's language menu without code changes.
