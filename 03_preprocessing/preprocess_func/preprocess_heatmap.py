import pandas as pd
import duckdb

from .download import resolve_source, cleanup

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

    # Extract device ID: last 8 chars of the device column (bugg_RPiID-xxx-<id>)
    data["short_device_id"] = data["device"].str[-8:].str.strip()

    # Load site info
    site_info = load_site_info(site_csv_path, username=username, password=password)
    # "Active" may contain NA/NaN (e.g. blank cells in the source CSV); treat
    # those as inactive rather than raising a boolean-mask error.
    site_info = site_info[site_info["Active"].fillna(False).astype(bool)].copy()
    site_info["clean_id"] = site_info["DeviceID"].str.strip().str[-8:]
    data["clean_id"] = data["short_device_id"].str.strip()

    # Merge data — Country comes directly from site_info
    df_merged = pd.merge(data, site_info, on="clean_id", how="left")

    df_merged["time_period"] = (df_merged["datetime"].dt.to_period("D").astype(str))

    # Create matrix
    matrix_data = pd.crosstab(
        index=[df_merged["Country"], df_merged["device"]],
        columns=df_merged["time_period"],
        values=df_merged["datetime"],
        aggfunc="count",
    ).fillna(0)

    sorted_matrix = matrix_data.sort_index()
    sorted_matrix.to_csv("../output/recording_matrix.csv")
