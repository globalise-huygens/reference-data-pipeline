"""
Common helper utilities for the GLOBALISE pipeline.

Includes S3 upload helpers, JSON serialization, framing output writers,
string/URI path sanitization, XML element naming, date formatting, and cell converters.
"""

import argparse
import calendar
import gzip
import html
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from typing import Any
from urllib.parse import quote

import boto3
import openpyxl
from rdflib import BNode, Dataset, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, SKOS
from rdflib.term import Node

PROJECT_URI_TOKEN = "data.globalise.huygens.knaw.nl"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass(frozen=True)
class S3UploadConfig:
    """
    Container for S3 upload parameters.

    Attributes:
        bucket (str): Target S3 bucket name.
        prefix (str): Object key prefix.
        endpoint_url (str, optional): Custom S3 endpoint URL. Defaults to None.
        region (str, optional): AWS region. Defaults to None.
        acl (str, optional): S3 canned ACL string. Defaults to None.
        access_key_id (str, optional): AWS access key ID. Defaults to None.
        secret_access_key (str, optional): AWS secret access key. Defaults to None.
    """

    bucket: str
    prefix: str
    endpoint_url: str | None = None
    region: str | None = None
    acl: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None


def add_s3_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add optional S3 upload configuration arguments to an argument parser.

    Args:
        parser (argparse.ArgumentParser): The argument parser instance to configure.
    """
    parser.add_argument(
        "--s3-bucket",
        default=os.getenv("S3_BUCKET"),
        help="Upload framed JSON directly to this S3 bucket instead of writing to disk. Defaults to S3_BUCKET.",
    )
    parser.add_argument(
        "--s3-prefix",
        default=os.getenv("S3_PREFIX", ""),
        help="Optional S3 key prefix (e.g. 'objects'). Defaults to S3_PREFIX.",
    )
    parser.add_argument(
        "--s3-endpoint-url",
        default=os.getenv("S3_ENDPOINT_URL"),
        help="Optional S3 endpoint URL for S3-compatible object stores. Defaults to S3_ENDPOINT_URL.",
    )
    parser.add_argument(
        "--s3-region",
        default=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"),
        help="Optional AWS region for S3 client configuration. Defaults to AWS_REGION/AWS_DEFAULT_REGION.",
    )
    parser.add_argument(
        "--s3-access-key-id",
        default=os.getenv("AWS_ACCESS_KEY_ID"),
        help="Optional S3 access key id. Defaults to AWS_ACCESS_KEY_ID.",
    )
    parser.add_argument(
        "--s3-secret-access-key",
        default=os.getenv("AWS_SECRET_ACCESS_KEY"),
        help="Optional S3 secret access key. Defaults to AWS_SECRET_ACCESS_KEY.",
    )
    parser.add_argument(
        "--s3-acl",
        help="Optional canned ACL (e.g. 'public-read').",
    )


def _normalise_s3_prefix(prefix: str) -> str:
    """
    Normalize S3 key prefix to ensure a single trailing slash.

    Args:
        prefix (str): Raw prefix string.

    Returns:
        str: Normalized prefix string with trailing slash, or empty string.

    Examples:
        >>> _normalise_s3_prefix("objects/")
        'objects/'
        >>> _normalise_s3_prefix("objects")
        'objects/'
        >>> _normalise_s3_prefix("")
        ''
        >>> _normalise_s3_prefix("  /path/sub/ ")
        'path/sub/'
    """
    cleaned = prefix.strip().strip("/")
    return f"{cleaned}/" if cleaned else ""


def build_s3_upload_config(args: argparse.Namespace) -> S3UploadConfig | None:
    """
    Construct an S3UploadConfig instance from parsed CLI arguments or env vars.

    Args:
        args (argparse.Namespace): Parsed command-line arguments.

    Returns:
        S3UploadConfig | None: Constructed configuration object, or None if no bucket specified.
    """
    bucket = args.s3_bucket or os.getenv("S3_BUCKET")
    if not bucket:
        return None

    return S3UploadConfig(
        bucket=bucket,
        prefix=_normalise_s3_prefix(args.s3_prefix),
        endpoint_url=args.s3_endpoint_url,
        region=args.s3_region,
        acl=args.s3_acl,
        access_key_id=args.s3_access_key_id,
        secret_access_key=args.s3_secret_access_key,
    )


def create_s3_client(config: S3UploadConfig) -> Any:
    """
    Create a Boto3 S3 client initialized with the provided configuration.

    Args:
        config (S3UploadConfig): S3 configuration parameters.

    Returns:
        Any: Initialized Boto3 S3 client object.
    """
    session = boto3.session.Session(region_name=config.region)
    client_args: dict[str, Any] = {"endpoint_url": config.endpoint_url}

    if config.access_key_id and config.secret_access_key:
        client_args["aws_access_key_id"] = config.access_key_id
        client_args["aws_secret_access_key"] = config.secret_access_key

    return session.client("s3", **client_args)


def safe_filename(value: str) -> str:
    """
    Sanitize arbitrary string values into safe filename path segments.

    Args:
        value (str): Raw string to sanitize.

    Returns:
        str: Cleaned string containing only valid filename characters.

    Examples:
        >>> safe_filename("  Hello World!  ")
        'Hello_World'
        >>> safe_filename("vocop:cluster/123#test")
        'vocop_cluster_123_test'
        >>> safe_filename("  ---  ")
        'entity'
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("._-")
    return cleaned or "entity"


