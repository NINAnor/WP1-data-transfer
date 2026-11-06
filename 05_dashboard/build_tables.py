"""
Build the aggregated tables used by the TABMON dashboard API.

Reads from the TABMON bucket (S3 or a local mirror) and writes small parquet
files that the API rolls up on request:

  deployments.parquet  deployment -> site / cluster / country / coordinates
  effort.parquet       site x date x hour -> recorded_minutes
  phenology.parquet    species x site x date x hour -> n_detections
  species.parquet      species names, calibrated thresholds, totals
  clips.parquet        example detections that can be played back
  photos.parquet       deployment photos (team area only)
  validations.parquet  validation progress per country / species

Usage:
  python build_tables.py --source s3://bencretois-ns8129k-proj-tabmon --out ./out
  python build_tables.py --source /mnt/tabmon --out ./out
"""

import argparse
import logging
import os
from pathlib import Path

import duckdb
from species_ecology import build_species_ecology
from species_info import build_species_info

log = logging.getLogger("tabmon.preprocess")

COUNTRY_TZ = {
    "Norway": "Europe/Oslo",
    "Spain": "Europe/Madrid",
    "Netherlands": "Europe/Amsterdam",
    "France": "Europe/Paris",
}
PREDICTIONS_GLOB = "tabmon_predictions/merged_predictions_light/*/*/*.parquet"
EXCLUDED_PROJECTS = ("proj_tabmon_NINA_ES_test",)
FILE_MINUTES = 5.0
DATA_START = "2025-01-01"
DEFAULT_THRESHOLD = 0.7
TARGET_PRECISION = 0.9
# If the target is never reached, accept the best threshold above this precision.
FALLBACK_PRECISION = 0.7
MIN_VALIDATIONS = 20
MIN_ABOVE_THRESHOLD = 10
CLIPS_PER_SPECIES_CLUSTER = 3
CLIP_MIN_CONFIDENCE = 0.8

FILE_TS = r"(\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2})"


def connect(use_s3: bool) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    con.execute("LOAD icu;")
    if use_s3:
        con.execute("LOAD httpfs;")

        def q(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"

        con.execute(
            f"""
            CREATE SECRET tabmon_s3 (
                TYPE s3,
                KEY_ID {q(os.environ["S3_ACCESS_KEY_ID"])},
                SECRET {q(os.environ["S3_SECRET_ACCESS_KEY"])},
                ENDPOINT {q(os.environ.get("S3_ENDPOINT", "s3.nird.sigma2.no"))},
                REGION {q(os.environ.get("S3_REGION", "us-east-1"))},
                URL_STYLE 'path'
            )
            """
        )
    con.execute("SET preserve_insertion_order = false")
    if tmp := os.environ.get("DUCKDB_TEMP_DIR"):
        con.execute(f"SET temp_directory = '{tmp}'")
    return con


def tz_case(col: str) -> str:
    """SQL CASE expression mapping a country column to its IANA timezone."""
    whens = " ".join(f"WHEN '{c}' THEN '{tz}'" for c, tz in COUNTRY_TZ.items())
    return f"CASE {col} {whens} ELSE 'Europe/Paris' END"


def local_ts(utc_expr: str, country_col: str) -> str:
    return f"timezone({tz_case(country_col)}, ({utc_expr})::TIMESTAMPTZ)"


def file_ts(name_col: str) -> str:
    """UTC timestamp parsed from a Bugg file name like 2025-06-06T13_04_30.037Z.mp3."""
    return (
        f"try_strptime(regexp_extract({name_col}, '{FILE_TS}', 1), '%Y-%m-%dT%H_%M_%S')"
    )


def build_deployments(con, src: str, out: str):
    log.info("deployments")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE deployments AS
        WITH raw AS (
            SELECT * FROM read_csv('{src}/site_info.csv', all_varchar = true,
                                   header = true)
        )
        SELECT
            trim(DeploymentID) AS deployment_id,
            lower(trim(DeviceID)) AS device_id,
            trim(Country) AS country,
            trim(Cluster) AS cluster,
            trim(Site) AS site,
            lower(regexp_replace(
                strip_accents(trim(Country) || '-' || trim(Cluster) || '-' || trim(Site)),
                '[^A-Za-z0-9]+', '-', 'g')) AS site_id,
            lower(regexp_replace(
                strip_accents(trim(Country) || '-' || trim(Cluster)),
                '[^A-Za-z0-9]+', '-', 'g')) AS cluster_id,
            TRY_CAST(Latitude AS DOUBLE) AS lat,
            TRY_CAST(Longitude AS DOUBLE) AS lon,
            upper(trim(Active)) = 'TRUE' AS active,
            Habitat AS habitat,
            TRY_CAST(Microphone_height AS DOUBLE) AS mic_height_cm,
            Microphone_direction AS mic_direction,
            TRY_CAST(Coordinates_uncertainty AS DOUBLE) AS coord_uncertainty_m,
            COALESCE(
                try_strptime(deploymentBeginDate || ' ' || COALESCE(deploymentBeginTime, '00:00:00'),
                             '%d/%m/%Y %H:%M:%S'),
                try_strptime(deploymentBeginDate, '%d/%m/%Y')) AS begin_ts,
            COALESCE(
                try_strptime(deploymentEndDate || ' ' || COALESCE(deploymentEndTime, '23:59:59'),
                             '%d/%m/%Y %H:%M:%S'),
                try_strptime(deploymentEndDate, '%d/%m/%Y')) AS end_ts,
            Comments AS comments
        FROM raw
        WHERE DeploymentID IS NOT NULL AND DeviceID IS NOT NULL
        """
    )
    con.execute(f"COPY deployments TO '{out}/deployments.parquet' (FORMAT parquet)")


def build_index(con, src: str):
    """Audio files from the bucket index, attributed to a deployment by device + time."""
    log.info("index -> recordings")
    excluded = ", ".join(f"'{p}'" for p in EXCLUDED_PROJECTS)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE files AS
        SELECT Path AS path, Name AS name, device AS bugg, {file_ts("Name")} AS ts_utc
        FROM read_parquet('{src}/index.parquet')
        WHERE NOT IsDir AND Name LIKE '%.mp3'
          AND country NOT IN ({excluded})
          AND country LIKE 'proj_tabmon_NINA%'
        """
    )
    # Map each Bugg directory to the short device ids used in site_info.
    con.execute(
        """
        CREATE OR REPLACE TABLE bugg_map AS
        SELECT DISTINCT f.bugg, d.device_id
        FROM (SELECT DISTINCT bugg FROM files) f
        JOIN (SELECT DISTINCT device_id FROM deployments) d
          ON ends_with(lower(f.bugg), d.device_id)
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE recordings AS
        SELECT f.path, f.name, f.ts_utc, d.deployment_id, d.device_id, d.site_id,
               d.country, {local_ts("f.ts_utc", "d.country")} AS ts_local
        FROM files f
        JOIN bugg_map b USING (bugg)
        JOIN deployments d
          ON d.device_id = b.device_id
         AND f.ts_utc >= COALESCE(d.begin_ts, TIMESTAMP '2000-01-01')
         AND f.ts_utc <= COALESCE(d.end_ts, TIMESTAMP '2100-01-01')
        WHERE f.ts_utc >= TIMESTAMP '{DATA_START}'
        QUALIFY row_number() OVER (PARTITION BY f.path ORDER BY d.begin_ts DESC) = 1
        """
    )


