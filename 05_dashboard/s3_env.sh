#!/bin/bash
# Load S3 credentials from the configured rclone remote without logging them.
set -euo pipefail

eval "$(python3 - "${RCLONE_CONFIG:-}" <<'PY'
import configparser
import os
import shlex
import sys

config_path = sys.argv[1]
parser = configparser.ConfigParser(interpolation=None)
if config_path:
    parser.read(config_path)
remote = parser["nirds3"] if parser.has_section("nirds3") else {}
for env_name, config_name in (
    ("S3_ACCESS_KEY_ID", "access_key_id"),
    ("S3_SECRET_ACCESS_KEY", "secret_access_key"),
    ("S3_ENDPOINT", "endpoint"),
):
    value = os.environ.get(env_name) or remote.get(config_name)
    if not value:
        raise SystemExit(f"missing {env_name} and nirds3.{config_name} in rclone config")
    print(f"export {env_name}={shlex.quote(value)}")
PY
)"