def safe_segment(value: str) -> str:
    """
    Sanitize a URI segment for use in filesystem directory paths.

    Args:
        value (str): Raw path segment to sanitize.

    Returns:
        str: Cleaned segment string.

    Examples:
        >>> safe_segment("concept:123")
        'concept_123'
        >>> safe_segment("   ")
        'entity'
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned.strip("._-") or "entity"


def local_identifier(uri: str | Node) -> str:
    """
    Extract local identifier fragment from a URI string or RDF term node.

    Args:
        uri (str | Node): URI string or RDF node to extract identifier from.

    Returns:
        str: Trailing segment of the URI.

    Examples:
        >>> local_identifier("https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/person:123")
        'person:123'
        >>> local_identifier("https://example.org/thesaurus/concept_42/")
        'concept_42'
    """
    return str(uri).rstrip("/").rsplit("/", 1)[-1]


def identifier_path(identifier: str) -> str:
    """
    Convert a colon-separated identifier into a relative directory path.

    Args:
        identifier (str): Colon-separated identifier string.

    Returns:
        str: Relative filesystem path.

    Examples:
        >>> identifier_path("thesaurus:concept_1")
        'thesaurus/concept_1'
        >>> identifier_path("entity")
        'entity'
    """
    parts = [safe_segment(part) for part in identifier.split(":") if part]
    if not parts:
        parts = ["entity"]
    return os.path.join(*parts)


def local_identifier_path(uri: str | Node) -> str:
    """
    Extract local identifier from URI and construct relative directory output path.

    Args:
        uri (str | Node): Resource URI reference or string.

    Returns:
        str: Relative directory path for the resource.

    Examples:
        >>> local_identifier_path("https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/person:123")
        'person/123'
    """
    tail = local_identifier(uri)
    parts = [safe_filename(part) for part in tail.split(":") if part]
    if not parts:
        parts = ["entity"]
    return os.path.join(*parts)


def xml_element_name(name: str) -> str:
    """
    Sanitize CSV column header string into a valid XML element tag name.

    Args:
        name (str): Raw CSV column header string.

    Returns:
        str: Valid XML element tag name.

    Examples:
        >>> xml_element_name("First Name")
        'First_Name'
        >>> xml_element_name("123column")
        'column_123column'
        >>> xml_element_name("")
        'column'
    """
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    if not cleaned:
        return "column"
    if not re.match(r"[A-Za-z_]", cleaned[0]):
        cleaned = f"column_{cleaned}"
    return cleaned


def cell_to_text(value: object) -> str:
    """
    Convert an openpyxl cell value or raw object to its string representation.

    Args:
        value (object): Cell content value or object.

    Returns:
        str: String value or ISO-formatted date string.

    Examples:
        >>> cell_to_text(None)
        ''
        >>> cell_to_text("Hello")
        'Hello'
        >>> cell_to_text(date(2025, 1, 15))
        '2025-01-15'
        >>> cell_to_text(datetime(2025, 1, 15, 10, 30, 0))
        '2025-01-15 10:30:00'
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def expand_date_literal(lexical_value: str, suffix: str) -> str:
    """
    Expand short date string (YYYY, YYYY-MM, or YYYY-MM-DD) with appropriate ISO timestamp.

    Args:
        lexical_value (str): Raw date string (e.g. "1650", "1650-05", or "1650-05-20").
        suffix (str): Timestamp suffix ("T00:00:00" or "T23:59:59").

    Returns:
        str: Expanded ISO date string.

    Raises:
        ValueError: If lexical_value length is invalid.

    Examples:
        >>> expand_date_literal("1650", "T00:00:00")
        '1650-01-01'
        >>> expand_date_literal("1650", "T23:59:59")
        '1650-12-31'
        >>> expand_date_literal("1650-05", "T00:00:00")
        '1650-05-01'
        >>> expand_date_literal("1650-05", "T23:59:59")
        '1650-05-31'
        >>> expand_date_literal("1650-05-20", "T00:00:00")
        '1650-05-20'
        >>> expand_date_literal("800", "T00:00:00")
        '0800-01-01'
    """
    if len(lexical_value) == 10:
        return lexical_value
    if len(lexical_value) < 4:
        lexical_value = lexical_value.zfill(4)
    if len(lexical_value) == 4:
        return (
            f"{lexical_value}-01-01"
            if "00:00:00" in suffix
            else f"{lexical_value}-12-31"
        )
    if len(lexical_value) == 7 and lexical_value[4] == "-":
        year, month = int(lexical_value[:4]), int(lexical_value[5:7])
        if "00:00:00" in suffix:
            return f"{lexical_value}-01"
        else:
            last_day = calendar.monthrange(year, month)[1]
            return f"{lexical_value}-{last_day:02d}"
    raise ValueError(f"Unexpected date format: {lexical_value}")


