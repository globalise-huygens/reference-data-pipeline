#!/usr/bin/env python3
"""Regenerate the inventory links parquet file from Object Store."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT_DIR / "data/input/links_data.parquet"
SOURCE_PATH = "s3://globalise-data/objects/inventory/links/*.csv"


def main() -> None:
    load_dotenv(ROOT_DIR / ".env")

    key_id = os.environ.get("SURF_GLOB_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID")
    secret = os.environ.get("SURF_GLOB_SECRET") or os.environ.get("AWS_SECRET_ACCESS_KEY")
    if not key_id or not secret:
        raise RuntimeError(
            "Set SURF_GLOB_KEY_ID/SURF_GLOB_SECRET or "
            "AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY in .env."
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect()
    try:
        connection.execute("INSTALL httpfs; LOAD httpfs")
        connection.execute(
            """
            CREATE OR REPLACE SECRET surf_glob (
                TYPE s3,
                REGION 'default',
                PROVIDER config,
                KEY_ID ?,
                SECRET ?,
                ENDPOINT 'objectstore.surf.nl',
                URL_STYLE path
            )
            """,
            [key_id, secret],
        )
        connection.execute(
            "COPY (SELECT * FROM read_csv("
            f"'{SOURCE_PATH}'"
            ", compression='gzip', auto_detect=true)) "
            f"TO '{OUTPUT_PATH}' (FORMAT PARQUET)"
        )
    finally:
        connection.close()


if __name__ == "__main__":
    main()