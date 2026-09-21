"""
Export documents to CSV with identifier, inventory number, scan filenames,
title, date, settlement, method, start/end scan types, and languages.

Output paths:
  Local disk: <output_dir>/document/documents.csv (default: data/output/s3/document/documents.csv)
  S3: <s3-prefix>document/documents.csv (e.g. objects/document/documents.csv)
"""

import argparse
import logging
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import duckdb

from utils import (
    add_s3_arguments,
    build_s3_upload_config,
    create_s3_client,
    output_bytes,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

TYPE_URI_PREFIX = "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/thesaurus:"
PLACE_URI_PREFIX = "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/place:"

# Database setup
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///globalise_documents.db")


def get_sqlite_path(database_url: str) -> str:
    """Extract filesystem path from a SQLite database URL.

    Args:
        database_url: SQLite database URL or plain path (e.g. 'sqlite:///path/to/db.sqlite').

    Returns:
        Filesystem path to the SQLite database.

    Examples:
        >>> get_sqlite_path("sqlite:////home/user/test.db")
        '/home/user/test.db'
        >>> get_sqlite_path("sqlite:///relative/test.db")
        'relative/test.db'
        >>> get_sqlite_path("/home/user/test.db")
        '/home/user/test.db'
        >>> get_sqlite_path("sqlite:////home/user/test.db?mode=ro")
        '/home/user/test.db'
    """
    url = database_url.split("?")[0]
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///") :]
    if url.startswith("sqlite://"):
        return url[len("sqlite://") :]
    return url


def get_settlement(document):
    """Get the settlement identifier URI and label for a document.

    Examples:
        >>> get_settlement(None)
        ('', '')
        >>> from types import SimpleNamespace
        >>> loc = SimpleNamespace(id="uuid-1", glob_id="GLOB2_1848", labels=[SimpleNamespace(label="Batavia")])
        >>> doc = SimpleNamespace(location=loc)
        >>> get_settlement(doc)
        ('https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/place:GLOB2_1848', 'Batavia')
    """
    loc = getattr(document, "location", None)
    if loc:
        glob_id = getattr(loc, "glob_id", None)
        settlement_id = (
            f"{PLACE_URI_PREFIX}{glob_id.strip()}"
            if glob_id and glob_id.strip()
            else ""
        )
        labels = getattr(loc, "labels", None)
        if labels and labels[0] and getattr(labels[0], "label", None):
            settlement_label = labels[0].label
        else:
            settlement_label = getattr(loc, "glob_id", "") or ""
        return settlement_id, settlement_label
    return "", ""


def get_inventory_number(document):
    """Get the inventory number for a document.

    Examples:
        >>> get_inventory_number(None)
        ''
    """
    inv = getattr(document, "inventory", None)
    if inv:
        return getattr(inv, "inventory_number", "") or ""
    return ""


def get_start_end_scan_filenames(document):
    """Get the first and last scan filenames for a document, sorted by scan name.

    Examples:
        >>> get_start_end_scan_filenames(None)
        ('', '')
    """
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
    """Get document page links sorted by scan filename.

    Examples:
        >>> get_ordered_page_links(None)
        []
    """
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
    """Get the first and last page links for a document, sorted by scan filename.

    Examples:
        >>> get_start_end_page_links(None)
        (None, None)
    """
    sorted_pages = get_ordered_page_links(document)
    if not sorted_pages:
        return None, None

    return sorted_pages[0], sorted_pages[-1]


def get_start_end_scan_types(document):
    """Get the scan types of the first and last scans for a document.

    Examples:
        >>> get_start_end_scan_types(None)
        ('', '')
    """
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
    """Get the document's earliest begin date in ISO 8601 format.

    Examples:
        >>> from types import SimpleNamespace
        >>> get_date_start(SimpleNamespace(date_earliest_begin=None))
        ''
    """
    if document.date_earliest_begin:
        return document.date_earliest_begin.isoformat()
    return ""


def get_date_latest_begin(document):
    """Get the document's latest begin date in ISO 8601 format.

    Examples:
        >>> from types import SimpleNamespace
        >>> get_date_latest_begin(SimpleNamespace(date_latest_begin=None))
        ''
    """
    if document.date_latest_begin:
        return document.date_latest_begin.isoformat()
    return ""


def get_date_earliest_end(document):
    """Get the document's earliest end date in ISO 8601 format.

    Examples:
        >>> from types import SimpleNamespace
        >>> get_date_earliest_end(SimpleNamespace(date_earliest_end=None))
        ''
    """
    if document.date_earliest_end:
        return document.date_earliest_end.isoformat()
    return ""


def get_date_end(document):
    """Get the document's latest end date in ISO 8601 format.

    Examples:
        >>> from types import SimpleNamespace
        >>> get_date_end(SimpleNamespace(date_latest_end=None))
        ''
    """
    if document.date_latest_end:
        return document.date_latest_end.isoformat()
    return ""


def get_identification_method(document):
    """Get the identification method name.

    Examples:
        >>> get_identification_method(None)
        ''
    """
    method = getattr(document, "method", None)
    if method:
        return getattr(method, "name", "") or ""
    return ""


def get_document_type_uuids(document):
    """Get a comma-separated list of linked document type UUIDs.

    Examples:
        >>> get_document_type_uuids(None)
        ''
    """
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


def get_languages(document):
    """Get a comma-separated list of distinct language codes for a document sorted alphabetically.

    Examples:
        >>> get_languages(None)
        ''
        >>> from types import SimpleNamespace
        >>> scan1 = SimpleNamespace(languages="nld,fra")
        >>> scan2 = SimpleNamespace(languages="unknown,nld,deu")
        >>> p1 = SimpleNamespace(page=SimpleNamespace(scan=scan1))
        >>> p2 = SimpleNamespace(page=SimpleNamespace(scan=scan2))
        >>> doc = SimpleNamespace(pages=[p1, p2])
        >>> get_languages(doc)
        'deu,fra,nld'
    """
    if not document:
        return ""

    codes = set()

    def collect(doc):
        for link in getattr(doc, "pages", None) or []:
            pg = getattr(link, "page", None) if link else None
            scan = getattr(pg, "scan", None) if pg else getattr(link, "scan", None)
            raw = getattr(scan, "languages", None) if scan else None
            if raw:
                for code in raw.split(","):
                    code = code.strip().lower()
                    if code and code != "unknown":
                        codes.add(code)
        for subdoc in getattr(doc, "sub_documents", None) or []:
            collect(subdoc)

    collect(document)
    return ",".join(sorted(codes))


def export_documents_csv(
    output_dir: str,
    gzipped: bool = True,
    s3_client=None,
    s3_config=None,
    database_url: str = DATABASE_URL,
):
    """Export all documents to CSV, either to disk or directly to S3.

    Uses DuckDB to query the SQLite database directly and stream the CSV export,
    avoiding slow iterative ORM queries.

    Args:
        output_dir: Base local output directory (or S3 key prefix root).
        gzipped: When True, gzip-compress the output file.
        s3_client: Initialized Boto3 S3 client, or None to write to disk.
        s3_config: S3UploadConfig instance, or None to write to disk.
        database_url: Connection URL or filesystem path to the SQLite database.

    Examples:
        >>> import sqlite3
        >>> with tempfile.TemporaryDirectory() as tmp_dir:
        ...     db_file = os.path.join(tmp_dir, "test.db")
        ...     conn = sqlite3.connect(db_file)
        ...     _ = conn.execute("CREATE TABLE document (id TEXT PRIMARY KEY, inventory_id TEXT, title TEXT, date_earliest_begin DATE, date_latest_begin DATE, date_earliest_end DATE, date_latest_end DATE, location_id TEXT, method_id TEXT)")
        ...     _ = conn.execute("CREATE TABLE inventory (id TEXT PRIMARY KEY, inventory_number TEXT)")
        ...     _ = conn.execute("CREATE TABLE document2documenttype (document_id TEXT, document_type_id TEXT)")
        ...     _ = conn.execute("CREATE TABLE document_type (id TEXT PRIMARY KEY)")
        ...     _ = conn.execute("CREATE TABLE page2document (document_id TEXT, page_id TEXT)")
        ...     _ = conn.execute("CREATE TABLE page (id TEXT PRIMARY KEY, scan_id TEXT)")
        ...     _ = conn.execute("CREATE TABLE scan (id TEXT PRIMARY KEY, filename TEXT, scan_type TEXT, languages TEXT)")
        ...     _ = conn.execute("CREATE TABLE settlement (id TEXT PRIMARY KEY, glob_id TEXT)")
        ...     _ = conn.execute("CREATE TABLE settlement_label (id TEXT, settlement_id TEXT, label TEXT)")
        ...     _ = conn.execute("CREATE TABLE document_identification_method (id TEXT PRIMARY KEY, name TEXT)")
        ...     _ = conn.execute("INSERT INTO document VALUES ('doc-1', 'inv-1', 'Test Title', '1650-01-01', '1650-01-01', '1650-01-02', '1650-01-02', 'loc-1', 'meth-1')")
        ...     _ = conn.execute("INSERT INTO document VALUES ('doc-2', 'inv-1', 'Doc with Scans', '1650-01-01', '1650-01-01', '1650-01-02', '1650-01-02', 'loc-1', 'meth-1')")
        ...     _ = conn.execute("INSERT INTO scan VALUES ('scan-1', 'NL-HaNA_1.04.02_4088_0329', 'Text', 'nld')")
        ...     _ = conn.execute("INSERT INTO scan VALUES ('scan-2', 'NL-HaNA_1.04.02_4088_0330', 'Text', 'fra,nld')")
        ...     _ = conn.execute("INSERT INTO page VALUES ('page-1', 'scan-1')")
        ...     _ = conn.execute("INSERT INTO page VALUES ('page-2', 'scan-2')")
        ...     _ = conn.execute("INSERT INTO page2document VALUES ('doc-2', 'page-1')")
        ...     _ = conn.execute("INSERT INTO page2document VALUES ('doc-2', 'page-2')")
        ...     _ = conn.execute("INSERT INTO inventory VALUES ('inv-1', '1234')")
        ...     _ = conn.execute("INSERT INTO settlement VALUES ('loc-1', 'GLOB_1')")
        ...     _ = conn.execute("INSERT INTO settlement_label VALUES ('lbl-1', 'loc-1', 'Batavia')")
        ...     _ = conn.execute("INSERT INTO document_identification_method VALUES ('meth-1', 'Manual')")
        ...     conn.commit()
        ...     conn.close()
        ...     out_dir = os.path.join(tmp_dir, "out")
        ...     export_documents_csv(out_dir, gzipped=False, database_url=db_file)
        ...     with open(os.path.join(out_dir, "document", "documents.csv"), "r") as f:
        ...         lines = [line.strip() for line in f.readlines()]
        ...     print(lines[0])
        ...     print(lines[1])
        ...     print(lines[2])
        ...     out_doc_dir = os.path.join(tmp_dir, "out_doc", "document")
        ...     export_documents_csv(out_doc_dir, gzipped=False, database_url=db_file)
        ...     print(os.path.exists(os.path.join(out_doc_dir, "documents.csv")))
        ...     from types import SimpleNamespace
        ...     uploaded = {}
        ...     mock_s3 = SimpleNamespace(put_object=lambda **kwargs: uploaded.update(kwargs))
        ...     mock_cfg = SimpleNamespace(bucket="test-bucket", prefix="objects/", acl=None)
        ...     export_documents_csv(out_dir, gzipped=False, s3_client=mock_s3, s3_config=mock_cfg, database_url=db_file)
        ...     print(uploaded["Key"])
        identifier,inventory_number,type_uuids,start_scan_filename,end_scan_filename,start_scan_type,end_scan_type,title,date_earliest_begin,date_latest_begin,date_earliest_end,date_latest_end,settlement,settlement_id,method,languages
        doc-1,1234,"","","","","",Test Title,1650-01-01,1650-01-01,1650-01-02,1650-01-02,Batavia,https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/place:GLOB_1,Manual,""
        NL-HaNA_1.04.02_4088_0329-0330,1234,"",NL-HaNA_1.04.02_4088_0329,NL-HaNA_1.04.02_4088_0330,Text,Text,Doc with Scans,1650-01-01,1650-01-01,1650-01-02,1650-01-02,Batavia,https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/place:GLOB_1,Manual,"fra,nld"
        True
        objects/document/documents.csv
    """
    db_path = get_sqlite_path(database_url)
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database file not found: {db_path}")

    logger.info(f"Exporting documents CSV from {db_path} using DuckDB...")
    con = duckdb.connect()
    try:
        con.execute("INSTALL sqlite; LOAD sqlite;")
        escaped_db_path = db_path.replace("'", "''")
        con.execute(f"ATTACH '{escaped_db_path}' AS db (TYPE SQLITE, READ_ONLY);")
        con.execute("USE db;")

        compression_clause = " (HEADER, COMPRESSION GZIP)" if gzipped else " (HEADER)"

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_output = os.path.join(
                tmpdir, "documents.csv.gz" if gzipped else "documents.csv"
            )
            escaped_temp_output = temp_output.replace("'", "''")

            query = f"""
            COPY (
                WITH doc_scans AS (
                    SELECT
                        p2d.document_id,
                        arg_min(s.filename, s.filename) AS start_scan_filename,
                        arg_max(s.filename, s.filename) AS end_scan_filename,
                        arg_min(COALESCE(s.scan_type, ''), s.filename) AS start_scan_type,
                        arg_max(COALESCE(s.scan_type, ''), s.filename) AS end_scan_type
                    FROM page2document p2d
                    JOIN page p ON p.id = p2d.page_id
                    JOIN scan s ON s.id = p.scan_id
                    GROUP BY p2d.document_id
                ),
                doc_languages AS (
                    SELECT
                        p2d.document_id,
                        string_agg(DISTINCT lower(trim(lang)), ',' ORDER BY lower(trim(lang))) AS languages
                    FROM page2document p2d
                    JOIN page p ON p.id = p2d.page_id
                    JOIN scan s ON s.id = p.scan_id
                    CROSS JOIN unnest(string_split(COALESCE(s.languages, ''), ',')) AS t(lang)
                    WHERE trim(lang) != '' AND lower(trim(lang)) != 'unknown'
                    GROUP BY p2d.document_id
                ),
                doc_types AS (
                    SELECT
                        dtl.document_id,
                        string_agg(
                            '{TYPE_URI_PREFIX}' || dt.id,
                            ',' ORDER BY '{TYPE_URI_PREFIX}' || dt.id
                        ) AS type_uuids
                    FROM document2documenttype dtl
                    JOIN document_type dt ON dt.id = dtl.document_type_id
                    GROUP BY dtl.document_id
                ),
                settlement_info AS (
                    SELECT
                        l.id AS location_id,
                        CASE
                            WHEN l.glob_id IS NOT NULL AND trim(l.glob_id) != ''
                            THEN '{PLACE_URI_PREFIX}' || trim(l.glob_id)
                            ELSE ''
                        END AS settlement_id,
                        COALESCE(
                            FIRST_VALUE(lbl.label) OVER (PARTITION BY l.id ORDER BY lbl.rowid),
                            l.glob_id,
                            ''
                        ) AS settlement_label
                    FROM settlement l
                    LEFT JOIN settlement_label lbl ON lbl.settlement_id = l.id
                    QUALIFY ROW_NUMBER() OVER (PARTITION BY l.id ORDER BY lbl.rowid) = 1
                )
                SELECT
                    CASE
                        WHEN ds.start_scan_filename IS NOT NULL AND ds.end_scan_filename IS NOT NULL
                             AND regexp_matches(ds.start_scan_filename, '_[0-9A-Za-z]+$')
                             AND regexp_matches(ds.end_scan_filename, '_[0-9A-Za-z]+$')
                        THEN regexp_replace(ds.start_scan_filename, '_[0-9A-Za-z]+$', '') || '_' || regexp_extract(ds.start_scan_filename, '([0-9A-Za-z]+)$', 1) || '-' || regexp_extract(ds.end_scan_filename, '([0-9A-Za-z]+)$', 1)
                        ELSE d.id
                    END AS identifier,
                    COALESCE(i.inventory_number, '') AS inventory_number,
                    COALESCE(dt.type_uuids, '') AS type_uuids,
                    COALESCE(ds.start_scan_filename, '') AS start_scan_filename,
                    COALESCE(ds.end_scan_filename, '') AS end_scan_filename,
                    COALESCE(ds.start_scan_type, '') AS start_scan_type,
                    COALESCE(ds.end_scan_type, '') AS end_scan_type,
                    COALESCE(d.title, '') AS title,
                    COALESCE(CAST(d.date_earliest_begin AS VARCHAR), '') AS date_earliest_begin,
                    COALESCE(CAST(d.date_latest_begin AS VARCHAR), '') AS date_latest_begin,
                    COALESCE(CAST(d.date_earliest_end AS VARCHAR), '') AS date_earliest_end,
                    COALESCE(CAST(d.date_latest_end AS VARCHAR), '') AS date_latest_end,
                    COALESCE(s.settlement_label, '') AS settlement,
                    COALESCE(s.settlement_id, '') AS settlement_id,
                    COALESCE(m.name, '') AS method,
                    COALESCE(dl.languages, '') AS languages
                FROM document d
                LEFT JOIN inventory i ON i.id = d.inventory_id
                LEFT JOIN doc_types dt ON dt.document_id = d.id
                LEFT JOIN doc_scans ds ON ds.document_id = d.id
                LEFT JOIN doc_languages dl ON dl.document_id = d.id
                LEFT JOIN settlement_info s ON s.location_id = d.location_id
                LEFT JOIN document_identification_method m ON m.id = d.method_id
                ORDER BY d.id
            ) TO '{escaped_temp_output}'{compression_clause};
            """
            con.execute(query)

            if s3_client and s3_config:
                with open(temp_output, "rb") as f:
                    payload = f.read()
                prefix = getattr(s3_config, "prefix", "") or ""
                if prefix.rstrip("/").endswith("document"):
                    s3_relative_path = "documents.csv"
                else:
                    s3_relative_path = "document/documents.csv"

                output_bytes(
                    payload,
                    s3_relative_path,
                    output_dir,
                    gzipped,
                    s3_client,
                    s3_config,
                    content_type="text/csv; charset=utf-8",
                )
                dest = f"{prefix}{s3_relative_path}"
            else:
                if os.path.basename(os.path.normpath(output_dir)) == "document":
                    target_path = os.path.join(output_dir, "documents.csv")
                else:
                    target_path = os.path.join(output_dir, "document", "documents.csv")
                dirpath = os.path.dirname(target_path)
                if dirpath:
                    os.makedirs(dirpath, exist_ok=True)
                shutil.move(temp_output, target_path)
                dest = target_path

        logger.info(f"Successfully exported documents to {dest}")
    finally:
        con.close()


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Export documents to a CSV file.")
    parser.add_argument(
        "output_dir",
        nargs="?",
        default=os.environ.get(
            "DOCUMENTS_CSV_OUTPUT_DIR",
            os.environ.get("DOCUMENTS_OUTPUT_DIR", "data/output/s3"),
        ),
        help="Base local output directory (default: data/output/s3, or DOCUMENTS_CSV_OUTPUT_DIR env var)",
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