def serialise_framed_json(framed: Any, gzipped: bool) -> bytes:
    """
    Serialize framed JSON structure into UTF-8 encoded bytes, optionally compressing with gzip.

    Args:
        framed (Any): JSON-LD structure or Python dictionary/list to serialize.
        gzipped (bool): Whether to compress output payload using gzip.

    Returns:
        bytes: Encoded JSON payload bytes.

    Examples:
        >>> data = {"@id": "http://example.org/1"}
        >>> payload = serialise_framed_json(data, gzipped=False)
        >>> b'http://example.org/1' in payload
        True
    """
    payload = json.dumps(framed, ensure_ascii=False, indent=2).encode("utf-8")
    if gzipped:
        return gzip.compress(payload)
    return payload


def output_bytes(
    payload: bytes,
    relative_path: str,
    output_dir: str,
    gzipped: bool,
    s3_client: Any | None,
    s3_config: S3UploadConfig | None,
    content_type: str = "application/octet-stream",
) -> None:
    """
    Output a raw byte payload either to local disk or directly to S3 object store.

    Args:
        payload (bytes): Encoded payload bytes to write.
        relative_path (str): Relative file output path (e.g. "document/123.json").
        output_dir (str): Base local output directory.
        gzipped (bool): Whether payload is gzip compressed (sets ContentEncoding on S3).
        s3_client (Any, optional): Initialized Boto3 S3 client. Defaults to None.
        s3_config (S3UploadConfig, optional): S3 configuration object. Defaults to None.
        content_type (str, optional): Value for the S3 ContentType header. Defaults to
            "application/octet-stream".
    """
    if s3_client and s3_config:
        object_key = f"{s3_config.prefix}{relative_path.replace(os.sep, '/')}"
        put_args: dict[str, Any] = {
            "Bucket": s3_config.bucket,
            "Key": object_key,
            "Body": payload,
            "ContentType": content_type,
        }
        if gzipped:
            put_args["ContentEncoding"] = "gzip"
        if getattr(s3_config, "acl", None):
            put_args["ACL"] = s3_config.acl

        s3_client.put_object(**put_args)
        return

    target_path = os.path.join(output_dir, relative_path)
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    with open(target_path, "wb") as outfile:
        outfile.write(payload)


