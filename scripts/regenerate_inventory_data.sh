#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

key_id="${SURF_GLOB_KEY_ID:-${AWS_ACCESS_KEY_ID:-}}"
secret="${SURF_GLOB_SECRET:-${AWS_SECRET_ACCESS_KEY:-}}"
if [ -z "$key_id" ] || [ -z "$secret" ]; then
    echo "Set SURF_GLOB_KEY_ID/SURF_GLOB_SECRET or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY in .env or the environment." >&2
    exit 1
fi

key_id="${key_id//\'/\'\'}"
secret="${secret//\'/\'\'}"
mkdir -p data/input

duckdb -bail <<EOF
CREATE OR REPLACE SECRET surf_glob (
    TYPE s3,
    REGION 'default',
    PROVIDER config,
    KEY_ID '${key_id}',
    SECRET '${secret}',
    ENDPOINT 'objectstore.surf.nl',
    URL_STYLE path
);

COPY (
    SELECT * FROM read_csv('s3://globalise-data/objects/inventory/*.csv', compression='gzip', auto_detect=true)
) TO 'data/input/inventory_data.parquet' (FORMAT PARQUET);

COPY (
    SELECT * FROM read_csv('s3://globalise-data/objects/inventory/links/*.csv', compression='gzip', auto_detect=true)
) TO 'data/input/links_data.parquet' (FORMAT PARQUET);
EOF
