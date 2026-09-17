"""
Export helpers: JSON/JSON-LD serializers for GLOBALISE entities.
Moved out of app.py to keep routes lean.
"""

import os
import sys
from functools import lru_cache
from typing import Any, Dict, List, Optional

import duckdb

_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

from models import Document, Inventory, RectoVerso
from utils import (
    get_canvas_uri_from_annotation_id,
    get_manifest_uri_from_annotation_id,
)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_LINKS_PARQUET = os.environ.get(
    "LINKS_PARQUET",
    os.path.join(ROOT_DIR, "data", "input", "links_data.parquet"),
)


@lru_cache(maxsize=4)
def load_scan_entities_from_parquet(
    parquet_path: str = DEFAULT_LINKS_PARQUET,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Load linked entities grouped by scan filename from the corpus links parquet file.

    Args:
        parquet_path: Path to the links-data parquet file.

    Returns:
        Mapping of scan filename to list of entity dicts containing:
    Examples:
        >>> import tempfile
        >>> from pathlib import Path
        >>> import duckdb
        >>> with tempfile.TemporaryDirectory() as directory:
        ...     parquet_path = Path(directory) / "links.parquet"
        ...     _ = duckdb.execute(
        ...         f"COPY (SELECT * FROM (VALUES "
        ...         f"('https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/annotations:entities:NL-HaNA_1.04.02_1053_0001#annotation:1', 'https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/person:123', 'Person', 'Anna Bijns'), "
        ...         f"('https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/annotations:entities:NL-HaNA_1.04.02_1053_0001#annotation:2', 'https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/person:123', 'Person', 'Anna Bijns') "
        ...         f") AS t(annotation_id, entity_uri, entity_type, entity_label)) TO '{parquet_path}' (FORMAT PARQUET)"
        ...     )
        ...     res = load_scan_entities_from_parquet(str(parquet_path))
        ...     len(res["NL-HaNA_1.04.02_1053_0001"])
        2
    """
    if not os.path.isfile(parquet_path):
        return {}

    conn = duckdb.connect()
    try:
        query = """
            SELECT
                split_part(split_part(annotation_id, '#', 1), 'annotations:entities:', 2) AS scan_filename,
                entity_uri,
                entity_type,
                entity_label,
                annotation_id
            FROM read_parquet(?)
            WHERE entity_uri IS NOT NULL AND entity_uri LIKE 'http%'
            ORDER BY annotation_id
        """
        rows = conn.execute(query, [parquet_path]).fetchall()
    finally:
        conn.close()

    scan_to_entities: Dict[str, List[Dict[str, Any]]] = {}
    for scan_fn, entity_uri, entity_type, entity_label, annotation_id in rows:
        if not scan_fn:
            continue
        scan_to_entities.setdefault(scan_fn, []).append(
            {
                "entity_uri": entity_uri,
                "entity_type": entity_type or "Type",
                "entity_label": entity_label or entity_uri,
                "annotation_id": annotation_id,
            }
        )
    return scan_to_entities


def get_annotationcollections_for_inventory(
    inventory: Inventory,
) -> List[Dict[str, Any]]:
    """
    Return a list of Linked Art AnnotationCollection objects for the given inventory (CuratedHolding).
    """

    collections: List[Dict[str, Any]] = []
    if not inventory:
        return collections

    # Collect distinct scans for this inventory in filename order
    scans = []
    if getattr(inventory, "scans", None):
        scans = sorted(inventory.scans, key=lambda s: s.filename or "")

    transcription_pages: List[Dict[str, Any]] = []
    entity_pages: List[Dict[str, Any]] = []
    event_pages: List[Dict[str, Any]] = []

    for scan in scans:
        if getattr(scan, "has_transcriptions", False):
            transcription_pages.append(
                {
                    "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/annotations:transcriptions:{scan.filename}",
                    "type": "AnnotationPage",
                    "label": {"en": [f"Transcriptions of scan {scan.filename}"]},
                }
            )
        if getattr(scan, "has_entities", False):
            entity_pages.append(
                {
                    "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/annotations:entities:{scan.filename}",
                    "type": "AnnotationPage",
                    "label": {"en": [f"Entities identified on scan {scan.filename}"]},
                }
            )
        if getattr(scan, "has_events", False):
            event_pages.append(
                {
                    "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/annotations:events:{scan.filename}",
                    "type": "AnnotationPage",
                    "label": {"en": [f"Events identified on scan {scan.filename}"]},
                }
            )

    # Transcriptions
    annotation_collection_transcription = {
        "@context": [
            "http://www.w3.org/ns/anno.jsonld",
            "http://www.w3.org/ns/ldp.jsonld",
        ],
        "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/inventory:{inventory.inventory_number}.annotations.transcriptions",
        "type": ["BasicContainer", "AnnotationCollection"],
        "label": "Transcriptions of the inventory",
        "total": len(
            transcription_pages
        ),  # TODO: count of all annotations instead of AnnotationPages
        "first": transcription_pages[0] if transcription_pages else None,
        "last": transcription_pages[-1] if transcription_pages else None,
    }
    collections.append(annotation_collection_transcription)

    # Entities
    annotation_collection_entities = {
        "@context": [
            "http://www.w3.org/ns/anno.jsonld",
            "http://www.w3.org/ns/ldp.jsonld",
        ],
        "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/inventory:{inventory.inventory_number}.annotations.entities",
        "type": ["BasicContainer", "AnnotationCollection"],
        "label": "Entities in the inventory",
        "total": len(
            entity_pages
        ),  # TODO: count of all annotations instead of AnnotationPages
        "first": entity_pages[0] if entity_pages else None,
        "last": entity_pages[-1] if entity_pages else None,
    }
    collections.append(annotation_collection_entities)

    # Events
    annotation_collection_events = {
        "@context": [
            "http://www.w3.org/ns/anno.jsonld",
            "http://www.w3.org/ns/ldp.jsonld",
        ],
        "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/inventory:{inventory.inventory_number}.annotations.events",
        "type": ["BasicContainer", "AnnotationCollection"],
        "label": "Events in the inventory",
        "total": len(
            event_pages
        ),  # TODO: count of all annotations instead of AnnotationPages
        "first": event_pages[0] if event_pages else None,
        "last": event_pages[-1] if event_pages else None,
    }
    collections.append(annotation_collection_events)

    return collections


def inventory_to_annotations_jsonld(inventory: Inventory) -> Dict[str, Any]:
    """
    Serialize the annotations for an inventory (.annotations) as a Linked Art Set containing its AnnotationCollections.
    """

    collections = (
        get_annotationcollections_for_inventory(inventory) if inventory else []
    )
    return {
        "@context": "https://linked.art/ns/v1/linked-art.json",
        "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/inventory:{inventory.inventory_number}.annotations",
        "type": "Set",
        "_label": f"Annotations for Inventory {inventory.inventory_number}",
        "member": collections,
    }


# Helper to serialize a Scan to JSON-LD (preliminary, can be extended)
# Some fields are placeholders or require further mapping


def scan_to_jsonld(scan) -> Dict[str, Any]:
    return {
        # "@context": "https://linked.art/ns/v1/linked-art.json",
        # "id": f"urn:uuid:{scan.id}",
        "type": "DigitalObject",
        "_label": f"Scan {scan.filename}",
        "classified_as": [
            {
                "id": "http://vocab.getty.edu/aat/300417380",
                "type": "Type",
                "_label": "Digitized image",
            }
        ],
        "identified_by": [
            {
                "type": "Identifier",
                "classified_as": {
                    "id": "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/thesaurus:cae829ae-d2ed-4970-99f6-ceb987a4a1fb",
                    "type": "Type",
                    "_label": "National Archives image identifier",
                },
                "content": scan.na_identifier or "",
            },
            # {
            #     "type": "Identifier",
            #     "classified_as": None,  # TODO: map if needed
            #     "content": scan.id,
            # },
        ],
        "dimension": [
            {
                "type": "Dimension",
                "classified_as": {
                    "id": "http://vocab.getty.edu/aat/300055644",
                    "type": "Type",
                    "_label": "Height",
                },
                "value": scan.height,
                "unit": {
                    "id": "http://vocab.getty.edu/aat/300266190",
                    "type": "MeasurementUnit",
                    "_label": "pixels",
                },
            },
            {
                "type": "Dimension",
                "classified_as": {
                    "id": "http://vocab.getty.edu/aat/300055647",
                    "type": "Type",
                    "_label": "Width",
                },
                "value": scan.width,
                "unit": {
                    "id": "http://vocab.getty.edu/aat/300266190",
                    "type": "MeasurementUnit",
                    "_label": "pixels",
                },
            },
        ],
        "access_point": {
            "id": scan.get_image_url(size="max") or "",
            "type": "DigitalObject",
        },
        "format": "image/jpeg",
        # 'digitally_carries' and 'digitally_shows' are placeholders for now
        # "digitally_carries": None,
        # "digitally_shows": None,
    }


def settlement_to_place_jsonld(settlement) -> Dict[str, Any]:
    """
    Serialize a Settlement to a Linked Art Place object.
    """
    labels = []
    if getattr(settlement, "labels", None):
        labels = [
            lbl.label.strip()
            for lbl in settlement.labels
            if getattr(lbl, "label", None) and lbl.label.strip()
        ]

    deduped_labels = list(dict.fromkeys(labels))
    place_label = deduped_labels[0] if deduped_labels else settlement.glob_id

    place_obj: Dict[str, Any] = {
        "type": "Place",
        "_label": place_label,
    }

    if getattr(settlement, "glob_id", None):
        place_obj["id"] = (
            "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/"
            f"place:{settlement.glob_id}"
        )

    if deduped_labels:
        place_obj["identified_by"] = [
            {"type": "Name", "content": label} for label in deduped_labels
        ]

    return place_obj


# ISO 639-3 codes found in data/language_data_per_scan.parquet, mapped to the
# Linked Art recommended language vocabulary
# (https://linked.art/model/vocab/recommended/languages/), which is keyed by
# ISO 639-1. Codes with no ISO 639-1 equivalent (and therefore no AAT id in
# that vocabulary) keep a label only.
LANGUAGE_MAP: Dict[str, Dict[str, Optional[str]]] = {
    "nld": {"label": "Dutch", "aat": "300388256"},
    "fra": {"label": "French", "aat": "300388306"},
    "eng": {"label": "English", "aat": "300388277"},
    "lat": {"label": "Latin", "aat": "300388693"},
    "por": {"label": "Portuguese", "aat": "300389115"},
    "msa": {"label": "Malay", "aat": "300388786"},
    "sin": {"label": "Sinhala", "aat": "300389279"},
    "deu": {"label": "German", "aat": "300388344"},
    "spa": {"label": "Spanish", "aat": "300389311"},
    "dan": {"label": "Danish", "aat": "300388204"},
    "ita": {"label": "Italian", "aat": "300388474"},
    "tam": {"label": "Tamil", "aat": "300389365"},
    "fas": {"label": "Persian", "aat": "300389087"},
    "jpn": {"label": "Japanese", "aat": "300388486"},
    "ben": {"label": "Bengali", "aat": "300387971"},
    "guj": {"label": "Gujarati", "aat": "300388371"},
    "heb": {"label": "Hebrew", "aat": "300388401"},
    "lzh": {
        "label": "Literary Chinese",
        "aat": None,
    },  # TODO: map to AAT id if available
    "chu": {"label": "Church Slavic", "aat": "300389289"},
    "bug": {"label": "Buginese", "aat": None},  # TODO: map to AAT id if available
    "grc": {"label": "Ancient Greek", "aat": "300387827"},
    "art": {"label": "Artificial language (unspecified)", "aat": "300389747"},
}


def language_code_to_jsonld(code: str) -> Optional[Dict[str, Any]]:
    """
    Map a single ISO 639-3 code to a Linked Art Language object.

    Returns None for empty/unknown codes (i.e. no language detected).
    """
    code = (code or "").strip().lower()
    if not code or code == "unknown":
        return None

    info = LANGUAGE_MAP.get(code)
    if info is None:
        # Unrecognized/unmapped code: keep it visible without a resolved id.
        return {"type": "Language", "_label": code}

    lang_obj: Dict[str, Any] = {"type": "Language", "_label": info["label"]}
    if info["aat"]:
        lang_obj["id"] = f"http://vocab.getty.edu/aat/{info['aat']}"
    return lang_obj


def scan_languages_jsonld(scan) -> List[Dict[str, Any]]:
    """
    Map a single Scan's raw `languages` field to a list of known Language objects.
    """

    raw = getattr(scan, "languages", None) if scan is not None else None
    if not raw:
        return []

    languages: List[Dict[str, Any]] = []
    seen = set()
    for code in raw.split(","):
        code = code.strip().lower()
        if not code or code in seen:
            continue
        seen.add(code)
        lang = language_code_to_jsonld(code)
        if lang is not None:
            languages.append(lang)
    return languages


def document_languages_jsonld(document) -> List[Dict[str, Any]]:
    """
    Collect the distinct known languages across all scans linked to a document's pages.
    """

    languages: List[Dict[str, Any]] = []
    seen = set()
    for link in getattr(document, "pages", None) or []:
        pg = getattr(link, "page", None) if link else None
        scan = getattr(pg, "scan", None) if pg else None
        for lang in scan_languages_jsonld(scan):
            key = lang.get("id") or lang.get("_label")
            if key in seen:
                continue
            seen.add(key)
            languages.append(lang)
    return languages


def document_classifications_jsonld(document) -> List[Dict[str, Any]]:
    """
    Generate Linked Art Type classifications from document_types_linked.

    Examples:
        >>> from types import SimpleNamespace
        >>> dt = SimpleNamespace(id="uuid-1", pref_label_en="Letter", pref_label_nl="Brief")
        >>> doc = SimpleNamespace(document_types_linked=[SimpleNamespace(document_type=dt)])
        >>> document_classifications_jsonld(doc)
        [{'id': 'https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/thesaurus:uuid-1', 'type': 'Type', '_label': 'Letter (document type)', 'prefLabel': [{'@language': 'nl', '@value': 'Brief'}, {'@language': 'en', '@value': 'Letter'}]}]
        >>> document_classifications_jsonld(SimpleNamespace(document_types_linked=[]))
        []
    """
    classified = []
    if getattr(document, "document_types_linked", None):
        for dt_link in document.document_types_linked:
            dt = getattr(dt_link, "document_type", None)
            if dt is None:
                continue
            doc_type_str = dt.pref_label_en or dt.pref_label_nl or dt.id
            pref_label = []
            if dt.pref_label_nl:
                pref_label.append({"@language": "nl", "@value": dt.pref_label_nl})
            if dt.pref_label_en:
                pref_label.append({"@language": "en", "@value": dt.pref_label_en})
            classified.append(
                {
                    "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/thesaurus:{dt.id}",
                    "type": "Type",
                    "_label": f"{doc_type_str} (document type)",
                    "prefLabel": pref_label,
                }
            )
    return classified


def document_title_jsonld(document) -> Optional[Dict[str, Any]]:
    """
    Generate Linked Art Title object for a document.

    Examples:
        >>> from types import SimpleNamespace
        >>> document_title_jsonld(SimpleNamespace(title="Missive"))
        {'type': 'Title', 'content': 'Missive', '_label': 'Title of the document'}
        >>> document_title_jsonld(SimpleNamespace(title=None)) is None
        True
    """

    if getattr(document, "title", None):
        return {
            "type": "Title",
            "content": document.title,
            "_label": "Title of the document",
        }
    return None


def document_timespan_jsonld(document) -> Optional[Dict[str, Any]]:
    """
    Generate Linked Art Timespan object for a document.

    Examples:
        >>> from types import SimpleNamespace
        >>> from datetime import date
        >>> doc = SimpleNamespace(
        ...     date_earliest_begin=date(1650, 1, 1),
        ...     date_latest_begin=date(1650, 1, 5),
        ...     date_earliest_end=date(1650, 2, 1),
        ...     date_latest_end=date(1650, 2, 10),
        ...     date_text="1-5 Jan to 1-10 Feb 1650",
        ... )
        >>> ts = document_timespan_jsonld(doc)
        >>> ts["type"]
        'Timespan'
        >>> ts["begin_of_the_begin"]
        '1650-01-01T00:00:00'
        >>> ts["end_of_the_end"]
        '1650-02-10T23:59:59'
        >>> ts["name"]["content"]
        '1-5 Jan to 1-10 Feb 1650'
        >>> document_timespan_jsonld(SimpleNamespace(date_earliest_begin=None, date_latest_end=None)) is None
        True
    """

    if (getattr(document, "date_earliest_begin", None) is None) and (
        getattr(document, "date_latest_end", None) is None
    ):
        return None

    timespan: Dict[str, Any] = {
        "type": "Timespan",
        "begin_of_the_begin": (
            str(document.date_earliest_begin) + "T00:00:00"
            if getattr(document, "date_earliest_begin", None) is not None
            else None
        ),
        "end_of_the_begin": (
            str(document.date_latest_begin) + "T23:59:59"
            if getattr(document, "date_latest_begin", None) is not None
            else None
        ),
        "begin_of_the_end": (
            str(document.date_earliest_end) + "T00:00:00"
            if getattr(document, "date_earliest_end", None) is not None
            else None
        ),
        "end_of_the_end": (
            str(document.date_latest_end) + "T23:59:59"
            if getattr(document, "date_latest_end", None) is not None
            else None
        ),
    }
    if getattr(document, "date_text", None):
        timespan["name"] = {
            "type": "Name",
            "content": document.date_text,
            "_label": document.date_text,
        }
    return timespan


def document_parts_jsonld(document) -> List[Dict[str, Any]]:
    """
    Generate physical page parts (recto/verso) for a document.

    Examples:
        >>> from types import SimpleNamespace
        >>> from models import RectoVerso
        >>> scan = SimpleNamespace(filename="NL-HaNA_1.04.02_1053_0001", na_identifier="na-1", width=1000, height=2000, languages=None, get_image_url=lambda size="max": "https://example.org/image.jpg")
        >>> page = SimpleNamespace(id="page-1", page_or_folio_number="12", recto_verso=RectoVerso.RECTO, scan=scan)
        >>> doc = SimpleNamespace(pages=[SimpleNamespace(index=0, page=page)])
        >>> parts = document_parts_jsonld(doc)
        >>> len(parts)
        1
        >>> parts[0]["_label"]
        'Fol. 12r'
        >>> parts[0]["classified_as"]["_label"]
        'Recto'
        >>> document_parts_jsonld(SimpleNamespace(pages=[]))
        []
    """
    parts: List[Dict[str, Any]] = []
    if not getattr(document, "pages", None):
        return parts

    sorted_page_links = sorted(
        document.pages,
        key=lambda p: (p.index if (p and getattr(p, "index", None) is not None) else 0),
    )
    for link in sorted_page_links:
        pg = getattr(link, "page", None)
        if pg is None:
            continue
        label: Optional[str]
        if pg.page_or_folio_number and pg.recto_verso:
            suffix = "r" if pg.recto_verso == RectoVerso.RECTO else "v"
            label = f"Fol. {pg.page_or_folio_number}{suffix}"
        elif pg.page_or_folio_number:
            label = f"Page {pg.page_or_folio_number}"
        else:
            label = f"Physical Page {pg.id[:8]}"

        # Determine recto/verso classification
        if pg.recto_verso == RectoVerso.RECTO:
            classification_id = "http://vocab.getty.edu/aat/300078817"  # Recto
            recto_verso_label = "Recto"
        elif pg.recto_verso == RectoVerso.VERSO:
            classification_id = "http://vocab.getty.edu/aat/300010292"  # Verso
            recto_verso_label = "Verso"
        else:
            classification_id = "http://vocab.getty.edu/aat/300241583"  # Generic part
            recto_verso_label = "Page"

        # Build scan reference if available
        scan_ref = None
        if getattr(pg, "scan", None):
            scan_ref = scan_to_jsonld(pg.scan)

        part: Dict[str, Any] = {
            "type": "PhysicalHumanMadeThing",
            "_label": label,
            "classified_as": {
                "id": classification_id,
                "type": "Type",
                "_label": recto_verso_label,
                "classified_as": [
                    {
                        "id": "http://vocab.getty.edu/aat/300241583",
                        "type": "Type",
                        "_label": "Part Type",
                    }
                ],
            },
            "carries": {
                "type": "LinguisticObject",
                "_label": "Textual content of the page",
                "language": scan_languages_jsonld(pg.scan) or None,
                "digitally_carried_by": {
                    "type": "DigitalObject",
                    "_label": "PageXML + Plain Text",
                },
            },
            "shows": {
                "type": "VisualItem",
                "_label": "Visual depiction of the page.",
                "digitally_shown_by": scan_ref,
            },
        }
        parts.append(part)

    return parts


def document_identified_by_jsonld(document) -> List[Dict[str, Any]]:
    """
    Generate external identifier objects (TANAP, OBP_INDEX, etc.).

    Examples:
        >>> from types import SimpleNamespace
        >>> ext_tanap = SimpleNamespace(identifier="TANAP-123", URL=None, context="TANAP database")
        >>> ext_obp = SimpleNamespace(identifier="OBP-456", URL=None, context="OBP_INDEX")
        >>> doc = SimpleNamespace(external_ids=[
        ...     SimpleNamespace(external=ext_tanap),
        ...     SimpleNamespace(external=ext_obp),
        ... ])
        >>> ids = document_identified_by_jsonld(doc)
        >>> len(ids)
        2
        >>> ids[0]["content"]
        'TANAP-123'
        >>> ids[0]["classified_as"]["_label"]
        'TANAP Identifier'
        >>> ids[1]["content"]
        'OBP-456'
        >>> ids[1]["classified_as"]["_label"]
        'OBP_INDEX Identifier'
        >>> document_identified_by_jsonld(SimpleNamespace(external_ids=[]))
        []
    """
    identified_by = []
    for ext_link in getattr(document, "external_ids", None) or []:
        external = getattr(ext_link, "external", None)
        if external is None:
            continue

        external_context = getattr(external, "context", None) or ""
        if "TANAP" in external_context:
            identifier_type = "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/thesaurus:5874f9e1-5645-4b4c-a61b-cc17ebde93a0"
            identifier_type_label = "TANAP Identifier"
        elif "OBP_INDEX" in external_context:
            identifier_type = "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/thesaurus:2122ddb6-c61f-4288-a592-4970bf42fc13"
            identifier_type_label = "OBP_INDEX Identifier"
        else:
            identifier_type = None
            identifier_type_label = None

        ident_obj: Dict[str, Any] = {
            "type": "Identifier",
            "content": external.identifier or external.URL or "",
        }
        if identifier_type:
            ident_obj["classified_as"] = {
                "id": identifier_type,
                "type": "Type",
                "_label": identifier_type_label,
            }
        identified_by.append(ident_obj)
    return identified_by


def document_dimensions_jsonld(document) -> List[Dict[str, Any]]:
    """
    Generate page count and scan count dimensions for a document.

    Examples:
        >>> from types import SimpleNamespace
        >>> page1 = SimpleNamespace(scan_id="scan-1")
        >>> page2 = SimpleNamespace(scan_id="scan-1")
        >>> doc = SimpleNamespace(pages=[
        ...     SimpleNamespace(page=page1),
        ...     SimpleNamespace(page=page2),
        ... ])
        >>> dims = document_dimensions_jsonld(doc)
        >>> len(dims)
        2
        >>> dims[0]["value"]
        2
        >>> dims[0]["unit"]["_label"]
        'Pages'
        >>> dims[1]["value"]
        1
        >>> dims[1]["unit"]["_label"]
        'Digitized images'
    """
    number_of_pages = len(document.pages) if getattr(document, "pages", None) else 0
    scan_ids = {
        link.page.scan_id
        for link in (getattr(document, "pages", None) or [])
        if link and getattr(link, "page", None) and link.page.scan_id
    }
    number_of_scans = len(scan_ids)

    return [
        {
            "type": "Dimension",
            "classified_as": {
                "id": "http://vocab.getty.edu/aat/300404433",
                "type": "Type",
                "_label": "Count of",
            },
            "value": number_of_pages,
            "unit": {
                "id": "http://vocab.getty.edu/aat/300194222",
                "type": "MeasurementUnit",
                "_label": "Pages",
            },
        },
        {
            "type": "Dimension",
            "classified_as": {
                "id": "http://vocab.getty.edu/aat/300404433",
                "type": "Type",
                "_label": "Number of scans",
            },
            "value": number_of_scans,
            "unit": {
                "id": "http://vocab.getty.edu/aat/300417380",
                "type": "MeasurementUnit",
                "_label": "Digitized images",
            },
        },
    ]


def document_entities_to_jsonld(
    document,
    links_parquet: Optional[str | Dict[str, List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    """
    Collect all linked entities and their grouped annotations across the document's scans.

    Args:
        document: SQLAlchemy Document instance.
        links_parquet: Optional pre-loaded scan_entities dict, parquet file path,
            or None to load from DEFAULT_LINKS_PARQUET.

    Returns:
        List of entity dicts with subject_of annotations AnnotationPage.

    Examples:
        >>> from types import SimpleNamespace
        >>> dummy_scan = SimpleNamespace(filename="NL-HaNA_1.04.02_1053_0001")
        >>> dummy_page = SimpleNamespace(scan=dummy_scan)
        >>> dummy_doc = SimpleNamespace(id="doc-123", pages=[SimpleNamespace(page=dummy_page)])
        >>> scan_ents = {
        ...     "NL-HaNA_1.04.02_1053_0001": [
        ...         {
        ...             "entity_uri": "https://example.org/place:1",
        ...             "entity_type": "Place",
        ...             "entity_label": "Batavia",
        ...             "annotation_id": "https://example.org/annotations:entities:NL-HaNA_1.04.02_1053_0001#annotation:1",
        ...         }
        ...     ]
        ... }
        >>> res = document_entities_to_jsonld(dummy_doc, links_parquet=scan_ents)
        >>> len(res)
        1
        >>> res[0]["_label"]
        'Batavia'
        >>> res[0]["subject_of"][0]["type"]
        ['DigitalObject', 'AnnotationPage']
        >>> res[0]["subject_of"][0]["items"][0]["partOf"]["partOf"]["id"]
        'https://example.org/inventory:1053.manifest'
    """

    if isinstance(links_parquet, dict):
        scan_entities = links_parquet
    elif isinstance(links_parquet, str):
        scan_entities = load_scan_entities_from_parquet(links_parquet)
    else:
        scan_entities = load_scan_entities_from_parquet(DEFAULT_LINKS_PARQUET)

    scan_filenames: list[str] = []
    seen_scans: set[str] = set()
    for link in getattr(document, "pages", None) or []:
        page = getattr(link, "page", None)
        scan = getattr(page, "scan", None) if page else None
        filename = getattr(scan, "filename", None) if scan else None
        if filename and filename not in seen_scans:
            seen_scans.add(filename)
            scan_filenames.append(filename)

    entities_map: dict[str, dict[str, Any]] = {}
    for scan_filename in scan_filenames:
        for item in scan_entities.get(scan_filename, []):
            entity_uri = item["entity_uri"]
            if entity_uri not in entities_map:
                entities_map[entity_uri] = {
                    "type": item["entity_type"],
                    "_label": item["entity_label"],
                    "annotation_ids": [],
                    "seen_annotation_ids": set(),
                }
            ann_id = item["annotation_id"]
            if ann_id not in entities_map[entity_uri]["seen_annotation_ids"]:
                entities_map[entity_uri]["seen_annotation_ids"].add(ann_id)
                entities_map[entity_uri]["annotation_ids"].append(ann_id)

    entities_in_document: list[dict[str, Any]] = []
    for entity_uri, entity_info in sorted(entities_map.items(), key=lambda x: x[0]):
        entity_label = entity_info["_label"]
        entity_type = entity_info["type"]
        annotations = [
            {
                "id": ann_id,
                "type": "Annotation",
                "partOf": {
                    "id": get_canvas_uri_from_annotation_id(ann_id),
                    "type": "Canvas",
                    "partOf": {
                        "id": get_manifest_uri_from_annotation_id(ann_id),
                        "type": "Manifest",
                    },
                },
            }
            for ann_id in entity_info["annotation_ids"]
        ]
        entity_id_part = entity_uri.rstrip("/").rsplit("/", 1)[-1]
        entities_in_document.append(
            {
                "id": entity_uri,
                "type": entity_type,
                "_label": entity_label,
                "subject_of": [
                    {
                        "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/document:{document.id}#annotations-{entity_id_part}",
                        "type": ["DigitalObject", "AnnotationPage"],
                        "_label": f"All annotations of entity {entity_label} in document {document.id}",
                        "items": annotations,
                    }
                ],
            }
        )

    return entities_in_document


def document_refers_to_jsonld(
    document,
    links_parquet: Optional[str | Dict[str, List[Dict[str, Any]]]] = None,
) -> Optional[List[Dict[str, Any]]]:
    """
    Build the 'refers_to' Linked Art section containing the Set of entities in this document.
    Returns None if there are no linked entities.

    Args:
        document: SQLAlchemy Document instance.
        links_parquet: Optional pre-loaded scan_entities dict, parquet file path,
            or None to load from DEFAULT_LINKS_PARQUET.

    Returns:
        List containing a single Set object with members if entities exist,
        otherwise None.

    Examples:
        >>> from types import SimpleNamespace
        >>> dummy_doc = SimpleNamespace(id="doc-123", pages=[])
        >>> document_refers_to_jsonld(dummy_doc, links_parquet={}) is None
        True
        >>> dummy_page = SimpleNamespace(scan=SimpleNamespace(filename="scan1.jpg"))
        >>> dummy_doc_with_page = SimpleNamespace(id="doc-123", pages=[SimpleNamespace(page=dummy_page)])
        >>> fake_links = {
        ...     "scan1.jpg": [
        ...         {
        ...             "entity_uri": "http://example.org/person/1",
        ...             "entity_label": "Jan",
        ...             "entity_type": "Person",
        ...             "annotation_id": "https://example.org/annotations:entities:NL-HaNA_1.04.02_1053_0001#ann:1",
        ...         }
        ...     ]
        ... }
        >>> res = document_refers_to_jsonld(dummy_doc_with_page, links_parquet=fake_links)
        >>> res[0]["type"]
        'Set'
        >>> res[0]["_label"]
        'Entities in Document doc-123'
        >>> res[0]["member"][0]["_label"]
        'Jan'
    """
    entities_in_document = document_entities_to_jsonld(document, links_parquet)
    if not entities_in_document:
        return None

    return [
        {
            "type": "Set",
            "_label": f"Entities in Document {document.id}",
            "member": entities_in_document,
        }
    ]


def document_physical_to_jsonld(
    document,
    links_parquet: Optional[str | Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """
    Serialize a physical Document record to Linked Art JSON-LD.

    Args:
        document: SQLAlchemy Document instance.
        links_parquet: Optional pre-loaded scan_entities dict, parquet file path,
            or None to load from DEFAULT_LINKS_PARQUET.

    Returns:
        Linked Art JSON-LD dictionary representing the physical document.

    Examples:
        >>> from types import SimpleNamespace
        >>> doc = SimpleNamespace(
        ...     id="doc-test-1",
        ...     title="Test Document",
        ...     document_types_linked=[],
        ...     external_ids=[],
        ...     pages=[],
        ...     location=None,
        ...     date_earliest_begin=None,
        ...     date_latest_begin=None,
        ...     date_earliest_end=None,
        ...     date_latest_end=None,
        ...     date_text=None,
        ... )
        >>> res = document_physical_to_jsonld(doc, links_parquet={})
        >>> res["type"]
        'PhysicalHumanMadeThing'
        >>> res["_label"]
        'Test Document (Document)'
        >>> "refers_to" in res["carries"]
        False
    """
    base_id = f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/document:{document.id}"
    place = settlement_to_place_jsonld(document.location) if document.location else None
    languages = document_languages_jsonld(document)

    carries: Dict[str, Any] = {
        "type": "LinguisticObject",
        "_label": "Textual content of the document",
        "language": languages if languages else None,
        # "digitally_carried_by": None,
        "referred_to_by": [  # All annotations in the document
            {
                "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/document:{document.id}.annotations",
                "type": "Set",
                "_label": f"Entity and event annotations for Document {document.id}",
            }
        ],
    }
    refers_to = document_refers_to_jsonld(document, links_parquet)
    if refers_to:
        carries["refers_to"] = refers_to

    return {
        "@context": "https://linked.art/ns/v1/linked-art.json",
        "id": base_id,
        "type": "PhysicalHumanMadeThing",
        "_label": f"{document.title or document.id} (Document)",
        "classified_as": document_classifications_jsonld(document),
        "title": document_title_jsonld(document),
        "identified_by": document_identified_by_jsonld(document),
        "dimension": document_dimensions_jsonld(document),
        "produced_by": {
            "type": "Production",
            "took_place_at": place,
            "timespan": document_timespan_jsonld(document),
            # "carried_out_by": None,  # unknown actor
        },
        "part": document_parts_jsonld(document),
        "carries": carries,
        "subject_of": {
            # "id": f"https://globalise.huygens.knaw.nl/document/{document.id}", # TODO: viewer uri???
            "type": "DigitalObject",
            "_label": "This document shown by Globalise",
        },
    }


# Series (Set) JSON-LD


def series_to_jsonld(series) -> Dict[str, Any]:
    # Determine classification based on hierarchy level
    # If it has a parent, it's likely a sub-grouping; otherwise a top-level grouping
    if getattr(series, "part_of_id", None) is not None:
        classified_as = {
            "id": "http://vocab.getty.edu/aat/300404023",
            "type": "Type",
            "_label": "Archival Subseries (subgroups)",
        }
    else:
        classified_as = {
            "id": "http://vocab.getty.edu/aat/300404022",
            "type": "Type",
            "_label": "Archival Grouping",
        }

    # Identified by Name
    identified_by = [
        {
            "type": "Name",
            "classified_as": [
                {
                    "id": "http://vocab.getty.edu/aat/300404670",
                    "type": "Type",
                    "_label": "Primary Name",
                }
            ],
            "content": series.title,
        }
    ]

    # Member of (parent Series if exists)
    member_of = None
    if getattr(series, "part_of", None) is not None:
        parent = series.part_of
        member_of = [
            {
                "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/series:{parent.id}",
                "type": "Set",
                "_label": parent.title,
            }
        ]

    result: Dict[str, Any] = {
        "@context": "https://linked.art/ns/v1/linked-art.json",
        "type": "Set",
        "_label": series.title,
        "classified_as": [classified_as],
        "identified_by": identified_by,
    }

    if member_of:
        result["member_of"] = member_of


def series_chain(series) -> Dict[str, Any]:
    """
    Build nested member_of structure from leaf series up to root series.

    Examples:
        >>> from types import SimpleNamespace
        >>> root = SimpleNamespace(title="Root Series", part_of=None)
        >>> child = SimpleNamespace(title="Child Series", part_of=root)
        >>> chain = series_chain(child)
        >>> chain["_label"]
        'Child Series'
        >>> chain["member_of"]["_label"]
        'Root Series'
    """
    chain = []
    current = series
    while current is not None:
        chain.append(current)
        current = current.part_of

    node: Optional[Dict[str, Any]] = None
    for series_obj in reversed(chain):
        current_node: Dict[str, Any] = {
            "type": "Set",
            "_label": series_obj.title,
        }
        if node is not None:
            current_node["member_of"] = node
        node = current_node

    assert node is not None
    return node


# Inventory (CuratedHolding) JSON-LD


def inventory_to_jsonld(inventory) -> Dict[str, Any]:
    inv_id = f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/inventory:{inventory.inventory_number}"

    # Include all titles from inventory
    title_obj = []
    if getattr(inventory, "titles", None) and inventory.titles:
        for title in inventory.titles:
            title_obj.append({"type": "Title", "content": title.title})

    # Timespan
    timespan = None
    if (
        (getattr(inventory, "date_start", None) is not None)
        or (getattr(inventory, "date_end", None) is not None)
        or (getattr(inventory, "date_text", None))
    ):
        timespan = {
            "type": "Timespan",
            "begin_of_the_begin": (
                str(inventory.date_start)
                if getattr(inventory, "date_start", None) is not None
                else None
            ),
            "end_of_the_end": (
                str(inventory.date_end)
                if getattr(inventory, "date_end", None) is not None
                else None
            ),
        }
        if getattr(inventory, "date_text", None):
            timespan["name"] = {
                "type": "Name",
                "_label": inventory.date_text,
                "content": inventory.date_text,
            }

    # Derive production places from settlements linked to documents in this inventory.
    place_candidates: List[Dict[str, Any]] = []
    seen_place_keys = set()
    if getattr(inventory, "documents", None):
        for doc in inventory.documents:
            settlement = getattr(doc, "location", None)
            if not settlement:
                continue

            place_key = getattr(settlement, "id", None) or getattr(
                settlement, "glob_id", None
            )
            if place_key in seen_place_keys:
                continue
            seen_place_keys.add(place_key)

            place_candidates.append(settlement_to_place_jsonld(settlement))

    took_place_at: Any
    if len(place_candidates) == 1:
        took_place_at = place_candidates[0]
    elif place_candidates:
        took_place_at = place_candidates
    else:
        took_place_at = None

    produced_by = {
        "type": "Production",
        # "classified_as": None,
        "took_place_at": took_place_at,
        "timespan": timespan,
        # "carried_out_by": {
        #     # "id": None,
        #     "type": "Actor",
        #     "_label": "Polity or Person",
        # },
    }

    # Parts: physical documents within inventory with full structure (pages as parts)
    parts: List[Dict[str, Any]] = []
    if getattr(inventory, "documents", None):
        for doc in inventory.documents:  # limit to avoid huge payloads
            title = doc.title or f"Document {doc.id}"

            # Get the shallow document reference (without pages) to avoid huge payloads
            doc_ref = {
                "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/document:{doc.id}",
                "type": "PhysicalHumanMadeThing",
                "_label": title,
            }
            parts.append(doc_ref)

    handle = inventory.handle if getattr(inventory, "handle", None) else None

    # Build member_of from real series relationships with nested parent chain per series
    member_of_list: List[Dict[str, Any]] = []
    if getattr(inventory, "member_of_series", None):
        for s in inventory.member_of_series:

            member_of_list.append(series_chain(s))

    subject_of: List[Dict[str, Any]] = []
    if handle:
        subject_of.append(
            {
                # Handle for Website NA
                "type": "LinguisticObject",
                "_label": f"Web page content for Inventory {inventory.inventory_number} at the National Archives",
                "digitally_carried_by": [
                    {
                        "type": "DigitalObject",
                        "_label": f"Inventory {inventory.inventory_number} at the National Archives",
                        "classified_as": [
                            {
                                "id": "http://vocab.getty.edu/aat/300264578",
                                "type": "Type",
                                "_label": "Web Page",
                            }
                        ],
                        "access_point": {
                            "id": handle,
                            "type": "DigitalObject",
                        },
                    }
                ],
            }
        )
    # IIIF Manifest
    subject_of.append(
        {
            "type": "LinguisticObject",
            "_label": f"IIIF Manifest content for Inventory {inventory.inventory_number}",
            "digitally_carried_by": [
                {
                    "type": "DigitalObject",
                    "access_point": [
                        # Shallow reference
                        {
                            "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/inventory:{inventory.inventory_number}.manifest",
                            "type": ["DigitalObject", "Manifest"],
                            "_label": f"IIIF Manifest for Inventory {inventory.inventory_number}",
                        }
                        # inventory_to_manifest_jsonld(
                        #     inventory,
                        #     manifest_uri=f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/inventory:{inventory.inventory_number}.manifest",
                        # )
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
        }
    )

    result: Dict[str, Any] = {
        "@context": "https://linked.art/ns/v1/linked-art.json",
        "id": inv_id,
        "type": "CuratedHolding",
        "_label": f"Inventory {inventory.inventory_number}",
        # Classified_as as a single object (per provided example)
        "classified_as": [
            {
                "id": "http://vocab.getty.edu/aat/300027046",  # File unit
                "type": "Type",
                "_label": "File unit",
            }
        ],
        "identified_by": [
            {
                "type": "Identifier",
                "classified_as": [
                    {
                        "id": "http://vocab.getty.edu/aat/300312355",
                        "type": "Type",
                        "_label": "Accession number",
                    }
                ],
                "content": inventory.inventory_number,
            },
            {
                "type": "Identifier",
                "classified_as": [
                    {
                        "id": "http://vocab.getty.edu/aat/300445023",
                        "type": "Type",
                        "_label": "Entry number",
                    }
                ],
                "content": inventory.id,
            },
        ],
        "title": title_obj,
        "produced_by": produced_by,
        "member_of": member_of_list,
        "part": parts,
        # "equivalent": handle,
        # IIIF
        "subject_of": subject_of,
        "carries": {
            "type": "LinguisticObject",
            "_label": "Textual content of the inventory",
            "digitally_carried_by": {
                # txt file
                "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/inventory:{inventory.inventory_number}.txt",
                "type": "DigitalObject",
                "_label": f"Plain text of Inventory {inventory.inventory_number}",
                "classified_as": {
                    "id": "http://vocab.getty.edu/aat/300460247",
                    "type": "Type",
                    "_label": "Plain text",
                },
                "format": "text/plain",
            },
            "referred_to_by": [  # All annotations
                {
                    "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/inventory:{inventory.inventory_number}.annotations",
                    "type": "Set",
                    "_label": f"Annotations for Inventory {inventory.inventory_number}",
                }
            ],
        },
    }

    return result


def get_first_page_index(doc) -> float | int:
    """
    Get the index of the first page in a document for sorting.

    Examples:
        >>> from types import SimpleNamespace
        >>> doc = SimpleNamespace(pages=[SimpleNamespace(index=5), SimpleNamespace(index=2)])
        >>> get_first_page_index(doc)
        2
        >>> get_first_page_index(SimpleNamespace(pages=[]))
        inf
    """
    if getattr(doc, "pages", None):
        indices = [
            p.index for p in doc.pages if p and getattr(p, "index", None) is not None
        ]
        if indices:
            return min(indices)
    return float("inf")


def create_document_range(
    doc: Document,
    inventory_number: str,
) -> Dict[str, Any]:
    """
    Create a IIIF Presentation 3.0 Range for a document and its subdocuments recursively.

    Examples:
        >>> from types import SimpleNamespace
        >>> doc = SimpleNamespace(
        ...     id="NL-HaNA_1.04.02_3598_0795-0943",
        ...     title="Resoluties",
        ...     date_text=None,
        ...     date_earliest_begin=None,
        ...     date_latest_begin=None,
        ...     date_earliest_end=None,
        ...     date_latest_end=None,
        ...     document_types_linked=[],
        ...     external_ids=[],
        ...     pages=[],
        ...     sub_documents=[],
        ... )
        >>> r = create_document_range(doc, inventory_number="3598")
        >>> r["id"]
        'https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/inventory:3598.manifest/range/NL-HaNA_1.04.02_3598_0795-0943'
        >>> r["label"]["en"]
        ['Resoluties']
    """
    # Determine label from title or ID
    if getattr(doc, "title", None):
        label_text = doc.title
    else:
        label_text = f"Document {doc.id}"

    doc_range: Dict[str, Any] = {
        "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/inventory:{inventory_number}.manifest/range/{doc.id}",
        "type": "Range",
        "label": {"en": [label_text]},
        "metadata": [],
        "items": [],
    }

    # Add metadata to the range
    # Title
    if getattr(doc, "title", None):
        doc_range["metadata"].append(
            {
                "label": {"en": ["Title"]},
                "value": {"en": [doc.title]},
            }
        )

    # Date
    if getattr(doc, "date_text", None):
        doc_range["metadata"].append(
            {
                "label": {"en": ["Date"]},
                "value": {"none": [doc.date_text]},
            }
        )
    elif getattr(doc, "date_earliest_begin", None) or getattr(
        doc, "date_latest_end", None
    ):
        date_str = ""
        if doc.date_earliest_begin and doc.date_latest_end:
            date_str = f"{doc.date_earliest_begin} / {doc.date_latest_end}"
        elif doc.date_earliest_begin:
            date_str = str(doc.date_earliest_begin)
        elif doc.date_latest_end:
            date_str = str(doc.date_latest_end)
        if date_str:
            doc_range["metadata"].append(
                {
                    "label": {"en": ["Date"]},
                    "value": {"none": [date_str]},
                }
            )

    if getattr(doc, "date_earliest_begin", None):
        doc_range["metadata"].append(
            {
                "label": {"en": ["Date earliest begin"]},
                "value": {"none": [str(doc.date_earliest_begin)]},
            }
        )
    if getattr(doc, "date_latest_begin", None):
        doc_range["metadata"].append(
            {
                "label": {"en": ["Date latest begin"]},
                "value": {"none": [str(doc.date_latest_begin)]},
            }
        )
    if getattr(doc, "date_earliest_end", None):
        doc_range["metadata"].append(
            {
                "label": {"en": ["Date earliest end"]},
                "value": {"none": [str(doc.date_earliest_end)]},
            }
        )
    if getattr(doc, "date_latest_end", None):
        doc_range["metadata"].append(
            {
                "label": {"en": ["Date latest end"]},
                "value": {"none": [str(doc.date_latest_end)]},
            }
        )

    # Type (from linked document types)
    if getattr(doc, "document_types_linked", None):
        for dt_link in doc.document_types_linked:
            dt = getattr(dt_link, "document_type", None)
            if dt is None:
                continue
            label = dt.pref_label_en or dt.pref_label_nl or dt.id
            doc_range["metadata"].append(
                {
                    "label": {"en": ["Type"]},
                    "value": {"none": [label]},
                }
            )

    # Inventory number
    doc_range["metadata"].append(
        {
            "label": {"en": ["Inventory number"]},
            "value": {"none": [inventory_number]},
        }
    )

    # External IDs (TANAP-id, etc.)
    if getattr(doc, "external_ids", None):
        for ext_id_link in doc.external_ids:
            ext = getattr(ext_id_link, "external", None)
            if not ext:
                continue
            external_context = getattr(ext, "context", None)
            external_identifier = getattr(ext, "identifier", None)
            if external_context and external_identifier:
                # Add with context label (e.g., "TANAP-id")
                label_text = (
                    "TANAP-id"
                    if external_context.upper() == "TANAP"
                    else f"{external_context} ID"
                )
                doc_range["metadata"].append(
                    {
                        "label": {"en": [label_text]},
                        "value": {"none": [external_identifier]},
                    }
                )

    # Document UUID identifier
    doc_range["metadata"].append(
        {
            "label": {"en": ["Identifier"]},
            "value": {"none": [doc.id]},
        }
    )

    # Link to the document physical metadata (via seeAlso)
    doc_range["seeAlso"] = [
        {
            "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/document:{doc.id}",
            "type": "PhysicalHumanMadeThing",
            "label": {"en": ["Document metadata"]},
        }
    ]

    # Check if this document has subdocuments
    has_subdocuments = bool(
        getattr(doc, "sub_documents", None) and len(doc.sub_documents) > 0
    )

    # Add canvas references for this document's pages
    if getattr(doc, "pages", None):
        # Sort pages by index
        sorted_page_links = sorted(
            doc.pages,
            key=lambda p: (
                p.index if (p and getattr(p, "index", None) is not None) else 0
            ),
        )
        # Track seen canvas IDs to avoid duplicates (2 pages can share 1 scan)
        seen_canvas_ids = set()
        for link in sorted_page_links:
            page = getattr(link, "page", None)
            if (
                page
                and getattr(page, "scan", None)
                and getattr(page.scan, "filename", None)
            ):
                # Reference the canvas by scan identifier
                canvas_id = f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/canvas:{page.scan.filename}"
                # Only add if not already in the list
                if canvas_id not in seen_canvas_ids:
                    seen_canvas_ids.add(canvas_id)
                    doc_range["items"].append({"id": canvas_id, "type": "Canvas"})

    # If it has subdocuments, add them as nested ranges
    if has_subdocuments:
        sorted_subdocs = sorted(doc.sub_documents, key=get_first_page_index)
        for subdoc in sorted_subdocs:
            subdoc_range = create_document_range(
                subdoc,
                inventory_number=inventory_number,
            )
            doc_range["items"].append(subdoc_range)

    return doc_range


def inventory_to_manifest_jsonld(inventory, manifest_uri: str) -> Dict[str, Any]:
    """
    Generate a IIIF Presentation 3.0 Manifest for the Inventory
    """

    # Metadata values for top-level IIIF Presentation 3.0 manifest metadata.
    title_values: List[str] = []
    if getattr(inventory, "titles", None):
        title_values = [t.title for t in inventory.titles if getattr(t, "title", None)]

    collection_values: List[str] = []
    if getattr(inventory, "member_of_series", None):
        collection_values = [
            s.title for s in inventory.member_of_series if getattr(s, "title", None)
        ]

    settlement_values: List[str] = []
    if getattr(inventory, "documents", None):
        for doc in inventory.documents:
            location = getattr(doc, "location", None)
            if not location:
                continue
            if getattr(location, "labels", None) and location.labels:
                first_label = getattr(location.labels[0], "label", None)
                if first_label:
                    settlement_values.append(first_label)
                    continue
            glob_id = getattr(location, "glob_id", None)
            if glob_id:
                settlement_values.append(glob_id)

    # Preserve order while de-duplicating; use null when no value is available.
    cleaned_title_values = [v.strip() for v in title_values if v and v.strip()]
    title_value = (
        "; ".join(list(dict.fromkeys(cleaned_title_values)))
        if cleaned_title_values
        else None
    )

    cleaned_collection_values = [
        v.strip() for v in collection_values if v and v.strip()
    ]
    collection_value = (
        "; ".join(list(dict.fromkeys(cleaned_collection_values)))
        if cleaned_collection_values
        else None
    )

    cleaned_settlement_values = [
        v.strip() for v in settlement_values if v and v.strip()
    ]
    settlement_value = (
        "; ".join(list(dict.fromkeys(cleaned_settlement_values)))
        if cleaned_settlement_values
        else None
    )

    if getattr(inventory, "date_start", None) and getattr(inventory, "date_end", None):
        date_value = f"{inventory.date_start} / {inventory.date_end}"
    elif getattr(inventory, "date_start", None):
        date_value = str(inventory.date_start)
    elif getattr(inventory, "date_end", None):
        date_value = str(inventory.date_end)
    else:
        date_value = None

    manifest_metadata: List[Dict[str, Any]] = []
    inv_num = getattr(inventory, "inventory_number", None)
    if inv_num:
        manifest_metadata.append(
            {
                "label": {"en": ["Inventory number"], "nl": ["Inventarisnummer"]},
                "value": {"none": [str(inv_num)]},
            }
        )
    if collection_value:
        manifest_metadata.append(
            {
                "label": {"en": ["Collection"], "nl": ["Collectie"]},
                "value": {"none": [collection_value]},
            }
        )
    if date_value:
        manifest_metadata.append(
            {
                "label": {"en": ["Date"], "nl": ["Datum"]},
                "value": {"none": [date_value]},
            }
        )
    if title_value:
        manifest_metadata.append(
            {
                "label": {"en": ["Title(s)"], "nl": ["Titel(s)"]},
                "value": {"none": [title_value]},
            }
        )
    if settlement_value:
        manifest_metadata.append(
            {
                "label": {"en": ["Settlement"], "nl": ["Comptoir"]},
                "value": {"none": [settlement_value]},
            }
        )
    handle_val = getattr(inventory, "handle", None)
    if handle_val:
        manifest_metadata.append(
            {
                "label": {"en": ["Handle"], "nl": ["Handle"]},
                "value": {"none": [str(handle_val)]},
            }
        )

    manifest: Dict[str, Any] = {
        "@context": [
            "https://linked.art/ns/v1/linked-art.json",
            "http://iiif.io/api/extension/navplace/context.json",
            "http://iiif.io/api/presentation/3/context.json",
            {
                "transcription-diplomatic": {
                    "@id": "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/thesaurus:transcription-diplomatic"
                },
                "transcription-normalized": {
                    "@id": "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/thesaurus:transcription-normalized"
                },
            },
        ],
        "id": manifest_uri,
        "type": "Manifest",
        "label": {"en": [f"Inventory {inventory.inventory_number}"]},
        "requiredStatement": {
            "label": {"en": ["Attribution"]},
            "value": {
                "en": [
                    '<span>GLOBALISE Project. <a href="https://creativecommons.org/publicdomain/zero/1.0/"> <img src="https://licensebuttons.net/l/zero/1.0/88x31.png" alt="CC0 1.0 Universal (CC0 1.0) Public Domain Dedication"/> </a> </span>'
                ]
            },
        },
        "rights": "http://creativecommons.org/publicdomain/zero/1.0/",
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
        "metadata": manifest_metadata,
        "items": [],
        "seeAlso": [
            {
                "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/inventory:{inventory.inventory_number}",
                "type": "CuratedHolding",
                "label": {"en": ["Curated Holding metadata"]},
            }
        ],
    }

    # Add navDate if inventory has date information
    if getattr(inventory, "date_start", None):
        manifest["navDate"] = f"{inventory.date_start}T00:00:00"
    elif getattr(inventory, "date_end", None):
        manifest["navDate"] = f"{inventory.date_end}T00:00:00"

    inventory_text_uri = (
        f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/"
        f"inventory:{inventory.inventory_number}.txt"
    )

    # Add Canvas for each Inventory's Scan (avoid document linkage)
    sorted_scans: List[Any] = []
    if getattr(inventory, "scans", None):
        # Sort scans by filename for consistent ordering
        sorted_scans = sorted(inventory.scans, key=lambda s: s.filename or "")
        for scan in sorted_scans:
            canvas_id = f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/canvas:{scan.filename}"
            # Determine recto/verso from related pages, if present
            rv_label = None
            if getattr(scan, "pages", None):
                rv_values = [
                    p.recto_verso.value
                    for p in scan.pages
                    if getattr(p, "recto_verso", None)
                ]
                if len(rv_values) == 1:
                    rv_label = rv_values[0]
                elif len(rv_values) > 1:
                    # Combine unique values
                    uniq = sorted(set(rv_values))
                    rv_label = ", ".join(uniq)

            image_id = scan.get_image_url(size="max") or ""
            # IIIF Image service id from info.json, if available
            service_id = None
            image_info = getattr(scan, "iiif_image_info", None)
            if image_info:
                service_id = image_info.replace("/info.json", "")

            # Normalized
            text_start = getattr(scan, "inventory_text_start_offset", None)
            text_end = getattr(scan, "inventory_text_end_offset", None)

            # Diplomatic
            text_htr_start = getattr(scan, "htr_text_start_offset", None)
            text_htr_end = getattr(scan, "htr_text_end_offset", None)

            # Metadata entries similar to the example (Filename, Web)
            # Use scan.filename directly; only include Web if `na_identifier` is a URL
            web_url = None
            if getattr(scan, "na_identifier", None):
                nai = str(scan.na_identifier)
                if nai.startswith("http://") or nai.startswith("https://"):
                    web_url = nai

            label_text = scan.filename

            canvas_obj: Dict[str, Any] = {
                "id": canvas_id,
                "type": "Canvas",
                "label": {"en": [label_text]},
                "height": scan.height,
                "width": scan.width,
                "metadata": [
                    {
                        "label": {"en": ["Filename"]},
                        "value": {"none": [scan.filename]},
                    },
                ],
                "items": [
                    {
                        "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/annotations:painting:{scan.filename}",
                        "type": "AnnotationPage",
                        "items": [
                            {
                                "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/annotations:painting:{scan.filename}#annotation",
                                "type": "Annotation",
                                "motivation": "painting",
                                "body": {
                                    "id": image_id,
                                    "type": "Image",
                                    "format": "image/jpeg",
                                    "height": scan.height,
                                    "width": scan.width,
                                    "service": [
                                        {
                                            "id": service_id,
                                            "type": "ImageService3",
                                            "profile": "level2",
                                        }
                                    ],
                                },
                                "target": canvas_id,
                            }
                        ],
                    }
                ],
                "annotations": [],
            }

            # Add annotation pages for transcriptions, entities, and events (only if available)
            annotations: List[Dict[str, Any]] = []

            # Normalized offsets
            if (
                text_start is not None
                and text_end is not None
                and text_htr_start is not None
                and text_htr_end is not None
            ):
                ap_text: Dict[str, Any] = {
                    "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/annotations:text:{scan.filename}",
                    "type": "AnnotationPage",
                    "items": [],
                }

                # Normalized text annotation
                if text_start is not None and text_end is not None:
                    ap_text["items"].append(
                        {
                            "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/annotations:text:{scan.filename}#annotation",
                            "type": "Annotation",
                            "motivation": "describing",
                            "body": {
                                "type": "TextualBody",
                                "value": f"Scan text range for {scan.filename}",
                                "format": "text/plain",
                                "purpose": "transcription-normalized",
                            },
                            "target": {
                                "type": "SpecificResource",
                                "source": {
                                    "id": inventory_text_uri,
                                    "type": "DigitalObject",
                                    "_label": f"Plain text of Inventory {inventory.inventory_number}",
                                },
                                "selector": {
                                    "type": "TextPositionSelector",
                                    "start": text_start,
                                    "end": text_end,
                                },
                            },
                        }
                    )

                # Diplomatic text annotation
                if text_htr_start is not None and text_htr_end is not None:
                    ap_text["items"].append(
                        {
                            "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/annotations:htr:{scan.filename}#annotation",
                            "type": "Annotation",
                            "motivation": "describing",
                            "body": {
                                "type": "TextualBody",
                                "value": f"Diplomatic scan text range for {scan.filename}",
                                "format": "text/plain",
                                "purpose": "transcription-diplomatic",
                            },
                            "target": {
                                "type": "SpecificResource",
                                "source": {
                                    # "id": inventory_text_uri,  # TODO?
                                    "type": "DigitalObject",
                                    "_label": f"Plain diplomatic text of Inventory {inventory.inventory_number}",
                                },
                                "selector": {
                                    "type": "TextPositionSelector",
                                    "start": text_htr_start,
                                    "end": text_htr_end,
                                },
                            },
                        }
                    )

                annotations.append(ap_text)

            if getattr(scan, "has_transcriptions", False):
                annotations.append(
                    {
                        "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/annotations:transcriptions:{scan.filename}",
                        "type": "AnnotationPage",
                        "label": {"en": [f"Transcriptions of scan {scan.filename}"]},
                    }
                )
            if getattr(scan, "has_entities", False):
                annotations.append(
                    {
                        "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/annotations:entities:{scan.filename}",
                        "type": "AnnotationPage",
                        "label": {
                            "en": [f"Entities identified on scan {scan.filename}"]
                        },
                    }
                )
            if getattr(scan, "has_events", False):
                annotations.append(
                    {
                        "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/annotations:events:{scan.filename}",
                        "type": "AnnotationPage",
                        "label": {"en": [f"Events identified on scan {scan.filename}"]},
                    }
                )
            if annotations:
                canvas_obj["annotations"] = annotations

            # Optional metadata: Web link and Recto/Verso info
            if web_url:
                canvas_obj["metadata"].append(
                    {
                        "label": {"en": ["Web"]},
                        "value": {"none": [f'<a href="{web_url}">{web_url}</a>']},
                    }
                )
            if rv_label:
                canvas_obj["metadata"].append(
                    {
                        "label": {"en": ["Recto/Verso"]},
                        "value": {"none": [rv_label]},
                    }
                )

            manifest["items"].append(canvas_obj)

    # Add thumbnail from first scan if available
    if manifest["items"] and sorted_scans:
        first_scan = sorted_scans[0]
        if first_scan:
            thumb_id = first_scan.get_image_url(size="982,") or ""
            service_id = None
            first_scan_info = getattr(first_scan, "iiif_image_info", None)
            if first_scan_info:
                service_id = first_scan_info.replace("/info.json", "")

            if thumb_id and service_id:
                manifest["thumbnail"] = [
                    {
                        "id": thumb_id,
                        "type": "Image",
                        "height": first_scan.height,
                        "width": first_scan.width,
                        "service": [
                            {
                                "id": service_id,
                                "type": "ImageService3",
                                "profile": "level2",
                            }
                        ],
                        "format": "image/jpeg",
                    }
                ]

    # Add Range for each Document in Inventory
    # Create IIIF Presentation 3.0 structures (ranges) for navigation
    if getattr(inventory, "documents", None):
        # Create a top-level Range for table of contents
        top_range: Dict[str, Any] = {
            "id": f"https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/inventory:{inventory.inventory_number}.manifest/range/top",
            "type": "Range",
            "label": {"en": ["Table of Contents"], "nl": ["Inhoudsopgave"]},
            "items": [],
        }

        # Only process top-level documents (those without a parent)
        top_level_docs = [doc for doc in inventory.documents if doc.part_of_id is None]
        sorted_docs = sorted(top_level_docs, key=get_first_page_index)

        # Create ranges for all top-level documents
        for doc in sorted_docs:
            doc_range = create_document_range(
                doc,
                inventory_number=inventory.inventory_number,
            )
            top_range["items"].append(doc_range)

        # Add structures to manifest
        if top_range["items"]:
            manifest["structures"] = [top_range]

    return manifest


if __name__ == "__main__":
    import doctest

    results = doctest.testmod()
    print(f"Doctest results: {results}")