def output_framed_json(
    framed: Any,
    relative_path: str,
    output_dir: str,
    gzipped: bool,
    s3_client: Any | None,
    s3_config: S3UploadConfig | None,
) -> None:
    """
    Output framed JSON payload either to local disk or directly to S3 object store.

    Args:
        framed (Any): Framed JSON data structure.
        relative_path (str): Relative file output path (e.g. "person/123.json").
        output_dir (str): Base local output directory.
        gzipped (bool): Whether output should be gzip compressed.
        s3_client (Any, optional): Initialized Boto3 S3 client. Defaults to None.
        s3_config (S3UploadConfig, optional): S3 configuration object. Defaults to None.
    """
    payload = serialise_framed_json(framed, gzipped=gzipped)
    output_bytes(
        payload,
        relative_path,
        output_dir,
        gzipped,
        s3_client,
        s3_config,
        content_type="application/ld+json; charset=utf-8",
    )


def html_to_markdown(text: str) -> str:
    """Convert HTML-formatted text strings into Markdown format.

    Args:
        text (str): Input text string, potentially containing HTML tags.

    Returns:
        str: Converted markdown text string with unescaped HTML entities.

    Examples:
        >>> html_to_markdown("<p>Hello <b>World</b>!</p>")
        'Hello **World**!'
        >>> html_to_markdown('<a href="https://example.org">Example</a>')
        '[Example](https://example.org)'
        >>> html_to_markdown("<em>Important</em> &amp; <strong>Crucial</strong>")
        '*Important* & **Crucial**'
        >>> html_to_markdown("Plain text without HTML")
        'Plain text without HTML'
    """
    if not text or not isinstance(text, str):
        return text

    if "<" not in text and ">" not in text and "&" not in text:
        return text

    text = re.sub(
        r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        r"[\2](\1)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"<(?:b|strong)>(.*?)</(?:b|strong)>",
        r"**\1**",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"<(?:i|em)>(.*?)</(?:i|em)>",
        r"*\1*",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"<code>(.*?)</code>",
        r"`\1`",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"<p>(.*?)</p>",
        r"\1\n\n",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"<br\s*/?>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"<li>(.*?)</li>",
        r"- \1\n",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"</?(?:ul|ol|div|span)[^>]*>",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)

    lines = [line.rstrip() for line in text.splitlines()]
    text = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def convert_html_literals_to_markdown(
    graph: Any,
    predicates: tuple[URIRef, ...] = (SKOS.definition, DCTERMS.references),
) -> Any:
    """
    Convert HTML-formatted literal values in an RDF graph or dataset to Markdown for specified predicates.

    Args:
        graph (Any): RDF graph or dataset to update.
        predicates (tuple[URIRef, ...], optional): Predicates whose literal values should be checked and converted.
            Defaults to (SKOS.definition, DCTERMS.references).

    Returns:
        Any: The updated RDF graph or dataset.

    Examples:
        >>> from rdflib import Graph, Literal, URIRef, Namespace
        >>> SKOS_NS = Namespace("http://www.w3.org/2004/02/skos/core#")
        >>> g = Graph()
        >>> res = URIRef("http://example.org/concept1")
        >>> _ = g.add((res, SKOS_NS["definition"], Literal("<p>Some <b>definition</b></p>")))
        >>> _ = convert_html_literals_to_markdown(g, (SKOS_NS["definition"],))
        >>> str(list(g.objects(res, SKOS_NS["definition"]))[0])
        'Some **definition**'
    """
    for pred in predicates:
        for sub, p, obj in set(graph.triples((None, pred, None))):
            if isinstance(obj, Literal):
                raw_str = str(obj)
                converted_str = html_to_markdown(raw_str)
                if converted_str != raw_str:
                    new_literal = Literal(
                        converted_str,
                        lang=obj.language,
                        datatype=obj.datatype,
                    )
                    graph.remove((sub, p, obj))
                    graph.add((sub, p, new_literal))
    return graph


