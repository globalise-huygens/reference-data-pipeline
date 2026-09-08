"""
Export individual CuratedHolding and HumanMadeObject JSON-LD records.

These are the objects currently returned directly from models by `inventory_to_jsonld`
and `document_physical_to_jsonld` and embedded inside `rdfs:seeAlso` links.
This script serializes these individual entities either to disk or directly
to an S3-compatible object store, following the same conventions as the
rest of the pipeline's `convert_to_json.py`.

Output paths (relative to --output-dir):
  document/<uuid>.json
  inventory/<inventory_number>.json
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, selectinload
from tqdm import tqdm

from utils import (
    add_s3_arguments,
    build_s3_upload_config,
    create_s3_client,
    output_framed_json,
)
from models import Inventory, Document, Series
from export import (
    document_physical_to_jsonld,
    inventory_to_jsonld,
    inventory_to_annotations_jsonld,
    get_annotationcollections_for_inventory,
    series_to_jsonld,
)

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///globalise_documents.db")


def natural_inv_sort_key(inv):
    """Sort inventory numbers naturally: numeric prefix then alphabetic suffix."""
    s = inv.inventory_number or ""
    i = 0
    while i < len(s) and s[i].isdigit():
        i += 1
    num = int(s[:i]) if i > 0 else 0
    suffix = s[i:].upper()
    return (num, suffix)


def export_documents(output_dir, gzipped, s3_client, s3_config):
    engine = create_engine(DATABASE_URL, echo=False)
    session = Session(engine)

    def emit(data, relative_path):
        output_framed_json(data, relative_path, output_dir, gzipped, s3_client, s3_config)

    print("Loading inventories...")
    inventories = session.query(Inventory).all()
    inventories.sort(key=natural_inv_sort_key)
    print(f"Loaded {len(inventories)} inventories.")

    t0 = time.time()

    # 1. Export Inventories
    for inventory in tqdm(inventories, desc="Exporting inventories", unit="inventory"):
        inv_data = inventory_to_jsonld(inventory)
        emit(inv_data, os.path.join("inventory", f"{inventory.inventory_number}.json"))

        # Export .annotations Set for the inventory
        ann_data = inventory_to_annotations_jsonld(inventory)
        emit(
            ann_data,
            os.path.join(
                "inventory", f"{inventory.inventory_number}.annotations.json"
            ),
        )

        # Export individual AnnotationCollections (.annotations.transcriptions, .annotations.entities, .annotations.events)
        ann_collections = get_annotationcollections_for_inventory(inventory)
        for collection_type, collection_data in zip(
            ["transcriptions", "entities", "events"], ann_collections
        ):
            emit(
                collection_data,
                os.path.join(
                    "inventory",
                    f"{inventory.inventory_number}.annotations.{collection_type}.json",
                ),
            )

    elapsed_inv = time.time() - t0
    print(f"Exported {len(inventories)} inventories in {elapsed_inv:.1f}s.")

    # 1b. Export the Series (Sets) into the same file system, resolving subsets and curated holdings.
    print("Loading Series (Sets)...")
    series_all = (
        session.query(Series)
        .options(selectinload(Series.sub_series), selectinload(Series.inventories))
        .all()
    )
    print(f"Loaded {len(series_all)} series records.")

    for s in tqdm(series_all, desc="Exporting series", unit="series"):
        s_data = series_to_jsonld(s)

        # Inject the members (which are sub-series and inventories)
        members = []

        # 1. Sub-sets
        for sub_s in sorted(s.sub_series, key=lambda x: x.title or ""):
            members.append(
                {
                    "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/series:{sub_s.id}",
                    "type": "Set",
                    "_label": sub_s.title,
                }
            )

        # 2. CuratedHoldings
        sorted_invs = sorted(s.inventories, key=natural_inv_sort_key)
        for inv in sorted_invs:
            members.append(
                {
                    "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/inventory:{inv.inventory_number}",
                    "type": "CuratedHolding",
                    "_label": f"Inventory {inv.inventory_number}",
                }
            )

        if members:
            s_data["member"] = members

        emit(s_data, os.path.join("inventory", f"series_{s.id}.json"))

    # 1c. Export the top-level global Set of all top-level series
    print("Generating top level Set metadata...")
    top_level_series = [s for s in series_all if s.part_of_id is None]
    top_level_series.sort(key=lambda x: x.title or "")

    # Recursively build the full embedded structure
    def build_nested_member(s):
        s_data = series_to_jsonld(s)

        # Remove 'member_of' if it exists since we're nesting top-down
        if "member_of" in s_data:
            del s_data["member_of"]

        members = []
        for sub_s in sorted(s.sub_series, key=lambda x: x.title or ""):
            members.append(build_nested_member(sub_s))

        for inv in sorted(s.inventories, key=natural_inv_sort_key):
            members.append(
                {
                    "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/inventory:{inv.inventory_number}",
                    "type": "CuratedHolding",
                    "_label": f"Inventory {inv.inventory_number}",
                }
            )

        if members:
            s_data["member"] = members

        return s_data

    set_data = {
        "@context": "https://linked.art/ns/v1/linked-art.json",
        "id": "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/inventory:set",
        "type": "Set",
        "_label": "Set of the Globalise corpus",
        "identified_by": [
            {
                "type": "Name",
                "classified_as": [
                    {
                        "id": "http://vocab.getty.edu/aat/300404670",
                        "type": "Type",
                        "_label": "Primary Name",
                    }
                ],
                "content": "Set of all VOC inventories that are part of the Globalise corpus",
            }
        ],
        "member": [build_nested_member(s) for s in top_level_series],
        "subject_of": [
            {
                "type": "LinguisticObject",
                "digitally_carried_by": [
                    {
                        "type": "DigitalObject",
                        "access_point": [
                            {
                                "id": "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/inventory:collection",
                                "type": "DigitalObject",  # Also Manifest?
                                "_label": "IIIF Collection of all inventories in the Globalise corpus",
                            }
                        ],
                        "conforms_to": [
                            {
                                "id": "http://iiif.io/api/presentation/",
                                "type": "InformationObject",
                            }
                        ],
                        "format": "application/ld+json;profile='http://iiif.io/api/presentation/3/context.json'",
                    }
                ],
            },
        ],
    }
    emit(set_data, os.path.join("inventory", "set.json"))
    print("Exported global Set object.")

    # How to save 500K files?
    # 2. Export Documents

    print("Loading documents...")
    # documents = session.query(Document).all()

    # For now, only inventory 1053 and 3598
    documents = (
        session.query(Document)
        .join(Document.inventory)
        .filter(Inventory.inventory_number.in_(["1053", "3598"]))
        .all()
    )

    total_docs = len(documents)
    print(f"Loaded {total_docs} documents.")

    t1 = time.time()
    for doc in tqdm(documents, desc="Exporting documents", unit="document"):
        doc_data = document_physical_to_jsonld(doc)
        emit(doc_data, os.path.join("document", f"{doc.id}.json"))

    elapsed_doc = time.time() - t1
    print(f"Exported {total_docs} documents in {elapsed_doc:.1f}s.")

    print("\nDone.")
    session.close()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export document/inventory/series JSON-LD records."
    )
    parser.add_argument(
        "output_dir",
        nargs="?",
        default=os.environ.get("DOCUMENTS_OUTPUT_DIR", "data/s3/objects"),
        help="Base local output directory (default: data/s3/objects, or DOCUMENTS_OUTPUT_DIR env var)",
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
    export_documents(args.output_dir, args.gzipped, s3_client, s3_config)
