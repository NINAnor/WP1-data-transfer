
import pandas as pd
import duckdb
from datetime import datetime, timedelta, timezone

from .download import resolve_source, cleanup


def device_key(ids: pd.Series) -> pd.Series:
    """Canonical recorder ID: lower case, last 8 characters, leading zeros removed.

    Bugg folders end in the 8-hex-digit ID (bugg_RPiID-1000000002550e55), but the
    site-info form often has the ID without its leading zero (2550e55).
    """
    return ids.astype("string").str.strip().str.lower().str[-8:].str.lstrip("0")


def preprocess_device_status(parquet_file, 
                             username=None, 
                             password=None, 
                             offline_threshold_days: int = 3):
    """Load and calculate comprehensive device status from parquet with site."""

    path, is_temp = resolve_source(parquet_file, username=username, password=password)

    # First get device status from recordings
    query = """
    SELECT
        device,
        RIGHT(device, 8) AS short_device,
        MAX(COALESCE(
            TRY_STRPTIME(Name, '%Y-%m-%dT%H_%M_%S.%fZ.mp3'),
            TRY_STRPTIME(Name, '%Y-%m-%dT%H_%M_%SZ.mp3'),
            STRPTIME(Name, '%Y-%m-%dT%H_%MZ.mp3')
        )) AS last_file,
        COUNT(*) as total_recordings
    FROM read_parquet(?)
    WHERE MimeType = 'audio/mpeg'
    GROUP BY device, short_device
    """

    try:
        df_status = duckdb.execute(query, (path,)).df()
    finally:
        cleanup(path, is_temp)

    # Calculate status
    now = datetime.now(timezone.utc)
    threshold = timedelta(days=offline_threshold_days)

    def calculate_status(t):
        if pd.isna(t):
            return "Offline"
        # Ensure datetime is timezone-aware
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return "Offline" if now - t > threshold else "Online"

    df_status["status"] = df_status["last_file"].apply(calculate_status)

    return df_status

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

def merge_status_site_info(df_status, site_csv_path, username=None, password=None):

    # Load site information first
    site_info = load_site_info(site_csv_path, username=username, password=password)

    # "Active" may contain NA/NaN (e.g. blank cells in the source CSV); treat
    # those as inactive rather than raising a boolean-mask error.
    site_info = site_info[site_info["Active"].fillna(False).astype(bool)].copy()

    # Match site-info device IDs to Bugg folders on the canonical ID, so that IDs
    # written without their leading zero (7 characters) still match.
    site_info["device_key"] = device_key(site_info["DeviceID"])
    df_status["device_key"] = device_key(df_status["short_device"])
    merged = pd.merge(site_info, df_status, on="device_key", how="left")
    merged["short_device"] = merged["short_device"].fillna(
        merged["DeviceID"].str.strip()
    )

    # Fill missing values for sites with no recordings
    merged["device_name"] = merged["device"].fillna("RPiID-" + merged["DeviceID"])
    merged["last_file"] = merged["last_file"].fillna(pd.NaT)
    merged["total_recordings"] = merged["total_recordings"].fillna(0)
    merged["status"] = merged["status"].fillna(
        "Offline"
    )  # Sites with no recordings are offline

    # Fill missing Country values
    merged["Country"] = merged["Country"].fillna("Unknown")

    # Rename columns to match expected interface
    # (excluding device since we already created device_name)
    merged = merged.rename(columns={"Site": "site_name", "Cluster": "cluster"})

    # Calculate days since last recording
    # Ensure timezone consistency
    now = datetime.now(timezone.utc)  # Define now for this function
    
    def make_timezone_aware(dt):
        if pd.isna(dt):
            return dt
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    merged["last_file"] = merged["last_file"].apply(make_timezone_aware)

    # Calculate days since last recording, handling NaT values
    def calculate_days_since(last_file_dt):
        if pd.isna(last_file_dt):
            return float("inf")  # Infinite days for devices with no recordings
        return (now - last_file_dt).total_seconds() / 86400

    merged["days_since_last"] = (
        merged["last_file"].apply(calculate_days_since).round(1)
    )

    merged.to_csv("../output/device_status.csv", index=False)

def main_status(parquet_file, 
                site_csv_path, 
                username=None, 
                password=None):
    
    df_status = preprocess_device_status(parquet_file, username=username, password=password)
    merge_status_site_info(df_status, site_csv_path, username=username, password=password)
