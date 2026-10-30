"""Export per-document collections of classified annotation mentions."""

import logging
import os
from itertools import batched, groupby
from typing import Any, Callable, Iterable

import duckdb

from models import get_document_id

BASE_URI = "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722"
GLOBALISE_CONTEXT = "https://objectstore.surf.nl/87435b768620494e8e911c83d1997f24:globalise-data/contexts/globalise.json"
ANNO_CONTEXT = "http://www.w3.org/ns/anno.jsonld"
LDP_CONTEXT = "http://www.w3.org/ns/ldp.jsonld"
DEFAULT_INVENTORY_PARQUET = os.environ.get(
    "INVENTORY_PARQUET",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data",
        "input",
        "inventory_data.parquet",
    ),
)

# Labels are the classified_as values in inventory_data.parquet.
NER_CATEGORIES = (
    ("PER_NAME", "Name of Person"),
    ("PRF", "Profession, title"),
    ("STATUS", "(Civic) status"),
    ("PER_ATTR", "Other persons attributes (than PER or STATUS)"),
    ("LOC_NAME", "Name of Location"),
    ("LOC_ADJ", "Derived (adjectival) form of location name"),
    (
        "ETH_REL",
        "Ethno-religious appellation or attribute, not derived from location name",
    ),
    ("CMTY_NAME", "Name of Commodity"),
    ("CMTY_QUAL", "Commodity qualifier: colors, processing"),
    ("CMTY_QUANT", "Quantity"),
    ("SHIP", "Ship name"),
    ("SHIP_TYPE", "Ship type"),
    ("ORG", "Organisation name"),
    ("DATE", "Date"),
    ("DOC", "Document"),
)
CATEGORY_BY_LABEL = {label: tag for tag, label in NER_CATEGORIES}
CATEGORY_BY_LABEL["EVENT"] = "EVENT"
COLLECTION_TYPES = ["DigitalObject", "BasicContainer", "AnnotationCollection"]
PAGE_SIZE = 1000
logger = logging.getLogger(__name__)


def _document_scans(document: Any) -> list[str]:
    links = sorted(
        getattr(document, "pages", None) or [],
        key=lambda link: (
            getattr(link, "index", None) is None,
            getattr(link, "index", 0) or 0,
        ),
    )
    filenames: list[str] = []
    seen: set[str] = set()
    for link in links:
        page = getattr(link, "page", None)
        scan = getattr(page, "scan", None)
        filename = getattr(scan, "filename", None)
        if filename and filename not in seen:
            filenames.append(filename)
            seen.add(filename)
    return filenames


def _rows(cursor: Any) -> Iterable[tuple]:
    while batch := cursor.fetchmany(10000):
        yield from batch


def export_document_annotations(
    documents: list,
    emit: Callable[[dict, str], None],
    parquet_path: str = DEFAULT_INVENTORY_PARQUET,
    page_size: int = PAGE_SIZE,
) -> None:
    """Read the inventory once and emit sets, collections, and pages for documents."""
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    if not documents:
        return
    if not os.path.isfile(parquet_path):
        raise FileNotFoundError(
            f"Annotation inventory parquet not found: {parquet_path}"
        )

    conn = duckdb.connect()
    try:
        conn.execute(
            "CREATE TEMP TABLE document_scans (doc_index INTEGER, scan_index INTEGER, filename VARCHAR)"
        )
        scan_rows = (
            (doc_index, scan_index, filename)
            for doc_index, doc in enumerate(documents)
            for scan_index, filename in enumerate(_document_scans(doc))
        )
        for batch in batched(scan_rows, 10000):
            conn.execute(
                """
                INSERT INTO document_scans
                SELECT unnest(?::INTEGER[]), unnest(?::INTEGER[]), unnest(?::VARCHAR[])
                """,
                [
                    [row[0] for row in batch],
                    [row[1] for row in batch],
                    [row[2] for row in batch],
                ],
            )
        cursor = conn.execute(
            """
            SELECT s.doc_index, s.filename, a.annotation_id, a.classified_as
            FROM read_parquet(?) AS a
            JOIN document_scans AS s
              ON s.filename = regexp_extract(
                  a.annotation_id, '/annotations:(?:entities|events):([^#]+)#', 1
              )
            WHERE a.annotation_id IS NOT NULL
            ORDER BY s.doc_index, s.scan_index, a.offset_scan NULLS LAST, a.annotation_id
            """,
            [parquet_path],
        )
        groups = iter(groupby(_rows(cursor), key=lambda row: row[0]))
        current = next(groups, None)
        unknown_labels: set[str] = set()
        for doc_index, document in enumerate(documents):
            mentions: dict[str, list[tuple[str, str]]] = {
                tag: [] for tag, _ in NER_CATEGORIES
            }
            mentions["EVENT"] = []
            seen: set[tuple[str, str]] = set()
            if current is not None and current[0] == doc_index:
                for _, scan, annotation_id, label in current[1]:
                    tag = CATEGORY_BY_LABEL.get(label)
                    if tag is None:
                        unknown_labels.add(str(label))
                        continue
                    key = (tag, annotation_id)
                    if key not in seen:
                        seen.add(key)
                        mentions[tag].append((annotation_id, scan))
                current = next(groups, None)
            _emit_document_collections(document, mentions, emit, page_size)
        if unknown_labels:
            logger.warning(
                "Skipped unrecognized annotation classifications: %s",
                ", ".join(sorted(unknown_labels)),
            )
    finally:
        conn.close()


