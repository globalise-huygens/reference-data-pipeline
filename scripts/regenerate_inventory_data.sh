#!/bin/bash

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1
cd "$ROOT_DIR" || exit 1

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

mkdir -p data/input || exit 1

duckdb <<EOF
CREATE OR REPLACE SECRET surf_glob (
    TYPE s3,
    REGION 'default',
    PROVIDER config,
    KEY_ID '${SURF_GLOB_KEY_ID}',
    SECRET '${SURF_GLOB_SECRET}',
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
