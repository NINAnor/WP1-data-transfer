"""Shared download helper with local-path support, validation and retries."""

import os
import tempfile
import time

import requests

PARQUET_MAGIC = b"PAR1"
CHUNK_SIZE = 1024 * 1024


class DownloadError(Exception):
    """Raised when a download fails or fails validation."""


def resolve_source(source, username=None, password=None, suffix=".parquet", retries=3, timeout=600):
    """Resolve `source` to a local file path.

    If `source` is an existing local file (or not an http(s) URL), it is
    returned as-is (is_temp=False). Otherwise it is treated as a URL:
    downloaded with retries, validated, and written to a temporary file
    (is_temp=True).

    Returns a (path, is_temp) tuple.
    """
    if not source.startswith(("http://", "https://")):
        return source, False

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            return _download(source, username, password, suffix, timeout), True
        except DownloadError as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2 ** attempt)

    raise last_error


def cleanup(path, is_temp):
    """Remove a temporary file produced by resolve_source. No-op for local paths."""
    if is_temp and path:
        try:
            os.unlink(path)
        except OSError:
            pass


def _download(url, username, password, suffix, timeout):
    try:
        with requests.get(url, auth=(username, password), stream=True, timeout=timeout) as response:
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        tmp.write(chunk)
                path = tmp.name
    except requests.RequestException as exc:
        raise DownloadError(f"Failed to download {url}: {exc}") from exc

    size = os.path.getsize(path)
    if size == 0:
        os.unlink(path)
        raise DownloadError(
            f"Empty response from {url} "
            f"(HTTP {response.status_code}, content-type {response.headers.get('Content-Type')})"
        )

    if suffix == ".parquet":
        with open(path, "rb") as f:
            head = f.read(4)
            f.seek(max(0, size - 4))
            tail = f.read(4)
        if head != PARQUET_MAGIC or tail != PARQUET_MAGIC:
            os.unlink(path)
            raise DownloadError(
                f"Downloaded file from {url} is not a valid parquet file "
                f"(HTTP {response.status_code}, content-type {response.headers.get('Content-Type')}, "
                f"size {size} bytes): expected 'PAR1' magic bytes at start and end, "
                f"got start={head!r} end={tail!r}"
            )

    return path
