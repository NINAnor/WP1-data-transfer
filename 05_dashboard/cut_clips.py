"""Pre-cut dashboard example clips and upload them to the TABMON bucket.

Existing clips are skipped, and an individual failure does not stop the rest
of the batch; failed clips are retried on the next run.
"""

import argparse
import logging
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
import duckdb
import mp3
from botocore.config import Config

log = logging.getLogger("tabmon.cut_clips")

TABLES_PREFIX = os.environ.get("TABLES_PREFIX", "data/preprocessed/dashboard")
CLIP_CONTEXT_S = 1.0
CLIP_DETECTION_S = 3.0
S3_BUCKET = os.environ.get("S3_BUCKET", "bencretois-ns8129k-proj-tabmon")


def s3_client():
    endpoint = os.environ.get("S3_ENDPOINT", "s3.nird.sigma2.no")
    if not endpoint.startswith("http"):
        endpoint = f"https://{endpoint}"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("S3_REGION", "us-east-1"),
        config=Config(
            retries={"max_attempts": 5, "mode": "adaptive"},
            read_timeout=180,
            max_pool_connections=64,
        ),
    )


def s3_range_reader(s3, bucket: str, key: str) -> mp3.RangeReader:
    def read(start: int, end: int) -> tuple[bytes, int]:
        obj = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes={start}-{end}")
        match = re.search(r"/(\d+)$", obj.get("ContentRange", ""))
        body = obj["Body"].read()
        return body, int(match.group(1)) if match else start + len(body)

    return read


def existing_keys(s3, bucket: str, prefix: str) -> set[str]:
    keys = set()
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=prefix
    ):
        keys.update(obj["Key"] for obj in page.get("Contents", []))
    return keys


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--limit", type=int, help="Only cut this many (for testing)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    prefix = TABLES_PREFIX.strip("/")
    s3 = s3_client()

    with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
        s3.download_fileobj(S3_BUCKET, f"{prefix}/clips.parquet", tmp)
        tmp.flush()
        clips = duckdb.sql(
            f"SELECT clip_id, path, start_s FROM read_parquet('{tmp.name}')"
        ).fetchall()

    clip_prefix = f"{prefix}/clips/{mp3.CLIP_VERSION}/"
    done = existing_keys(s3, S3_BUCKET, clip_prefix)
    todo = [clip for clip in clips if f"{clip_prefix}{clip[0]}.mp3" not in done]
    if args.limit:
        todo = todo[: args.limit]
    log.info(
        "%d clips listed, %d already cut, %d to do", len(clips), len(done), len(todo)
    )

    def work(clip):
        clip_id, path, start_s = clip
        audio = mp3.cut_clip(
            s3_range_reader(s3, S3_BUCKET, path),
            float(start_s),
            CLIP_CONTEXT_S,
            CLIP_DETECTION_S,
        )
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=f"{clip_prefix}{clip_id}.mp3",
            Body=audio,
            ContentType="audio/mpeg",
        )
        return len(audio)

    started, ok, failed = time.monotonic(), 0, 0
    with ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(work, clip): clip for clip in todo}
        for future in as_completed(futures):
            try:
                future.result()
                ok += 1
            except Exception as exc:  # noqa: BLE001 - continue after per-clip failures
                failed += 1
                log.warning("clip %s failed: %s", futures[future][0], exc)
            if (ok + failed) % 250 == 0:
                rate = (ok + failed) / (time.monotonic() - started)
                log.info("%d/%d done (%.1f clips/s)", ok + failed, len(todo), rate)
    log.info(
        "finished: %d cut, %d failed in %.0fs", ok, failed, time.monotonic() - started
    )


if __name__ == "__main__":
    main()
