import duckdb

from .download import resolve_source, cleanup

def preprocess_all_device_stats(parquet_file, username=None, password=None):
    """Get statistics for all devices in the audio dataset."""

    path, is_temp = resolve_source(parquet_file, username=username, password=password)

    query = """
    SELECT 
        RIGHT(device, 8) AS device_id,
        device AS full_device_name,
        COUNT(*) AS total_recordings,
        SUM(Size) AS total_size_bytes,
        MIN(ModTime) AS earliest_recording,
        MAX(ModTime) AS latest_recording
    FROM read_parquet(?)
    WHERE MimeType = 'audio/mpeg'
    AND device IS NOT NULL
    AND device != ''
    GROUP BY device
    ORDER BY total_recordings DESC
    """

    try:
        result = duckdb.execute(query, (path,)).df()
    finally:
        cleanup(path, is_temp)

    # Add calculated columns
    result['total_size_gb'] = result['total_size_bytes'] / (1024**3)
    result['avg_file_size_mb'] = (result['total_size_bytes'] / result['total_recordings']) / (1024**2)

    # Save to CSV
    result.to_csv("../output/all_device_stats.csv", index=False)

def preprocess_dataset_stats(parquet_file, username=None, password=None):
    """Get statistics for the entire audio dataset."""

    path, is_temp = resolve_source(parquet_file, username=username, password=password)

    query = """
    SELECT COUNT(*) as total_recordings, SUM(Size) as total_size_bytes
    FROM read_parquet(?)
    WHERE MimeType = 'audio/mpeg'
    """

    try:
        result = duckdb.execute(query, (path,)).df()
    finally:
        cleanup(path, is_temp)

    result.to_csv("../output/dataset_stats.csv", index=False)
