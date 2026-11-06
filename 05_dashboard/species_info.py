"""
Fetch a short description and a freely licensed photo for each species from
Wikipedia / Wikimedia Commons. Results are cached in species_info.parquet and
only missing species are fetched on later runs.

Wikimedia API etiquette: identify the client with a User-Agent and keep the
request rate modest.
"""

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html import unescape

log = logging.getLogger("tabmon.species_info")

USER_AGENT = "TABMON-dashboard/0.1 (https://www.nina.no/english/TABMON; benjamin.cretois@nina.no)"
SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia only serves standard thumbnail widths (e.g. 330, 500, 960, 1280).
THUMB_WIDTH = 960

COLUMNS = (
    "species",
    "wiki_title",
    "wiki_url",
    "extract",
    "image_url",
    "image_page",
    "image_artist",
    "image_license",
    "image_license_url",
)


def _get_json(url: str, attempts: int = 6) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code in (429, 503) and attempt < attempts - 1:
                retry_after = exc.headers.get("Retry-After", "")
                wait = float(retry_after) if retry_after.isdigit() else 2**attempt
                time.sleep(min(wait, 60))
                continue
            log.warning("%s -> HTTP %s", url, exc.code)
        except (urllib.error.URLError, TimeoutError) as exc:
            log.warning("%s -> %s", url, exc)
        return None
    return None


def _strip_html(value: str | None) -> str | None:
    if not value:
        return None
    text = unescape(re.sub(r"<[^>]+>", "", value))
    return re.sub(r"\s+", " ", text).strip() or None


def _commons_file(image_url: str) -> str | None:
    """File name of a Commons image URL (thumbnail or original)."""
    if "/wikipedia/commons/" not in image_url:
        return None  # local (often non-free) Wikipedia upload: skip
    parts = urllib.parse.unquote(image_url).split("/")
    if "thumb" in parts:
        return parts[parts.index("thumb") + 3]
    return parts[-1]


def _image_metadata(file_name: str) -> dict:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "titles": f"File:{file_name}",
            "prop": "imageinfo",
            "iiprop": "extmetadata|url",
            "format": "json",
        }
    )
    data = _get_json(f"{COMMONS_API}?{params}") or {}
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        return {
            "image_page": info.get("descriptionurl"),
            "image_artist": _strip_html(meta.get("Artist", {}).get("value")),
            "image_license": _strip_html(meta.get("LicenseShortName", {}).get("value")),
            "image_license_url": meta.get("LicenseUrl", {}).get("value"),
        }
    return {}


def fetch_one(species: str) -> dict:
    row = dict.fromkeys(COLUMNS)
    row["species"] = species
    summary = _get_json(
        SUMMARY_URL.format(urllib.parse.quote(species.replace(" ", "_")))
    )
    if not summary or summary.get("type") == "disambiguation":
        return row
    row["wiki_title"] = summary.get("title")
    row["wiki_url"] = summary.get("content_urls", {}).get("desktop", {}).get("page")
    row["extract"] = summary.get("extract")
    image = (summary.get("thumbnail") or {}).get("source")
    file_name = _commons_file(image) if image else None
    if file_name:
        meta = _image_metadata(file_name)
        # Only keep images whose licence we could read, so we can attribute them.
        if meta.get("image_license"):
            row["image_url"] = re.sub(r"/\d+px-", f"/{THUMB_WIDTH}px-", image)
            row.update(meta)
    return row


def build_species_info(con, out: str, workers: int = 2) -> None:
    log.info("species info (Wikipedia)")
    try:
        con.execute(
            f"CREATE OR REPLACE TABLE species_info AS "
            f"SELECT * FROM read_parquet('{out}/species_info.parquet')"
        )
    except Exception:  # noqa: BLE001 - first run or unavailable species-info cache
        cols = ", ".join(f"{c} VARCHAR" for c in COLUMNS)
        con.execute(f"CREATE OR REPLACE TABLE species_info ({cols})")

    # Fix thumbnail widths cached by earlier versions.
    con.execute(
        f"UPDATE species_info SET image_url = "
        f"regexp_replace(image_url, '/[0-9]+px-', '/{THUMB_WIDTH}px-')"
    )
    # Species without a page are retried each run (cheap, and covers transient errors).
    con.execute("DELETE FROM species_info WHERE wiki_title IS NULL")
    missing = [
        r[0]
        for r in con.execute(
            f"""
            SELECT species FROM read_parquet('{out}/species.parquet')
            WHERE species NOT IN (SELECT species FROM species_info)
            """
        ).fetchall()
    ]
    log.info("fetching %d new species", len(missing))
    with ThreadPoolExecutor(workers) as pool:
        rows = list(pool.map(fetch_one, missing))
    if rows:
        placeholders = ", ".join("?" for _ in COLUMNS)
        con.executemany(
            f"INSERT INTO species_info VALUES ({placeholders})",
            [[r[c] for c in COLUMNS] for r in rows],
        )
    con.execute(f"COPY species_info TO '{out}/species_info.parquet' (FORMAT parquet)")
