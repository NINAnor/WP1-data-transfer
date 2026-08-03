import pandas as pd
import duckdb

from .download import resolve_source, cleanup

# Country mapping
COUNTRY_MAP = {
    "proj_tabmon_NINA": "Norway",
    "proj_tabmon_NINA_ES": "Spain",
    "proj_tabmon_NINA_NL": "Netherlands",
    "proj_tabmon_NINA_FR": "France",
}

def load_site_info(csv_file, delimiter=",", username=None, password=None):
    """Load site information from CSV file or URL."""

    path, is_temp = resolve_source(csv_file, username=username, password=password, suffix=".csv")

    try:
        site_info = pd.read_csv(path, delimiter=delimiter)
    finally:
        cleanup(path, is_temp)

    # Convert coordinates to numeric values
    site_info["Latitude"] = pd.to_numeric(site_info["Latitude"], errors="coerce")
    site_info["Longitude"] = pd.to_numeric(site_info["Longitude"], errors="coerce")
    return site_info

def preprocess_heatmap(parquet_file, site_csv_path, username, password):
    """Load and process recording matrix data."""

    query = """
    SELECT *,
        COALESCE(
            TRY_STRPTIME(Name, '%Y-%m-%dT%H_%M_%S.%fZ.mp3'),
            TRY_STRPTIME(Name, '%Y-%m-%dT%H_%M_%SZ.mp3'),
            STRPTIME(Name, '%Y-%m-%dT%H_%MZ.mp3')
        ) AS datetime
    FROM read_parquet(?)
    WHERE MimeType = 'audio/mpeg'
    AND datetime >= ?
    """

    path, is_temp = resolve_source(parquet_file, username=username, password=password)

    try:
        data = duckdb.execute(query, (path, "2025-01-01")).df()
    finally:
        cleanup(path, is_temp)

    # Extract device ID from path
    data["short_device_id"] = (
        data["Path"]
        .str.split("/")
        .str[-3]
        .str.split("-")
        .str[-1]
        .str[-8:]
        .str.strip()
    )

    # Load site info
    site_info = load_site_info(site_csv_path, username=username, password=password)
    site_info = site_info[site_info["Active"]].copy()
    site_info["clean_id"] = site_info["DeploymentID"].str.strip()
    data["clean_id"] = data["short_device_id"].str.strip()

    # Merge data
    df_merged = pd.merge(data, site_info, on="clean_id", how="left")

    # Map countries
    for code, country_name in COUNTRY_MAP.items():
        df_merged.loc[df_merged["country"] == code, "Country"] = country_name

    df_merged["time_period"] = (df_merged["datetime"].dt.to_period("D").astype(str))

    # Create matrix
    matrix_data = pd.crosstab(
        index=[df_merged["Country"], df_merged["device"]],
        columns=df_merged["time_period"],
        values=df_merged["datetime"],
        aggfunc="count",
    ).fillna(0)

    sorted_matrix = matrix_data.sort_index()
    sorted_matrix.to_csv("./recording_matrix.csv")
