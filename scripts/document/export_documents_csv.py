"""
Export documents to CSV with identifier, inventory number, scan filenames,
title, date, settlement, method, and start/end scan types.

Export documents to a CSV file.

options:
  -h, --help            show this help message and exit
  --filename, -f FILENAME
                        Output filename (default: data/s3/document/documents.csv)
  --gzip                Gzip-compress the output file (default)
  --no-gzip             Write plain CSV without gzip compression

"""

import argparse
import csv
import gzip
import io
import os
import sys
import logging

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from tqdm import tqdm

from utils import (
    add_s3_arguments,
    build_s3_upload_config,
    create_s3_client,
    output_bytes,
)
from models import Base, Document

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

TYPE_URI_PREFIX = "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/thesaurus:"

# Database setup
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///globalise_documents.db")
engine = create_engine(DATABASE_URL, echo=False)


def get_settlement(document):
    """Get the settlement UUID and label for a document."""
    loc = getattr(document, "location", None)
    if loc:
        settlement_id = getattr(loc, "id", "") or ""
        labels = getattr(loc, "labels", None)
        if labels and labels[0] and getattr(labels[0], "label", None):
            settlement_label = labels[0].label
        else:
            settlement_label = getattr(loc, "glob_id", "") or ""
        return settlement_id, settlement_label
    return "", ""


def get_inventory_number(document):
    """Get the inventory number for a document."""
    inv = getattr(document, "inventory", None)
    if inv:
        return getattr(inv, "inventory_number", "") or ""
    return ""


def get_start_end_scan_filenames(document):
    """Get the first and last scan filenames for a document, sorted by scan name."""
    pages = getattr(document, "pages", None)
    if not pages:
        return "", ""

    sorted_pages = sorted(
        pages,
        key=lambda p: (
            p.page.scan.filename
            if (
                p
                and getattr(p, "page", None)
                and getattr(p.page, "scan", None)
                and getattr(p.page.scan, "filename", None)
            )
            else ""
        ),
    )
    first_page_link = sorted_pages[0] if sorted_pages else None
    last_page_link = sorted_pages[-1] if sorted_pages else None

    start_scan_filename = ""
    end_scan_filename = ""

    if (
        first_page_link
        and getattr(first_page_link, "page", None)
        and getattr(first_page_link.page, "scan", None)
        and getattr(first_page_link.page.scan, "filename", None)
    ):
        start_scan_filename = first_page_link.page.scan.filename or ""

    if (
        last_page_link
        and getattr(last_page_link, "page", None)
        and getattr(last_page_link.page, "scan", None)
        and getattr(last_page_link.page.scan, "filename", None)
    ):
        end_scan_filename = last_page_link.page.scan.filename or ""

    return start_scan_filename, end_scan_filename


def get_ordered_page_links(document):
    """Get document page links sorted by scan filename."""
    pages = getattr(document, "pages", None)
    if not pages:
        return []

    return sorted(
        pages,
        key=lambda p: (
            p.page.scan.filename
            if (
                p
                and getattr(p, "page", None)
                and getattr(p.page, "scan", None)
                and getattr(p.page.scan, "filename", None)
            )
            else ""
        ),
    )


def get_start_end_page_links(document):
    """Get the first and last page links for a document, sorted by scan filename."""
    sorted_pages = get_ordered_page_links(document)
    if not sorted_pages:
        return None, None

    return sorted_pages[0], sorted_pages[-1]


def get_start_end_scan_types(document):
    """Get the scan types of the first and last scans for a document."""
    first_page_link, last_page_link = get_start_end_page_links(document)
    if not first_page_link or not last_page_link:
        return "", ""

    start_scan_type = ""
    end_scan_type = ""

    if (
        first_page_link
        and getattr(first_page_link, "page", None)
        and getattr(first_page_link.page, "scan", None)
    ):
        scan_type = getattr(first_page_link.page.scan, "scan_type", None)
        if scan_type:
            start_scan_type = (
                scan_type.value if hasattr(scan_type, "value") else str(scan_type)
            )

    if (
        last_page_link
        and getattr(last_page_link, "page", None)
        and getattr(last_page_link.page, "scan", None)
    ):
        scan_type = getattr(last_page_link.page.scan, "scan_type", None)
        if scan_type:
            end_scan_type = (
                scan_type.value if hasattr(scan_type, "value") else str(scan_type)
            )

    return start_scan_type, end_scan_type