def build_coverage(con, src: str):
    """Deployment-days that BirdNET has processed: only these count as effort."""
    log.info("coverage")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE analysed_days AS
        SELECT DISTINCT deployment_id,
               {file_ts("filename")}::DATE AS day_utc
        FROM read_parquet('{src}/{PREDICTIONS_GLOB}', hive_partitioning = true,
                          union_by_name = true)
        """
    )


def build_effort(con, out: str):
    log.info("effort")
    con.execute(
        f"""
        COPY (
            SELECT r.site_id, r.ts_local::DATE AS date, hour(r.ts_local)::TINYINT AS hour,
                   (count(*) * {FILE_MINUTES})::FLOAT AS recorded_minutes
            FROM recordings r
            SEMI JOIN analysed_days a
              ON a.deployment_id = r.deployment_id AND a.day_utc = r.ts_utc::DATE
            GROUP BY ALL
            ORDER BY site_id, date, hour
        ) TO '{out}/effort.parquet' (FORMAT parquet)
        """
    )


def build_validations(con, src: str, out: str):
    """Collect (species, confidence, is_correct) pairs from citizen and expert sessions."""
    log.info("validations")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE citizen AS
        SELECT species, TRY_CAST(confidence AS DOUBLE) AS confidence,
               country, filename, TRY_CAST(start_time AS INTEGER) AS start_s,
               CASE lower(validation_response) WHEN 'yes' THEN true
                    WHEN 'no' THEN false END AS correct
        FROM read_csv('{src}/validations/*.csv', union_by_name = true,
                      all_varchar = true, header = true)
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE expert_raw AS
        SELECT country, filename, TRY_CAST(start_time AS INTEGER) AS start_s,
               string_split(birdnet_species_detected, '|') AS sp,
               string_split(birdnet_confidences, '|') AS conf,
               string_split(identified_species, '|') AS truth
        FROM read_csv('{src}/validations_expert/*.csv', union_by_name = true,
                      all_varchar = true, header = true)
        WHERE birdnet_species_detected IS NOT NULL
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE expert AS
        SELECT sp[i] AS species, TRY_CAST(conf[i] AS DOUBLE) AS confidence,
               country, filename, start_s, list_contains(truth, sp[i]) AS correct
        FROM expert_raw, range(1, len(sp) + 1) t(i)
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE validated AS
        SELECT *, 'citizen' AS source FROM citizen
        WHERE correct IS NOT NULL AND confidence BETWEEN 0 AND 1
        UNION ALL
        SELECT *, 'expert' AS source FROM expert
        WHERE correct IS NOT NULL AND confidence BETWEEN 0 AND 1
        """
    )
    con.execute(
        f"""
        COPY (
            SELECT country, species, source, count(*) AS n_validated,
                   count(*) FILTER (WHERE correct) AS n_correct
            FROM validated GROUP BY ALL
        ) TO '{out}/validations.parquet' (FORMAT parquet)
        """
    )


