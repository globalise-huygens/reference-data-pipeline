#!/bin/bash

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1
cd "$ROOT_DIR" || exit 1

# Load environment variables
if [ -f .env ]; then
    set -a
    source .env || exit 1
    set +a
fi

key_id="${SURF_GLOB_KEY_ID:-${AWS_ACCESS_KEY_ID:-}}"
secret="${SURF_GLOB_SECRET:-${AWS_SECRET_ACCESS_KEY:-}}"
if [ -z "$key_id" ] || [ -z "$secret" ]; then
    echo "Skipping inventory download: set SURF_GLOB_KEY_ID/SURF_GLOB_SECRET or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY in .env or the environment." >&2
    exit 0
fi

key_id="${key_id//\'/\'\'}"
secret="${secret//\'/\'\'}"
mkdir -p data/input || exit 1

duckdb <<EOF
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
    SELECT
        *,
        split_part(split_part(annotation_id, '#', 1), ':', -1) AS filename
    FROM read_csv(
        's3://globalise-data/objects/inventory/*.csv',
        compression='gzip',
        auto_detect=true,
        header=true,
        delim=',',
        quote='"',
        escape='"'
    )
) TO 'data/input/inventory_data.parquet' (FORMAT PARQUET);

COPY (
    SELECT
        *,
        split_part(split_part(annotation_id, '#', 1), ':', -1) AS filename
    FROM read_csv(
        's3://globalise-data/objects/inventory/links/*.csv',
        compression='gzip',
        auto_detect=true,
        header=true,
        delim=',',
        quote='"',
        escape='"',
        types={
            'annotation_id': 'VARCHAR',
            'status_id': 'VARCHAR',
            'annotation_entity_type': 'VARCHAR',
            'entity_id': 'VARCHAR',
            'entity_uri': 'VARCHAR',
            'entity_type': 'VARCHAR',
            'entity_label': 'VARCHAR',
            'concept_uri': 'VARCHAR',
            'concept_label': 'VARCHAR',
            'begin_of_the_begin': 'VARCHAR',
            'end_of_the_begin': 'VARCHAR',
            'begin_of_the_end': 'VARCHAR',
            'end_of_the_end': 'VARCHAR',
            'linking_method': 'VARCHAR',
            'comments': 'VARCHAR',
            'datetime': 'TIMESTAMP',
            'similarity_type': 'VARCHAR'
        }
    )
) TO 'data/input/links_data.parquet' (FORMAT PARQUET);
EOF