def get_date_start(document):
    """Get the document's earliest begin date in ISO 8601 format."""
    if document.date_earliest_begin:
        return document.date_earliest_begin.isoformat()
    return ""


def get_date_latest_begin(document):
    """Get the document's latest begin date in ISO 8601 format."""
    if document.date_latest_begin:
        return document.date_latest_begin.isoformat()
    return ""


def get_date_earliest_end(document):
    """Get the document's earliest end date in ISO 8601 format."""
    if document.date_earliest_end:
        return document.date_earliest_end.isoformat()
    return ""


def get_date_end(document):
    """Get the document's latest end date in ISO 8601 format."""
    if document.date_latest_end:
        return document.date_latest_end.isoformat()
    return ""


def get_identification_method(document):
    """Get the identification method name."""
    method = getattr(document, "method", None)
    if method:
        return getattr(method, "name", "") or ""
    return ""


def get_document_type_uuids(document):
    """Get a comma-separated list of linked document type UUIDs."""
    types_linked = getattr(document, "document_types_linked", None)
    if not types_linked:
        return ""

    type_ids = sorted(
        f"{TYPE_URI_PREFIX}{link.document_type.id}"
        for link in types_linked
        if link
        and getattr(link, "document_type", None)
        and getattr(link.document_type, "id", None)
    )
    return ",".join(type_ids)


def export_documents_csv(output_dir, gzipped, s3_client, s3_config):
    """Export all documents to CSV, either to disk or directly to S3.

    Args:
        output_dir: Base local output directory (or S3 key prefix root).
        gzipped: When True, gzip-compress the output payload.
        s3_client: Initialized Boto3 S3 client, or None to write to disk.
        s3_config: S3UploadConfig instance, or None to write to disk.
    """
    with Session(engine) as session:
        # Query all documents
        documents = session.query(Document).all()

        logger.info(f"Found {len(documents)} documents to export")

        buffer = io.StringIO()
        writer = csv.writer(buffer)

        # Write header
        writer.writerow(
            [
                "identifier",
                "inventory_number",
                "type_uuids",
                "start_scan_filename",
                "end_scan_filename",
                "start_scan_type",
                "end_scan_type",
                "title",
                "date_earliest_begin",
                "date_latest_begin",
                "date_earliest_end",
                "date_latest_end",
                "settlement",
                "settlement_id",
                "method",
            ]
        )

        # Write data rows
        for document in tqdm(
            documents, desc="Exporting documents to CSV", unit="document"
        ):
            start_scan_filename, end_scan_filename = get_start_end_scan_filenames(
                document
            )
            start_scan_type, end_scan_type = get_start_end_scan_types(document)
            settlement_id, settlement_label = get_settlement(document)
            writer.writerow(
                [
                    document.id,
                    get_inventory_number(document),
                    get_document_type_uuids(document),
                    start_scan_filename,
                    end_scan_filename,
                    start_scan_type,
                    end_scan_type,
                    document.title or "",
                    get_date_start(document),
                    get_date_latest_begin(document),
                    get_date_earliest_end(document),
                    get_date_end(document),
                    settlement_label,
                    settlement_id,
                    get_identification_method(document),
                ]
            )

        payload = buffer.getvalue().encode("utf-8")
        if gzipped:
            payload = gzip.compress(payload)

        output_bytes(
            payload,
            "documents.csv",
            output_dir,
            gzipped,
            s3_client,
            s3_config,
            content_type="text/csv; charset=utf-8",
        )

        logger.info(
            f"Exported {len(documents)} documents to {output_dir}/documents.csv"
        )


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Export documents to a CSV file.")
    parser.add_argument(
        "output_dir",
        nargs="?",
        default=os.environ.get("DOCUMENTS_CSV_OUTPUT_DIR", "data/output/s3/document"),
        help="Base local output directory (default: data/output/s3/document, or DOCUMENTS_CSV_OUTPUT_DIR env var)",
    )
    parser.add_argument(
        "--gzipped", action="store_true", help="Gzip-compress the output file"
    )
    add_s3_arguments(parser)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    s3_config = build_s3_upload_config(args)
    s3_client = create_s3_client(s3_config) if s3_config else None
    export_documents_csv(args.output_dir, args.gzipped, s3_client, s3_config)