def build_thresholds(con):
    """Per-species threshold calibrated on the citizen and expert validations."""
    log.info("thresholds")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE thresholds AS
        WITH grid AS (SELECT unnest(range(10, 100, 5)) / 100.0 AS t),
        per_t AS (
            SELECT v.species, g.t,
                   count(*) FILTER (WHERE v.confidence >= g.t) AS n_above,
                   avg(v.correct::INT) FILTER (WHERE v.confidence >= g.t) AS precision
            FROM validated v CROSS JOIN grid g
            GROUP BY ALL
        ),
        totals AS (SELECT species, count(*) AS n FROM validated GROUP BY ALL),
        strict AS (
            SELECT species, min(t) AS t FROM per_t
            WHERE precision >= {TARGET_PRECISION} AND n_above >= {MIN_ABOVE_THRESHOLD}
            GROUP BY ALL
        ),
        fallback AS (
            SELECT species, arg_max(t, precision - t / 1000) AS t FROM per_t
            WHERE precision >= {FALLBACK_PRECISION} AND n_above >= {MIN_ABOVE_THRESHOLD}
            GROUP BY ALL
        ),
        best AS (
            SELECT species, COALESCE(s.t, f.t) AS t,
                   CASE WHEN s.t IS NOT NULL THEN 'calibrated'
                        WHEN f.t IS NOT NULL THEN 'calibrated_low' END AS status
            FROM strict s FULL JOIN fallback f USING (species)
        )
        SELECT tot.species, tot.n AS n_validated, b.t AS threshold,
               p.precision AS precision_at_threshold,
               COALESCE(b.status, 'unreliable') AS status
        FROM totals tot
        LEFT JOIN best b USING (species)
        LEFT JOIN per_t p ON p.species = b.species AND p.t = b.t
        WHERE tot.n >= {MIN_VALIDATIONS}
        """
    )


def build_detections(con, src: str, out: str):
    log.info("detections (this is the long step)")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE detections AS
        SELECT p.filename AS name, p.device_id, p.deployment_id,
               p."start time"::INTEGER AS start_s, p.confidence,
               p."scientific name" AS species,
               {file_ts("p.filename")} + to_seconds(p."start time") AS ts_utc
        FROM read_parquet('{src}/{PREDICTIONS_GLOB}',
                          hive_partitioning = true, union_by_name = true) p
        LEFT JOIN thresholds t ON t.species = p."scientific name"
        WHERE p.confidence >= COALESCE(t.threshold, {DEFAULT_THRESHOLD})
          AND COALESCE(t.status, '') <> 'unreliable'
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE detections_sited AS
        SELECT x.*, d.site_id, d.cluster_id, d.country,
               {local_ts("x.ts_utc", "d.country")} AS ts_local
        FROM detections x
        JOIN deployments d USING (deployment_id)
        WHERE x.ts_utc >= TIMESTAMP '{DATA_START}'
        """
    )
    con.execute(
        f"""
        COPY (
            SELECT species, site_id, ts_local::DATE AS date,
                   hour(ts_local)::TINYINT AS hour,
                   count(*)::INTEGER AS n_detections
            FROM detections_sited
            GROUP BY ALL
            ORDER BY species, site_id, date, hour
        ) TO '{out}/phenology.parquet' (FORMAT parquet)
        """
    )