OLD_BASE = "https://globalise.example.com/"
SARI_BASE = "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/"


@lru_cache(maxsize=None)
def transform_sari_uri(uri: str) -> str:
    """
    Transform a single sari-namespace URI from slash to colon-hash pattern.

    Args:
        uri (str): Raw input URI string to transform.

    Returns:
        str: Transformed URI string with colon and hash fragment formatting.

    Examples:
        >>> transform_sari_uri("https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/person/vocop_cluster_134981")
        'https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/person:vocop_cluster_134981'
        >>> transform_sari_uri("https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/person/vocop_cluster_134981/social_status/HASH")
        'https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/person:vocop_cluster_134981#social_status:HASH'
        >>> transform_sari_uri("https://globalise.example.com/person/vocop_cluster_134981")
        'https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/person:vocop_cluster_134981'
    """
    if uri.startswith(OLD_BASE):
        uri = SARI_BASE + uri[len(OLD_BASE) :]

    if not uri.startswith(SARI_BASE):
        return uri

    if " " in uri:
        uri = SARI_BASE + quote(uri[len(SARI_BASE) :], safe="/:@!$&'()*+,;=?#")

    path = uri[len(SARI_BASE) :]

    if not path or "/" not in path:
        return uri

    parts = path.split("/")
    if len(parts) < 2:
        return uri

    result = f"{parts[0]}:{parts[1]}"

    i = 2
    first_fragment = True
    while i < len(parts):
        sep = "#" if first_fragment else ":"
        first_fragment = False
        if i + 1 < len(parts):
            result += f"{sep}{parts[i]}:{parts[i + 1]}"
            i += 2
        else:
            result += f"{sep}{parts[i]}"
            i += 1

    return SARI_BASE + result


def normalize_uris(graph: Graph) -> int:
    """
    Transform all SARI URIs in an RDF graph from slash-separated to colon-hash format.

    Args:
        graph (Graph): The RDF graph to process and normalize in place.

    Returns:
        int: Number of triples modified.

    Examples:
        >>> from rdflib import Graph, URIRef, RDFS
        >>> g = Graph()
        >>> _ = g.add((URIRef("https://globalise.example.com/place/GLOB_1"), RDFS.label, URIRef("https://globalise.example.com/place/GLOB_1/name/1")))
        >>> normalize_uris(g)
        1
        >>> sorted(str(s) for s in g.subjects())
        ['https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/place:GLOB_1']
        >>> sorted(str(o) for o in g.objects())
        ['https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/place:GLOB_1#name:1']
    """
    replacements: list[tuple[Node, Node, Node, Node, Node, Node]] = []

    for s, p, o in graph.triples((None, None, None)):
        new_s = URIRef(transform_sari_uri(str(s))) if isinstance(s, URIRef) else s
        new_p = URIRef(transform_sari_uri(str(p))) if isinstance(p, URIRef) else p
        new_o = URIRef(transform_sari_uri(str(o))) if isinstance(o, URIRef) else o

        if new_s != s or new_p != p or new_o != o:
            replacements.append((s, p, o, new_s, new_p, new_o))

    for old_s, old_p, old_o, new_s, new_p, new_o in replacements:
        graph.remove((old_s, old_p, old_o))
        graph.add((new_s, new_p, new_o))

    graph.bind("sari", Namespace(SARI_BASE), override=True)
    return len(replacements)