def _emit_document_collections(
    document: Any,
    mentions: dict[str, list[tuple[str, str]]],
    emit: Callable[[dict, str], None],
    page_size: int,
) -> None:
    doc_id = get_document_id(document)
    set_uri = f"{BASE_URI}/document:{doc_id}.annotations"
    categories = [
        (tag, f"Document mentions of {label} ({tag})") for tag, label in NER_CATEGORIES
    ]
    categories.append(("EVENT", "Document mentions of all Event tags"))
    members = []
    inventory = document.inventory
    for tag, label in categories:
        suffix = "events" if tag == "EVENT" else tag.lower()
        collection_uri = f"{set_uri}.{suffix}"
        relative = os.path.join("document", f"{doc_id}.annotations.{suffix}")
        items = mentions[tag]
        page_count = max(1, (len(items) + page_size - 1) // page_size)
        members.append(
            {"id": collection_uri, "type": COLLECTION_TYPES, "_label": label}
        )
        emit(
            {
                "@context": [GLOBALISE_CONTEXT, ANNO_CONTEXT, LDP_CONTEXT],
                "id": collection_uri,
                "type": COLLECTION_TYPES,
                "_label": label,
                "total": len(items),
                "first": f"{collection_uri}/page-1.json",
                "last": f"{collection_uri}/page-{page_count}.json",
            },
            f"{relative}.json",
        )
        for page_number in range(1, page_count + 1):
            page = {
                "@context": [GLOBALISE_CONTEXT, ANNO_CONTEXT],
                "id": f"{collection_uri}/page-{page_number}.json",
                "type": ["DigitalObject", "AnnotationPage"],
                "partOf": collection_uri,
                "items": [
                    {
                        "id": annotation_id,
                        "type": "Annotation",
                        "partOf": {
                            "id": f"{BASE_URI}/canvas:{scan}",
                            "type": "Canvas",
                            "partOf": {
                                "id": f"{BASE_URI}/inventory:{inventory.inventory_number}.manifest",
                                "type": "Manifest",
                            },
                        },
                    }
                    for annotation_id, scan in items[
                        (page_number - 1) * page_size : page_number * page_size
                    ]
                ],
            }
            if page_number > 1:
                page["previous"] = f"{collection_uri}/page-{page_number - 1}.json"
            if page_number < page_count:
                page["next"] = f"{collection_uri}/page-{page_number + 1}.json"
            emit(page, os.path.join(relative, f"page-{page_number}.json"))
    emit(
        {
            "@context": [GLOBALISE_CONTEXT, ANNO_CONTEXT, LDP_CONTEXT],
            "id": set_uri,
            "type": "Set",
            "_label": "Document mentions of all NER categories",
            "member": members,
        },
        os.path.join("document", f"{doc_id}.annotations.json"),
    )