def build_species(con, names_csv: str | None, out: str):
    log.info("species")
    names = (
        f"read_csv('{names_csv}', header = true, all_varchar = true)"
        if names_csv
        else "(SELECT NULL::VARCHAR AS Scientific_Name, NULL::VARCHAR AS en_uk, "
        "NULL::VARCHAR AS fr, NULL::VARCHAR AS es, NULL::VARCHAR AS nl, "
        "NULL::VARCHAR AS no WHERE false)"
    )
    con.execute(
        f"""
        COPY (
            WITH totals AS (
                SELECT species, count(*) AS n_detections,
                       count(DISTINCT site_id) AS n_sites,
                       count(DISTINCT country) AS n_countries
                FROM detections_sited GROUP BY ALL
            ),
            checked AS (
                SELECT species, count(*) AS n_validated,
                       count(*) FILTER (WHERE correct) AS n_correct
                FROM validated GROUP BY ALL
            )
            SELECT tot.species, n.en_uk AS common_en, n.fr AS common_fr,
                   n.es AS common_es, n.nl AS common_nl, n.no AS common_no,
                   tot.n_detections, tot.n_sites, tot.n_countries,
                   COALESCE(th.threshold, {DEFAULT_THRESHOLD}) AS threshold,
                   COALESCE(th.status, 'default') AS threshold_source,
                   COALESCE(ck.n_validated, 0) AS n_validated,
                   COALESCE(ck.n_correct, 0) AS n_correct,
                   th.precision_at_threshold
            FROM totals tot
            LEFT JOIN {names} n ON n.Scientific_Name = tot.species
            LEFT JOIN thresholds th USING (species)
            LEFT JOIN checked ck USING (species)
            ORDER BY n_detections DESC
        ) TO '{out}/species.parquet' (FORMAT parquet)
        """
    )


def build_clips(con, out: str):
    """Pick playable examples: validated-correct first, then highest confidence."""
    log.info("clips")
    con.execute(
        f"""
        COPY (
            WITH ok AS (
                SELECT filename AS name, start_s, species, bool_and(correct) AS ok
                FROM validated GROUP BY ALL
            ),
            candidates AS (
                SELECT x.species, x.site_id, x.cluster_id, x.country, x.name,
                       x.device_id, x.start_s, x.confidence, x.ts_local,
                       COALESCE(ok.ok, false) AS validated
                FROM detections_sited x
                LEFT JOIN ok USING (name, start_s, species)
                WHERE (ok.ok IS NULL AND x.confidence >= {CLIP_MIN_CONFIDENCE})
                   OR ok.ok
            ),
            picked AS (
                SELECT * FROM candidates
                QUALIFY row_number() OVER (
                    PARTITION BY species, cluster_id
                    ORDER BY validated DESC, confidence DESC
                ) <= {CLIPS_PER_SPECIES_CLUSTER}
            )
            SELECT md5(r.path || ':' || p.start_s || ':' || p.species)[:16] AS clip_id,
                   p.species, p.site_id, p.cluster_id, p.country, r.path,
                   p.start_s, p.confidence, p.validated, p.ts_local AS recorded_at
            FROM picked p
            JOIN recordings r ON r.name = p.name AND contains(r.path, p.device_id || '/')
        ) TO '{out}/clips.parquet' (FORMAT parquet)
        """
    )


def build_photos(con, src: str, out: str):
    log.info("photos")
    name = "regexp_extract(file, '[^/]+$')"
    con.execute(
        f"""
        COPY (
            SELECT path, name, device_id,
                   COALESCE(NULLIF(lower(regexp_extract(
                       name, '_[0-9a-f]{{7,8}}_([a-z]+)', 1)), ''), 'site') AS picture_type
            FROM (
                SELECT substr(file, length('{src}/data/') + 1) AS path,
                       {name} AS name,
                       lower(regexp_extract(lower({name}), '_([0-9a-f]{{7,8}})[_.( ]', 1))
                           AS device_id
                FROM glob('{src}/data/pictures/**')
                WHERE regexp_matches(lower(file), '\\.(jpe?g|png)$')
            )
            WHERE device_id <> ''
        ) TO '{out}/photos.parquet' (FORMAT parquet)
        """
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", required=True, help="s3://bucket or local dir")
    parser.add_argument(
        "--out",
        required=True,
        help="Output dir, or s3://bucket/data/preprocessed/dashboard",
    )
    parser.add_argument(
        "--names", help="CSV with Scientific_Name,en_uk,fr,es,nl,no columns"
    )
    parser.add_argument("--skip-detections", action="store_true")
    parser.add_argument(
        "--skip-species-info",
        action="store_true",
        help="Do not query Wikipedia / Wikidata",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    src = args.source.rstrip("/")
    out = args.out.rstrip("/")
    if not out.startswith("s3://"):
        Path(out).mkdir(parents=True, exist_ok=True)

    con = connect(src.startswith("s3://") or out.startswith("s3://"))
    build_deployments(con, src, out)
    build_index(con, src)
    build_coverage(con, src)
    build_effort(con, out)
    build_photos(con, src, out)
    build_validations(con, src, out)
    build_thresholds(con)
    if not args.skip_detections:
        build_detections(con, src, out)
        build_species(con, args.names, out)
        build_clips(con, out)
        if not args.skip_species_info:
            build_species_info(con, out)
            build_species_ecology(con, out)
    log.info("done -> %s", out)


if __name__ == "__main__":
    main()