def convert_hash_uris_to_bnodes(
    graph: Graph, uri_token: str = PROJECT_URI_TOKEN
) -> int:
    """
    Convert SARI component URIRefs containing '#' and the specified URI token into blank nodes (BNode).

    Args:
        graph (Graph): The RDF graph to process and normalize in place.
        uri_token (str, optional): Token to identify project URIs. Defaults to PROJECT_URI_TOKEN.

    Returns:
        int: Number of unique URIs converted to blank nodes.

    Examples:
        >>> from rdflib import Graph, Literal, URIRef, BNode, RDFS
        >>> g = Graph()
        >>> uri1 = URIRef("https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/place:GLOB_1#declarative_place:123")
        >>> _ = g.add((URIRef("https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/place:GLOB_1"), RDFS.label, uri1))
        >>> _ = g.add((uri1, RDFS.label, Literal("Declarative Place")))
        >>> num_converted = convert_hash_uris_to_bnodes(g)
        >>> num_converted
        1
        >>> any(isinstance(o, BNode) for o in g.objects())
        True
    """
    uri_to_bnode: dict[URIRef, BNode] = {}

    for s, p, o in graph.triples((None, None, None)):
        if isinstance(s, URIRef) and "#" in str(s) and uri_token in str(s):
            if s not in uri_to_bnode:
                uri_to_bnode[s] = BNode()
        if isinstance(o, URIRef) and "#" in str(o) and uri_token in str(o):
            if o not in uri_to_bnode:
                uri_to_bnode[o] = BNode()

    if not uri_to_bnode:
        return 0

    replacements: list[tuple[Node, Node, Node, Node, Node, Node]] = []

    for s, p, o in graph.triples((None, None, None)):
        new_s = uri_to_bnode[s] if isinstance(s, URIRef) and s in uri_to_bnode else s
        new_o = uri_to_bnode[o] if isinstance(o, URIRef) and o in uri_to_bnode else o

        if new_s != s or new_o != o:
            replacements.append((s, p, o, new_s, p, new_o))

    for old_s, old_p, old_o, new_s, new_p, new_o in replacements:
        graph.remove((old_s, old_p, old_o))
        graph.add((new_s, new_p, new_o))

    return len(uri_to_bnode)


OLD_CONCEPT_BASE_PATTERN = re.compile(
    r"^https?://digitaalerfgoed\.poolparty\.biz/globalise/(.*)$"
)
NEW_CONCEPT_BASE = "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/thesaurus:"


def load_concept_uri_replacements(
    xlsx_path: str | None = None,
) -> dict[URIRef, URIRef]:
    """
    Load mapping of deleted concept URIs to replacement concept URIs from an Excel file,
    rewriting PoolParty base URIs to SARI thesaurus base URIs.

    Args:
        xlsx_path (str, optional): Path to the Excel file containing old and new concept URIs.
            Defaults to data/input/deleted URI's and concepts.xlsx.

    Returns:
        dict[URIRef, URIRef]: Dictionary mapping old concept URIRefs to replacement URIRefs.

    Examples:
        >>> mapping = load_concept_uri_replacements()
        >>> isinstance(mapping, dict)
        True
    """
    if xlsx_path is None:
        xlsx_path = os.path.join(
            BASE_DIR, "data", "input", "deleted URI's and concepts.xlsx"
        )

    if not os.path.isfile(xlsx_path):
        return {}

    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    sheet = wb.active
    if sheet is None:
        return {}

    mapping: dict[URIRef, URIRef] = {}
    for row in sheet.iter_rows(values_only=True):
        if not row or len(row) < 2:
            continue
        old_val, new_val = row[0], row[1]
        if not old_val or not new_val:
            continue
        old_str = str(old_val).strip()
        new_str = str(new_val).strip()
        if old_str.lower() in ("deleted uri", "old uri") or new_str.lower() in (
            "concept was merged into",
            "new uri",
        ):
            continue
        if ";" in new_str:
            new_str = new_str.split(";")[0].strip()
        if ";" in old_str:
            old_uris = [u.strip() for u in old_str.split(";") if u.strip()]
        else:
            old_uris = [old_str]

        # Apply simple replace of base URIs
        new_str_replaced = OLD_CONCEPT_BASE_PATTERN.sub(
            rf"{NEW_CONCEPT_BASE}\1", new_str
        )
        new_uri = URIRef(new_str_replaced)

        for u in old_uris:
            if u and new_str:
                u_replaced = OLD_CONCEPT_BASE_PATTERN.sub(rf"{NEW_CONCEPT_BASE}\1", u)
                mapping[URIRef(u)] = new_uri
                mapping[URIRef(u_replaced)] = new_uri

    wb.close()
    return mapping


