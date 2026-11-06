import os

from dotenv import load_dotenv
from preprocess_func.audio_preprocessing import (
    preprocess_all_device_stats,
    preprocess_dataset_stats,
)
from preprocess_func.device_status_preprocessing import main_status
from preprocess_func.preprocess_heatmap import preprocess_heatmap

load_dotenv()


def main(parquet_file, username, password, site_csv):
    preprocess_dataset_stats(parquet_file, username, password)
    preprocess_all_device_stats(parquet_file, username, password)
    main_status(parquet_file, site_csv, username=username, password=password)
    preprocess_heatmap(parquet_file, site_csv, username=username, password=password)


if __name__ == "__main__":
    # Load configuration from environment variables
    parquet_file = os.getenv("PARQUET_FILE_URL")
    username = os.getenv("USERNAME")
    password = os.getenv("PASSWORD")
    site_csv = os.getenv("SITE_CSV_URL")

    # Validate that all required environment variables are set
    required_vars = {
        "PARQUET_FILE_URL": parquet_file,
        "USERNAME": username,
        "PASSWORD": password,
        "SITE_CSV_URL": site_csv,
    }

    missing_vars = [var for var, value in required_vars.items() if not value]
    if missing_vars:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing_vars)}"
        )

    main(
        parquet_file=parquet_file,
        username=username,
        password=password,
        site_csv=site_csv,
    )
