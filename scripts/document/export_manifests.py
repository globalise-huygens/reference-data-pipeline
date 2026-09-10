"""
Export IIIF Manifests for all inventories in the database.

Writes gzipped or plain JSON files, either to disk or directly to an
S3-compatible object store, following the same conventions as the rest of
the pipeline's `convert_to_json.py`.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session
from tqdm import tqdm

from utils import (
    add_s3_arguments,
    build_s3_upload_config,
    create_s3_client,
    output_framed_json,
)
from models import Inventory
from export import inventory_to_manifest_jsonld

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///globalise_documents.db")
BASE_URI = "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722"


def export_all_manifests(output_dir, gzipped, s3_client, s3_config):
    engine = create_engine(DATABASE_URL, echo=False)
    session = Session(engine)

    total = session.query(func.count(Inventory.id)).scalar()
    print(f"Exporting manifests for {total} inventories to {output_dir}/")

    inventories = session.query(Inventory).all()

    t0 = time.time()
    for inventory in tqdm(inventories, desc="Exporting manifests", unit="manifest"):
        inv_num = inventory.inventory_number
        manifest_uri = f"{BASE_URI}/inventory:{inv_num}.manifest"
        manifest = inventory_to_manifest_jsonld(inventory, manifest_uri)

        output_framed_json(
            manifest,
            f"{inv_num}.manifest.json",
            output_dir,
            gzipped,
            s3_client,
            s3_config,
        )

    elapsed = time.time() - t0
    print(f"\nDone. {total} manifests written to {output_dir}/ in {elapsed:.1f}s")

    session.close()


def parse_args():
    parser = argparse.ArgumentParser(description="Export IIIF Manifests.")
    parser.add_argument(
        "output_dir",
        nargs="?",
        default=os.environ.get("MANIFEST_OUTPUT_DIR", "data/output/s3/inventory"),
        help="Base local output directory (default: data/output/s3/inventory, or MANIFEST_OUTPUT_DIR env var)",
    )
    parser.add_argument(
        "--gzipped", action="store_true", help="Output gzipped JSON files"
    )
    add_s3_arguments(parser)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    s3_config = build_s3_upload_config(args)
    s3_client = create_s3_client(s3_config) if s3_config else None
    export_all_manifests(args.output_dir, args.gzipped, s3_client, s3_config)