def replace_concept_uris(
    graph: Any,
    mapping: dict[URIRef, URIRef] | None = None,
    xlsx_path: str | None = None,
) -> int:
    """
    Replace deprecated or deleted concept URIs with their new URIs in an RDF graph or dataset.

    Args:
        graph (Any): Target RDF graph or dataset to modify in place.
        mapping (dict[URIRef, URIRef], optional): Mapping from old URIRef to new URIRef. If None,
            loads replacements from the Excel file specified by xlsx_path or the default location.
        xlsx_path (str, optional): Custom path to the Excel file containing URI replacements.

    Returns:
        int: Number of triples/quads modified in the graph or dataset.

    Examples:
        >>> from rdflib import Graph, URIRef, Literal
        >>> g = Graph()
        >>> old_u = URIRef("https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/thesaurus:old")
        >>> new_u = URIRef("https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/thesaurus:new")
        >>> p = URIRef("https://example.org/property")
        >>> _ = g.add((URIRef("https://example.org/entity/1"), p, old_u))
        >>> replace_concept_uris(g, mapping={old_u: new_u})
        1
        >>> list(g.objects(URIRef("https://example.org/entity/1"), p))
        [rdflib.term.URIRef('https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/thesaurus:new')]
    """
    if mapping is None:
        mapping = (
            load_concept_uri_replacements(xlsx_path)
            if xlsx_path
            else load_concept_uri_replacements()
        )

    if not mapping:
        return 0

    if isinstance(graph, Dataset):
        quads_to_remove: list[tuple[Node, Node, Node, Any]] = []
        quads_to_add: list[tuple[Node, Node, Node, Any]] = []
        for s, p, o, ctx in graph.quads((None, None, None, None)):
            new_s = mapping[s] if isinstance(s, URIRef) and s in mapping else s
            new_p = mapping[p] if isinstance(p, URIRef) and p in mapping else p
            new_o = mapping[o] if isinstance(o, URIRef) and o in mapping else o
            if new_s != s or new_p != p or new_o != o:
                quads_to_remove.append((s, p, o, ctx))
                quads_to_add.append((new_s, new_p, new_o, ctx))
        for q in quads_to_remove:
            graph.remove(q)  # type: ignore[arg-type]
        for q in quads_to_add:
            graph.add(q)  # type: ignore[arg-type]
        return len(quads_to_remove)
    else:
        triples_to_remove: list[tuple[Node, Node, Node]] = []
        triples_to_add: list[tuple[Node, Node, Node]] = []
        for s, p, o in graph.triples((None, None, None)):
            new_s = mapping[s] if isinstance(s, URIRef) and s in mapping else s
            new_p = mapping[p] if isinstance(p, URIRef) and p in mapping else p
            new_o = mapping[o] if isinstance(o, URIRef) and o in mapping else o
            if new_s != s or new_p != p or new_o != o:
                triples_to_remove.append((s, p, o))
                triples_to_add.append((new_s, new_p, new_o))
        for t in triples_to_remove:
            graph.remove(t)
        for t in triples_to_add:
            graph.add(t)
        return len(triples_to_remove)


if __name__ == "__main__":
    import doctest

    results = doctest.testmod()
    print(f"Doctest results: {results}")
