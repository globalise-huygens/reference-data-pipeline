"""
Export a IIIF Collection that references all inventory manifests.

Generates a gzipped JSON file ready for upload to the object store alongside the manifests:
    aws s3 sync objects/ s3://globalise-data/objects --acl=public-read --content-encoding gzip
"""

import argparse
import os
import sys

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
from models import Inventory

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///globalise_documents.db")
BASE_URI = "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722"


def natural_inv_sort_key(inv):
    """Sort inventory numbers naturally: numeric prefix then alphabetic suffix."""
    s = inv.inventory_number or ""
    i = 0
    while i < len(s) and s[i].isdigit():
        i += 1
    num = int(s[:i]) if i > 0 else 0
    suffix = s[i:].upper()
    return (num, suffix)


def export_collection(output_dir, gzipped, s3_client, s3_config):
    engine = create_engine(DATABASE_URL, echo=False)
    session = Session(engine)

    inventories = (
        session.query(Inventory)
        .options(
            selectinload(Inventory.titles),
            selectinload(Inventory.scans),
        )
        .all()
    )
    inventories.sort(key=natural_inv_sort_key)

    print(f"Building IIIF Collection for {len(inventories)} inventories...")

    items = []
    for inventory in tqdm(inventories, desc="Building collection items"):
        inv_num = inventory.inventory_number

        # Build label from titles or fall back to inventory number
        if inventory.titles:
            title_text = "; ".join(t.title for t in inventory.titles if t.title)
            label_text = f"{inv_num} - {title_text}"
        else:
            label_text = f"{inv_num}"

        manifest_ref = {
            "id": f"{BASE_URI}/inventory:{inv_num}.manifest",
            "type": "Manifest",
            "label": {"en": [label_text]},
        }

        # Add navDate if date information is available
        if inventory.date_start:
            manifest_ref["navDate"] = f"{inventory.date_start}T00:00:00"
        elif inventory.date_end:
            manifest_ref["navDate"] = f"{inventory.date_end}T00:00:00"

        # Add thumbnail from first scan
        if inventory.scans:
            first_scan = sorted(inventory.scans, key=lambda s: s.filename or "")[0]
            thumb_url = first_scan.get_image_url(size="982,")
            service_id = None
            if getattr(first_scan, "iiif_image_info", False):
                service_id = first_scan.iiif_image_info.replace("/info.json", "")
            if thumb_url and service_id:
                manifest_ref["thumbnail"] = [
                    {
                        "id": thumb_url,
                        "type": "Image",
                        "height": first_scan.height,
                        "width": first_scan.width,
                        "service": [
                            {
                                "@id": service_id,
                                "@type": "ImageService3",
                                "profile": "level2",
                                "format": "image/jpeg",
                            }
                        ],
                        "format": "image/jpeg",
                    }
                ]

        items.append(manifest_ref)

    collection = {
        "@context": [
            "https://linked.art/ns/v1/linked-art.json",
            "http://iiif.io/api/presentation/3/context.json",
        ],
        "id": f"{BASE_URI}/inventory:collection",
        "type": "Collection",
        "label": {
            "en": ["GLOBALISE — Dutch East India Company archives (1.04.02)"],
        },
        "summary": {
            "en": [
                "A collection of digitised inventories from the archives of the "
                "Dutch East India Company (VOC), part of the National Archives of "
                "the Netherlands (NL-HaNA), access number 1.04.02."
            ],
        },
        "requiredStatement": {
            "label": {"en": ["Attribution"]},
            "value": {
                "en": [
                    "<span>GLOBALISE Project. "
                    '<a href="https://creativecommons.org/publicdomain/zero/1.0/">'
                    '<img src="https://licensebuttons.net/l/zero/1.0/88x31.png" '
                    'alt="CC0 1.0 Universal (CC0 1.0) Public Domain Dedication"/> '
                    "</a> </span>"
                ]
            },
        },
        "rights": "http://creativecommons.org/publicdomain/zero/1.0/",
        "homepage": [
            {
                "id": "https://globalise.huygens.knaw.nl",
                "type": "Text",
                "label": {"en": ["GLOBALISE Project"]},
                "format": "text/html",
            }
        ],
        "provider": [
            {
                "id": "https://globalise.huygens.knaw.nl",
                "type": "Agent",
                "label": {"en": ["GLOBALISE Project"]},
                "homepage": [
                    {
                        "id": "https://globalise.huygens.knaw.nl",
                        "type": "Text",
                        "label": {"en": ["GLOBALISE Project"]},
                        "format": "text/html",
                    }
                ],
                "logo": [
                    {
                        "id": "https://objectstore.surf.nl/87435b768620494e8e911c83d1997f24:globalise-data/static/img/globalise.png",
                        "type": "Image",
                        "height": 182,
                        "width": 1200,
                        "format": "image/png",
                    }
                ],
            }
        ],
        "seeAlso": [
            {
                "id": f"{BASE_URI}/inventory:set",
                "type": "Set",
                "label": {"en": ["Set metadata"]},
            }
        ],
        "items": items,
    }

    output_framed_json(
        collection, "collection.json", output_dir, gzipped, s3_client, s3_config
    )

    print(f"Done. Collection with {len(items)} manifests written to {output_dir}/collection.json")

    session.close()


def parse_args():
    parser = argparse.ArgumentParser(description="Export IIIF Collection.")
    parser.add_argument(
        "output_dir",
        nargs="?",
        default=os.environ.get("MANIFEST_OUTPUT_DIR", "data/s3/objects/inventory"),
        help="Base local output directory (default: data/s3/objects/inventory, or MANIFEST_OUTPUT_DIR env var)",
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
    export_collection(args.output_dir, args.gzipped, s3_client, s3_config)
