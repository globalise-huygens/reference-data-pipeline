#!/usr/bin/env python3
"""
Export framed JSON files from Turtle graphs for any category.
"""

import argparse
import copy
import csv
import glob
import json
import os
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Iterable

import duckdb
import requests_cache
from pyld import jsonld  # type: ignore[import-untyped]
from rdflib import Dataset, Graph, Literal, URIRef, Namespace
from rdflib.namespace import DCTERMS, RDF, RDFS, SKOS
from rdflib.term import Node
from tqdm import tqdm

from utils import (
    S3UploadConfig,
    add_s3_arguments,
    build_s3_upload_config,
    convert_html_literals_to_markdown,
    create_s3_client,
    identifier_path,
    local_identifier,
    local_identifier_path,
    output_framed_json,
    safe_segment,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONTEXT = "https://objectstore.surf.nl/87435b768620494e8e911c83d1997f24:globalise-data/contexts/globalise.json"
PROJECT_URI_TOKEN = "data.globalise.huygens.knaw.nl"
URI_BASE = "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/"
SARI_BASE = "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/"

OA = Namespace("http://www.w3.org/ns/oa#")
CRM = Namespace("http://www.cidoc-crm.org/cidoc-crm/")
CRMDIG = Namespace("http://www.ics.forth.gr/isl/CRMdig/")

CATEGORY_HYDRA_METADATA: dict[str, dict[str, str]] = {
    "person": {
        "title": "Person Collection",
        "type": "Person",
        "class_uri": "http://www.cidoc-crm.org/cidoc-crm/E21_Person",
        "uri_prefix": "person:",
        "page_prefix": "",
    },
    "place": {
        "title": "Place Collection",
        "type": "Place",
        "class_uri": "http://www.cidoc-crm.org/cidoc-crm/E53_Place",
        "uri_prefix": "place:",
        "page_prefix": "",
    },
    "organization": {
        "title": "Organization Collection",
        "type": "Group",
        "class_uri": "http://www.cidoc-crm.org/cidoc-crm/E74_Group",
        "uri_prefix": "group:",
        "page_prefix": "",
    },
    "polity": {
        "title": "Polity Collection",
        "type": "Polity",
        "class_uri": "https://ontology.swissartresearch.net/aaao/ZE39_Polity",
        "uri_prefix": "polity:",
        "page_prefix": "",
    },
    "rulership": {
        "title": "Rulership Collection",
        "type": "Rulership",
        "class_uri": "https://ontology.swissartresearch.net/pwro/WE2_Sovereignty",
        "uri_prefix": "rulership:",
        "page_prefix": "",
    },
    "ship": {
        "title": "Ship Collection",
        "type": "Ship",
        "class_uri": "http://www.cidoc-crm.org/cidoc-crm/E22_Human-Made_Object",
        "uri_prefix": "ship:",
        "page_prefix": "",
    },
    "voyage": {
        "title": "Voyage Collection",
        "type": "Voyage",
        "class_uri": "https://ontology.swissartresearch.net/pwro/WE7_Voyage",
        "uri_prefix": "voyage:",
        "page_prefix": "",
    },
    "conversion": {
        "title": "Conversion Collection",
        "type": "Conversion",
        "class_uri": "https://w3id.org/globalise/ontology/G1_Financial_Exchange",
        "uri_prefix": "conversion:",
        "page_prefix": "",
    },
    "occurrence": {
        "title": "Occurrence Collection",
        "type": "Occurrence",
        "class_uri": "http://www.cidoc-crm.org/cidoc-crm/E5_Event",
        "uri_prefix": "occurrence:",
        "page_prefix": "",
    },
    "concept": {
        "title": "Concept Collection",
        "type": "Concept",
        "class_uri": "http://www.w3.org/2004/02/skos/core#Concept",
        "uri_prefix": "thesaurus:",
        "page_prefix": "concept-",
    },
    "conceptscheme": {
        "title": "ConceptScheme Collection",
        "type": "ConceptScheme",
        "class_uri": "http://www.w3.org/2004/02/skos/core#ConceptScheme",
        "uri_prefix": "thesaurus:",
        "page_prefix": "conceptscheme-",
    },
    "collection": {
        "title": "SKOS Collection",
        "type": "SKOSCollection",
        "class_uri": "http://www.w3.org/2004/02/skos/core#Collection",
        "uri_prefix": "thesaurus:",
        "page_prefix": "collection-",
    },
}

requests_cache.install_cache(".cache", backend="sqlite", expire_after=3600)

CONCEPT_RELATION_PREDICATES = frozenset(
    {
        SKOS.broader,
        SKOS.narrower,
        SKOS.related,
        SKOS.broaderTransitive,
        SKOS.narrowerTransitive,
        SKOS.exactMatch,
        SKOS.closeMatch,
        SKOS.broadMatch,
        SKOS.narrowMatch,
        SKOS.relatedMatch,
    }
)

EXCLUDED_PREDICATES = frozenset(
    {
        DCTERMS.contributor,
        DCTERMS.created,
        DCTERMS.creator,
        DCTERMS.modified,
        DCTERMS.publisher,
        DCTERMS.title,
    }
)

PREDICATES_FOR_SHALLOW_RESOURCE = frozenset(
    {
        RDF.type,
        RDFS.label,
        SKOS.prefLabel,
        SKOS.altLabel,
    }
)


ANNOTATION_ENTITY_TYPE_BY_ROOT_TYPE = {
    "http://www.cidoc-crm.org/cidoc-crm/E21_Person": "Person",
    "http://www.cidoc-crm.org/cidoc-crm/E53_Place": "Place",
    "http://www.cidoc-crm.org/cidoc-crm/E74_Group": "Group",
    "http://www.cidoc-crm.org/cidoc-crm/E22_Human-Made_Object": "HumanMadeObject",
}


def parse_args(args_list: list[str] | None = None) -> argparse.Namespace:
    """
    Parse command-line arguments for the JSON export script.

    Args:
        args_list (list[str], optional): CLI arguments list for testing. Defaults to None.

    Returns:
        argparse.Namespace: Parsed command-line arguments.

    Examples:
        >>> parse_args(["entity", "in.ttl", "out", "frame.json", "http://example.org/Type"]).mode
        'entity'
    """
    parser = argparse.ArgumentParser(
        description="Export framed JSON files from Turtle graphs."
    )
    parser.add_argument(
        "mode",
        choices=["entity", "thesaurus", "catalog"],
        help="Conversion mode: 'entity' for entities, 'thesaurus' for concepts, 'catalog' for catalog index",
    )
    parser.add_argument("input_ttl", nargs="?")
    parser.add_argument("output_dir", nargs="?")
    parser.add_argument("frame_jsonld", nargs="?")
    parser.add_argument("root_type_uri", nargs="?")
    parser.add_argument(
        "--gzipped", action="store_true", help="Output gzipped JSON files"
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=1000,
        help="Items per page for Hydra collection index pages (default: 1000)",
    )
    parser.add_argument(
        "--annotation-page-size",
        type=int,
        default=1000,
        help="Items per AnnotationPage (default: 1000)",
    )
    parser.add_argument(
        "--category-key",
        help="Override category key for Hydra collection",
    )
    parser.add_argument(
        "--category-title",
        help="Override collection title for Hydra collection",
    )
    parser.add_argument(
        "--member-type",
        help="Override member @type for Hydra collection",
    )
    parser.add_argument(
        "--class-uri",
        help="Override RDF class URI for member type in Hydra collection context",
    )
    parser.add_argument(
        "--skip-hydra",
        action="store_true",
        help="Skip generating Hydra collection pages and catalog index",
    )
    parser.add_argument(
        "--nprocs",
        type=int,
        default=int(os.getenv("NPROCS", "6")),
        help="Number of parallel workers for chunk framing (default: 6)",
    )
    parser.add_argument(
        "--persons-csv",
        help="Path to persons.csv for generating Hydra collection index",
    )
    parser.add_argument(
        "--links-parquet",
        default=os.path.join(BASE_DIR, "data", "input", "links_data.parquet"),
        help="Parquet inventory of corpus annotations (default: data/input/links_data.parquet)",
    )
    parser.add_argument(
        "--skip-annotations",
        action="store_true",
        help="Skip corpus AnnotationCollection generation for entity and concept frames",
    )
    add_s3_arguments(parser)

    return parser.parse_args(args_list)


def parse_rdf_graph(path: str) -> Graph:
    """
    Parse an RDF file (.ttl or .trig) into an RDF Graph or Dataset.

    Args:
        path (str): Filepath to the RDF graph or dataset.

    Returns:
        Graph: Parsed RDF graph or dataset object.
    """
    _, ext = os.path.splitext(path.lower())

    if ext == ".trig":
        dataset = Dataset(default_union=True)
        dataset.parse(path, format="trig")
        return dataset

    ttl_graph = Graph()
    ttl_graph.parse(path, format="turtle")

    return ttl_graph


def bind_namespaces(target: Graph, namespaces: Iterable[tuple[str, URIRef]]) -> None:
    """
    Bind namespace prefixes onto the target RDF graph.

    Args:
        target (Graph): The RDF graph to bind prefixes to.
        namespaces (Iterable[tuple[str, URIRef]]): Collection of prefix and namespace URIRef pairs.

    Examples:
        >>> from rdflib import Graph, URIRef
        >>> graph = Graph()
        >>> bind_namespaces(graph, [("example", URIRef("https://example.org/"))])
        >>> str(dict(graph.namespaces())["example"])
        'https://example.org/'
    """
    for prefix, namespace in namespaces:
        target.bind(prefix, namespace, replace=False)


def frame_graph(graph: Graph, frame_doc: dict[str, Any], root_id: str) -> Any:
    """
    Frame an RDF graph into a JSON-LD dictionary using PyLD.

    Args:
        graph (Graph): RDF graph to frame.
        frame_doc (dict[str, Any]): JSON-LD frame specification dictionary.
        root_id (str): Optional root entity URI identifier.

    Returns:
        Any: Framed JSON-LD dictionary or list structure.
    """
    frame_doc = copy.deepcopy(frame_doc)

    if root_id:
        frame_doc["@id"] = root_id
    else:
        frame_doc.pop("@id", None)

    framed = jsonld.frame(json.loads(graph.serialize(format="json-ld")), frame_doc)

    return framed


def load_corpus_annotations(
    parquet_path: str,
    entity_type: str | None = None,
    uri_column: str = "entity_uri",
) -> dict[str, list[str]]:
    """
    Read corpus annotation identifiers grouped by reference-data or concept URI.

    Queries the parquet file with DuckDB to push down filters and group
    distinct annotations per URI efficiently without reading the full dataset into Python.

    Args:
        parquet_path: Path to the links-data parquet file.
        entity_type: Optional value of the parquet ``entity_type`` column to filter by.
        uri_column: Column containing target URIs ('entity_uri' or 'concept_uri').

    Returns:
        Mapping of reference-data or concept URI to sorted corpus annotation URIs.

    Examples:
        >>> import tempfile
        >>> from pathlib import Path
        >>> import duckdb
        >>> with tempfile.TemporaryDirectory() as directory:
        ...     parquet_path = Path(directory) / "links.parquet"
        ...     _ = duckdb.execute(
        ...         f"COPY (SELECT * FROM (VALUES "
        ...         f"('https://example.org/annotation/2', 'https://example.org/person:123', 'Person', 'https://example.org/thesaurus:123'), "
        ...         f"('https://example.org/annotation/1', 'https://example.org/person:123', 'Person', 'https://example.org/thesaurus:123'), "
        ...         f"('https://example.org/annotation/1', 'https://example.org/person:123', 'Person', 'https://example.org/thesaurus:123'), "
        ...         f"('https://example.org/annotation/3', 'https://example.org/place:456', 'Place', 'https://example.org/thesaurus:456')"
        ...         f") AS t(annotation_id, entity_uri, entity_type, concept_uri)) TO '{parquet_path}' (FORMAT PARQUET)"
        ...     )
        ...     load_corpus_annotations(str(parquet_path), "Person")
        ...     load_corpus_annotations(str(parquet_path), uri_column="concept_uri")
        {'https://example.org/person:123': ['https://example.org/annotation/1', 'https://example.org/annotation/2']}
        {'https://example.org/thesaurus:123': ['https://example.org/annotation/1', 'https://example.org/annotation/2'], 'https://example.org/thesaurus:456': ['https://example.org/annotation/3']}
    """
    if uri_column not in {"entity_uri", "concept_uri"}:
        raise ValueError(f"Unsupported uri_column: {uri_column}")

    where_clauses = [f"{uri_column} IS NOT NULL", "annotation_id IS NOT NULL"]
    params: list[str] = [parquet_path]
    if entity_type is not None:
        where_clauses.append("entity_type = ?")
        params.append(entity_type)

    where_sql = " AND ".join(where_clauses)
    query = f"""
        SELECT
            {uri_column},
            list(DISTINCT annotation_id ORDER BY annotation_id) AS annotations
        FROM read_parquet(?)
        WHERE {where_sql}
        GROUP BY {uri_column}
        ORDER BY {uri_column}
    """
    conn = duckdb.connect()
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    return {str(row[0]): [str(ann_id) for ann_id in row[1]] for row in rows}


def annotation_collection_uri(entity_uri: str) -> str:
    """
    Return the URI of an entity's corpus AnnotationCollection.

    Examples:
        >>> annotation_collection_uri("https://example.org/person:123")
        'https://example.org/person:123.annotations'
    """
    return f"{entity_uri}.annotations"


def add_annotation_collection_reference(graph: Graph, entity_uri: str) -> str:
    """
    Add an AnnotationCollection relation to an entity graph and return its label.

    Examples:
        >>> entity_uri = "https://example.org/person:123"
        >>> graph = Graph()
        >>> _ = graph.add((URIRef(entity_uri), RDFS.label, Literal("Anna Bijns")))
        >>> add_annotation_collection_reference(graph, entity_uri)
        'Anna Bijns'
        >>> collection_uri = URIRef("https://example.org/person:123.annotations")
        >>> (URIRef(entity_uri), CRM.P129i_is_subject_of, collection_uri) in graph
        True
        >>> set(graph.objects(collection_uri, RDF.type)) == {CRMDIG.D1_Digital_Object, OA.AnnotationCollection}
        True
        >>> list(graph.objects(collection_uri, RDFS.label))
        [rdflib.term.Literal('Corpus mentions of Anna Bijns')]
    """
    entity = URIRef(entity_uri)
    collection = URIRef(annotation_collection_uri(entity_uri))
    label = extract_title(graph, entity)

    graph.add((entity, CRM.P129i_is_subject_of, collection))

    graph.add((collection, RDF.type, CRMDIG.D1_Digital_Object))
    graph.add((collection, RDF.type, OA.AnnotationCollection))
    graph.add((collection, RDFS.label, Literal(f"Corpus mentions of {label}")))

    return label


def output_annotation_collection(
    entity_uri: str,
    entity_label: str,
    annotation_ids: list[str],
    output_dir: str,
    gzipped: bool = False,
    s3_client: Any | None = None,
    s3_config: S3UploadConfig | None = None,
    page_size: int = 1000,
) -> None:
    """
    Write an entity's AnnotationCollection and paginated AnnotationPages.

    Examples:
        >>> import tempfile
        >>> from pathlib import Path
        >>> entity_uri = "https://example.org/person:123"
        >>> annotation_ids = ["https://example.org/annotation/1", "https://example.org/annotation/2", "https://example.org/annotation/3"]
        >>> with tempfile.TemporaryDirectory() as directory:
        ...     output_annotation_collection(
        ...         entity_uri, "Anna Bijns", annotation_ids, directory, page_size=2
        ...     )
        ...     output_dir = Path(directory)
        ...     collection = json.loads((output_dir / "person" / "123.annotations.json").read_text())
        ...     first_page = json.loads((output_dir / "person" / "123.annotations" / "page-1.json").read_text())
        ...     second_page = json.loads((output_dir / "person" / "123.annotations" / "page-2.json").read_text())
        ...     (collection["total"], len(first_page["items"]), first_page["next"] == second_page["id"], len(second_page["items"]))
        (3, 2, True, 1)
    """
    collection_uri = annotation_collection_uri(entity_uri)
    relative_path = local_identifier_path(entity_uri)
    page_directory = f"{relative_path}.annotations"
    collection_output_name = f"{relative_path}.annotations.json"
    page_size = max(1, page_size)
    total_pages = max(1, (len(annotation_ids) + page_size - 1) // page_size)

    collection_doc = {
        "@context": [
            "http://www.w3.org/ns/anno.jsonld",
            "http://www.w3.org/ns/ldp.jsonld",
        ],
        "id": collection_uri,
        "type": ["BasicContainer", "AnnotationCollection"],
        "label": f"Corpus mentions of {entity_label}",
        "total": len(annotation_ids),
        "first": f"{collection_uri}/page-1.json",
        "last": f"{collection_uri}/page-{total_pages}.json",
    }
    output_framed_json(
        collection_doc,
        collection_output_name,
        output_dir,
        gzipped,
        s3_client,
        s3_config,
    )

    for page_number in range(1, total_pages + 1):
        page_uri = f"{collection_uri}/page-{page_number}.json"
        start_index = (page_number - 1) * page_size
        end_index = page_number * page_size
        page_doc: dict[str, Any] = {
            "@context": [
                "http://www.w3.org/ns/anno.jsonld",
                "http://www.w3.org/ns/ldp.jsonld",
            ],
            "id": page_uri,
            "type": "AnnotationPage",
            "partOf": collection_uri,
            "items": [
                {
                    "id": annotation_id,
                    "type": "Annotation",
                }
                for annotation_id in annotation_ids[start_index:end_index]
            ],
        }
        if page_number > 1:
            page_doc["previous"] = f"{collection_uri}/page-{page_number - 1}.json"
        if page_number < total_pages:
            page_doc["next"] = f"{collection_uri}/page-{page_number + 1}.json"

        output_framed_json(
            page_doc,
            os.path.join(page_directory, f"page-{page_number}.json"),
            output_dir,
            gzipped,
            s3_client,
            s3_config,
        )


def _process_entity_chunk(
    input_ttl: str,
    output_dir: str,
    frame_path: str,
    root_type_uri_str: str,
    gzipped: bool,
    s3_config_dict: dict[str, Any] | None,
    annotations_by_entity: dict[str, list[str]],
    annotation_page_size: int,
) -> int:
    resource_type = URIRef(root_type_uri_str)
    s3_config = S3UploadConfig(**s3_config_dict) if s3_config_dict else None
    s3_client = create_s3_client(s3_config) if s3_config else None

    g = parse_rdf_graph(input_ttl)
    for pred in EXCLUDED_PREDICATES:
        g.remove((None, pred, None))

    resources = {
        res
        for res in g.subjects(RDF.type, resource_type)
        if isinstance(res, URIRef) and PROJECT_URI_TOKEN in str(res)
    }

    if not resources:
        return 0

    with open(frame_path, "r", encoding="utf-8") as infile:
        frame_doc = json.load(infile)

    if not s3_config:
        os.makedirs(output_dir, exist_ok=True)

    for res in sorted(resources, key=str):
        annotation_ids = annotations_by_entity.get(str(res), [])
        relative_path = local_identifier_path(res)
        output_name = os.path.join(
            os.path.dirname(relative_path),
            f"{os.path.basename(relative_path)}.json",
        )

        if not s3_config:
            target_path = os.path.join(output_dir, output_name)
            if (
                not annotation_ids
                and os.path.exists(target_path)
                and os.path.getsize(target_path) > 0
            ):
                continue

        graph = g.cbd(res, include_reifications=False)
        graph = add_referenced_data(graph, g)

        if annotation_ids:
            entity_label = add_annotation_collection_reference(graph, str(res))
            output_annotation_collection(
                str(res),
                entity_label,
                annotation_ids,
                output_dir,
                gzipped,
                s3_client,
                s3_config,
                annotation_page_size,
            )
        framed = frame_graph(graph, frame_doc, str(res))
        output_framed_json(
            framed,
            output_name,
            output_dir,
            gzipped,
            s3_client,
            s3_config,
        )
    return len(resources)


def generate_hydra_collection_from_persons_csv(
    persons_csv_path: str,
    output_dir: str,
    gzipped: bool = False,
    s3_client: Any | None = None,
    s3_config: S3UploadConfig | None = None,
    page_size: int = 1000,
) -> None:
    meta = CATEGORY_HYDRA_METADATA.get("person", {})
    cat_title = meta.get("title", "Person Collection")
    m_type = meta.get("type", "Person")
    c_uri = meta.get("class_uri", "http://www.cidoc-crm.org/cidoc-crm/E21_Person")
    uri_prefix = meta.get("uri_prefix", "person:")

    page_context: list[Any] = [
        "http://www.w3.org/ns/hydra/context.jsonld",
        {
            m_type: c_uri,
        },
    ]

    members = []
    with open(persons_csv_path, "r", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            local_id = row["URI"]
            label = (row.get("rdfs:label") or "").strip() or local_id
            full_uri = f"{SARI_BASE}{uri_prefix}{local_id}"
            members.append(
                {
                    "@id": full_uri,
                    "@type": m_type,
                    "title": label,
                }
            )

    members.sort(key=lambda m: m["@id"])
    page_size = max(1, page_size)
    total_items = len(members)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    base_collection_uri = f"{URI_BASE}{uri_prefix}index"
    rel_folder = os.path.join("person", "index")

    for page_num in range(1, total_pages + 1):
        page_file = f"page-{page_num}.json"
        page_uri = f"{base_collection_uri}/{page_file}"
        relative_path = os.path.join(rel_folder, page_file)

        start_index = (page_num - 1) * page_size
        end_index = page_num * page_size
        page_members = members[start_index:end_index]

        view: dict[str, Any] = {
            "@id": page_uri,
            "@type": "PartialCollectionView",
            "first": f"{base_collection_uri}/page-1.json",
            "last": f"{base_collection_uri}/page-{total_pages}.json",
        }

        if page_num > 1:
            view["previous"] = f"{base_collection_uri}/page-{page_num - 1}.json"

        if page_num < total_pages:
            view["next"] = f"{base_collection_uri}/page-{page_num + 1}.json"

        collection_doc = {
            "@context": page_context,
            "@id": page_uri,
            "@type": "Collection",
            "title": cat_title,
            "totalItems": total_items,
            "member": page_members,
            "view": view,
        }

        output_framed_json(
            collection_doc,
            relative_path,
            output_dir,
            gzipped,
            s3_client,
            s3_config,
        )


def convert_entity(
    args: argparse.Namespace,
    gzipped: bool = False,
    s3_client: Any | None = None,
    s3_config: S3UploadConfig | None = None,
) -> int:
    """
    Convert entity RDF graphs (person, place, organization, etc.) to framed JSON.

    Args:
        args (argparse.Namespace): Parsed CLI arguments.
        gzipped (bool, optional): Whether output should be gzipped. Defaults to False.
        s3_client (Any, optional): Initialized Boto3 S3 client. Defaults to None.
        s3_config (S3UploadConfig, optional): S3 upload configuration. Defaults to None.

    Returns:
        int: Exit status code (0 for success, 1 for error).
    """
    input_path = os.path.abspath(os.path.expanduser(args.input_ttl))
    output_dir = os.path.abspath(os.path.expanduser(args.output_dir))
    frame_path = os.path.abspath(os.path.expanduser(args.frame_jsonld))
    resource_type = URIRef(args.root_type_uri)

    if not os.path.exists(input_path):
        print(f"Error: file or directory '{input_path}' not found.")
        return 1
    if not os.path.isfile(frame_path):
        print(f"Error: frame file '{frame_path}' not found.")
        return 1

    annotations_by_entity: dict[str, list[str]] = {}
    annotation_entity_type = ANNOTATION_ENTITY_TYPE_BY_ROOT_TYPE.get(str(resource_type))
    if annotation_entity_type and not getattr(args, "skip_annotations", False):
        links_parquet = os.path.abspath(os.path.expanduser(args.links_parquet))
        if not os.path.isfile(links_parquet):
            print(f"Error: links parquet file '{links_parquet}' not found.")
            return 1
        annotations_by_entity = load_corpus_annotations(
            links_parquet, annotation_entity_type
        )

    if os.path.isdir(input_path):
        ttl_files = sorted(
            glob.glob(os.path.join(input_path, "**", "*.ttl"), recursive=True)
        )
        if not ttl_files:
            print(f"Error: no .ttl files found in directory '{input_path}'.")
            return 1

        nprocs = getattr(args, "nprocs", 6)
        s3_dict = (
            {
                "bucket": s3_config.bucket,
                "prefix": s3_config.prefix,
                "endpoint_url": s3_config.endpoint_url,
                "region": s3_config.region,
                "acl": s3_config.acl,
                "access_key_id": s3_config.access_key_id,
                "secret_access_key": s3_config.secret_access_key,
            }
            if s3_config
            else None
        )

        print(
            f"Framing {len(ttl_files)} chunk TTL files in parallel (workers={nprocs})..."
        )
        with ProcessPoolExecutor(max_workers=nprocs) as executor:
            futures = [
                executor.submit(
                    _process_entity_chunk,
                    ttl_file,
                    output_dir,
                    frame_path,
                    str(resource_type),
                    gzipped,
                    s3_dict,
                    annotations_by_entity,
                    getattr(args, "annotation_page_size", 1000),
                )
                for ttl_file in ttl_files
            ]
            total_entities = sum(f.result() for f in futures)
        print(f"Framed {total_entities} entities across {len(ttl_files)} chunks.")

        if not getattr(args, "skip_hydra", False):
            category_key = (
                getattr(args, "category_key", None)
                or os.path.splitext(os.path.basename(frame_path))[0]
            )
            persons_csv = getattr(args, "persons_csv", None) or os.path.join(
                BASE_DIR, "data", "input", "person", "csv", "persons.csv"
            )
            if category_key == "person" and os.path.isfile(persons_csv):
                generate_hydra_collection_from_persons_csv(
                    persons_csv,
                    output_dir,
                    gzipped=gzipped,
                    s3_client=s3_client,
                    s3_config=s3_config,
                    page_size=getattr(args, "page_size", 1000),
                )
            generate_hydra_catalog(
                output_dir,
                gzipped=gzipped,
                s3_client=s3_client,
                s3_config=s3_config,
            )
        return 0

    g = parse_rdf_graph(input_path)

    for pred in EXCLUDED_PREDICATES:
        g.remove((None, pred, None))

    resources = {
        res
        for res in g.subjects(RDF.type, resource_type)
        if isinstance(res, URIRef) and PROJECT_URI_TOKEN in str(res)
    }

    if not resources:
        print(
            f"Error: no project root entities of type '{resource_type}' found in '{input_path}'."
        )
        return 1

    with open(frame_path, "r", encoding="utf-8") as infile:
        frame_doc = json.load(infile)

    if not s3_config:
        os.makedirs(output_dir, exist_ok=True)

    resource_list = sorted(resources, key=str)

    action = "Uploading" if s3_config else "Writing"
    for res in tqdm(resource_list, desc=f"{action} entity frames", unit="frame"):
        annotation_ids = annotations_by_entity.get(str(res), [])
        relative_path = local_identifier_path(res)
        output_name = os.path.join(
            os.path.dirname(relative_path),
            f"{os.path.basename(relative_path)}.json",
        )

        if not s3_config:
            target_path = os.path.join(output_dir, output_name)
            if (
                not annotation_ids
                and os.path.exists(target_path)
                and os.path.getsize(target_path) > 0
            ):
                continue

        graph = g.cbd(res, include_reifications=False)
        graph = add_referenced_data(graph, g)

        if annotation_ids:
            entity_label = add_annotation_collection_reference(graph, str(res))
            output_annotation_collection(
                str(res),
                entity_label,
                annotation_ids,
                output_dir,
                gzipped,
                s3_client,
                s3_config,
                getattr(args, "annotation_page_size", 1000),
            )
        framed = frame_graph(graph, frame_doc, str(res))
        output_framed_json(
            framed,
            output_name,
            output_dir,
            gzipped,
            s3_client,
            s3_config,
        )

    if not getattr(args, "skip_hydra", False):
        category_key = (
            getattr(args, "category_key", None)
            or os.path.splitext(os.path.basename(frame_path))[0]
        )
        generate_hydra_collection(
            g,
            resources,
            category_key,
            output_dir,
            gzipped=gzipped,
            s3_client=s3_client,
            s3_config=s3_config,
            page_size=getattr(args, "page_size", 1000),
            category_title=getattr(args, "category_title", None),
            member_type=getattr(args, "member_type", None),
            class_uri=getattr(args, "class_uri", None)
            or (
                str(args.root_type_uri)
                if getattr(args, "root_type_uri", None)
                else None
            ),
        )

        generate_hydra_catalog(
            output_dir,
            gzipped=gzipped,
            s3_client=s3_client,
            s3_config=s3_config,
        )

    if s3_config:
        print(
            f"Uploaded framed JSON files to s3://{s3_config.bucket}/{s3_config.prefix}"
        )
    else:
        print(f"Wrote framed JSON files to '{output_dir}'.")

    return 0


def convert_thesaurus(
    input_path: str,
    output_dir: str,
    frame_path: str,
    scheme_frame_path: str,
    collection_frame_path: str,
    gzipped: bool = False,
    s3_client: Any | None = None,
    s3_config: S3UploadConfig | None = None,
    page_size: int = 1000,
    links_parquet: str | None = None,
    skip_annotations: bool = False,
    annotation_page_size: int = 1000,
) -> int:
    """
    Convert SKOS thesaurus concept schemes, concepts, and collections to framed JSON.

    Args:
        input_path (str): Filepath to input .trig or .ttl file.
        output_dir (str): Output directory path.
        frame_path (str): Concept frame JSON-LD path.
        scheme_frame_path (str): ConceptScheme frame JSON-LD path.
        collection_frame_path (str): Collection frame JSON-LD path.
        gzipped (bool, optional): Whether output should be gzipped. Defaults to False.
        s3_client (Any, optional): Initialized Boto3 S3 client. Defaults to None.
        s3_config (S3UploadConfig, optional): S3 upload configuration. Defaults to None.
        page_size (int, optional): Page size for Hydra collections. Defaults to 1000.
        links_parquet (str, optional): Path to links parquet file for concept corpus annotations. Defaults to None.
        skip_annotations (bool, optional): Skip corpus annotations for concepts. Defaults to False.
        annotation_page_size (int, optional): Page size for concept AnnotationPages. Defaults to 1000.

    Returns:
        int: Exit status code (0 for success, 1 for error).
    """
    with open(frame_path, "r", encoding="utf-8") as infile:
        frame_doc = json.load(infile)

    with open(scheme_frame_path, "r", encoding="utf-8") as infile:
        scheme_frame_doc = json.load(infile)

    with open(collection_frame_path, "r", encoding="utf-8") as infile:
        collection_frame_doc = json.load(infile)

    annotations_by_concept: dict[str, list[str]] = {}
    if not skip_annotations and links_parquet:
        resolved_parquet = os.path.abspath(os.path.expanduser(links_parquet))
        if not os.path.isfile(resolved_parquet):
            print(f"Error: links parquet file '{resolved_parquet}' not found.")
            return 1
        annotations_by_concept = load_corpus_annotations(
            resolved_parquet, uri_column="concept_uri"
        )

    ds = parse_rdf_graph(input_path)

    ds = add_labels(
        ds,
        (SKOS.ConceptScheme, SKOS.Concept, SKOS.Collection),
        (SKOS.prefLabel, DCTERMS.title),
        RDFS.label,
        default_language="nl",
    )

    ds = add_labels(
        ds,
        (SKOS.ConceptScheme, SKOS.Collection),
        (DCTERMS.title,),
        SKOS.prefLabel,
        default_language=None,
    )

    for sub, pred, obj in set(ds.triples((None, DCTERMS.identifier, None))):
        if isinstance(obj, Literal):
            ds.add((sub, pred, Literal(str(obj))))
            ds.remove((sub, pred, obj))

    for sub, pred, obj in set(ds.triples((None, DCTERMS.description, None))):
        if isinstance(obj, Literal):
            ds.add((sub, SKOS.definition, obj))
            ds.remove((sub, pred, obj))

    ds = convert_html_literals_to_markdown(
        ds, predicates=(SKOS.definition, DCTERMS.references)
    )

    for pred in EXCLUDED_PREDICATES:
        ds.remove((None, pred, None))

    schemes = set(ds.subjects(RDF.type, SKOS.ConceptScheme))
    concepts = set(ds.subjects(RDF.type, SKOS.Concept))
    collections = set(ds.subjects(RDF.type, SKOS.Collection))

    resources = schemes.union(concepts).union(collections)

    if not s3_config:
        os.makedirs(output_dir, exist_ok=True)

    resource_list = sorted(resources, key=str)

    action = "Uploading" if s3_config else "Writing"
    for res in tqdm(resource_list, desc=f"{action} thesaurus frames", unit="frame"):
        annotation_ids = annotations_by_concept.get(str(res), [])
        rel_path = identifier_path(local_identifier(res))
        output_name = os.path.join(
            os.path.dirname(rel_path), f"{os.path.basename(rel_path)}.json"
        )

        if not s3_config:
            target_path = os.path.join(output_dir, output_name)
            if (
                not annotation_ids
                and os.path.exists(target_path)
                and os.path.getsize(target_path) > 0
            ):
                continue

        g = ds.cbd(res, include_reifications=False)

        if res in concepts:
            g = add_relations(
                g,
                ds,
                predicates=(SKOS.broader, SKOS.topConceptOf),
                uri=res,
            )
            g = add_inverse_relations(g, ds, uri=res, predicates=(SKOS.member,))

        if res in collections:
            g = add_relations(
                g,
                ds,
                predicates=(SKOS.member, SKOS.memberList),
                uri=res,
            )

        if res in schemes:
            g = add_inverse_relations(
                g,
                ds,
                uri=res,
                predicates=(SKOS.inScheme,),
            )

        g = add_referenced_data(g, ds)

        if res in concepts and annotation_ids:
            concept_label = add_annotation_collection_reference(g, str(res))
            output_annotation_collection(
                str(res),
                concept_label,
                annotation_ids,
                output_dir,
                gzipped,
                s3_client,
                s3_config,
                annotation_page_size,
            )

        if res in schemes:
            frame = scheme_frame_doc
        elif res in collections:
            frame = collection_frame_doc
        else:
            frame = frame_doc

        framed = frame_graph(g, frame, str(res))

        output_framed_json(
            framed,
            output_name,
            output_dir,
            gzipped,
            s3_client,
            s3_config,
        )

    schemes_graph = Graph()
    for scheme in schemes:
        schemes_graph += ds.cbd(scheme, include_reifications=False)

    schemes_graph = add_referenced_data(schemes_graph, ds)
    framed = frame_graph(schemes_graph, scheme_frame_doc, "")

    output_framed_json(
        framed,
        os.path.join("thesaurus", "schemes.json"),
        output_dir,
        gzipped,
        s3_client,
        s3_config,
    )

    collections_graph = Graph()
    for collection in collections:
        collections_graph += ds.cbd(collection, include_reifications=False)
        collections_graph = add_relations(
            collections_graph,
            ds,
            predicates=(SKOS.member, SKOS.memberList),
            uri=collection,
        )

    collections_graph = add_referenced_data(collections_graph, ds)
    framed = frame_graph(collections_graph, collection_frame_doc, "")

    output_framed_json(
        framed,
        os.path.join("thesaurus", "collections.json"),
        output_dir,
        gzipped,
        s3_client,
        s3_config,
    )

    generate_hydra_collection(
        ds,
        concepts,
        "concept",
        output_dir,
        gzipped=gzipped,
        s3_client=s3_client,
        s3_config=s3_config,
        page_size=page_size,
    )

    generate_hydra_collection(
        ds,
        schemes,
        "conceptscheme",
        output_dir,
        gzipped=gzipped,
        s3_client=s3_client,
        s3_config=s3_config,
        page_size=page_size,
    )

    generate_hydra_collection(
        ds,
        collections,
        "collection",
        output_dir,
        gzipped=gzipped,
        s3_client=s3_client,
        s3_config=s3_config,
        page_size=page_size,
    )

    generate_hydra_catalog(
        output_dir,
        gzipped=gzipped,
        s3_client=s3_client,
        s3_config=s3_config,
    )

    if s3_config:
        print(
            f"Uploaded thesaurus frames to s3://{s3_config.bucket}/{s3_config.prefix}"
        )
    else:
        print(f"Wrote thesaurus frames to '{output_dir}'.")

    return 0


def extract_title(
    graph: Graph | Dataset,
    res: Node,
    predicates: Iterable[URIRef] = (
        RDFS.label,
        SKOS.prefLabel,
        DCTERMS.title,
    ),
) -> str:
    """
    Extract title or label for a resource node from an RDF graph or dataset.

    Args:
        graph (Graph | Dataset): Source RDF graph or dataset.
        res (Node): Target resource URI node.
        predicates (Iterable[URIRef], optional): Predicates to check.

    Returns:
        str: Best available title or label, or fallback to local identifier.

    Examples:
        >>> from rdflib import Graph, URIRef, Literal, RDFS
        >>> g = Graph()
        >>> r = URIRef("https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/person:0001")
        >>> _ = g.add((r, RDFS.label, Literal("John Doe")))
        >>> extract_title(g, r)
        'John Doe'
    """

    for pred in predicates:
        for label in graph.objects(res, pred):
            val = str(label).strip()
            if val:
                return val

    return local_identifier(res)


def generate_hydra_collection(
    graph: Graph | Dataset,
    resources: Iterable[Node],
    category_key: str,
    output_dir: str,
    gzipped: bool = False,
    s3_client: Any | None = None,
    s3_config: S3UploadConfig | None = None,
    page_size: int = 1000,
    category_title: str | None = None,
    member_type: str | None = None,
    class_uri: str | None = None,
) -> None:
    """
    Generate Hydra Collection pages for a given category of resources.

    Args:
        graph (Graph | Dataset): RDF graph or dataset containing resource titles.
        resources (Iterable[Node]): Collection of resource nodes.
        category_key (str): Category identifier key (e.g. 'person').
        output_dir (str): Base output directory.
        gzipped (bool, optional): Whether output is gzipped. Defaults to False.
        s3_client (Any, optional): Boto3 S3 client. Defaults to None.
        s3_config (S3UploadConfig, optional): S3 configuration. Defaults to None.
        page_size (int, optional): Items per page. Defaults to 1000.
        category_title (str, optional): Title override for the collection. Defaults to None.
        member_type (str, optional): @type override for members. Defaults to None.
        class_uri (str, optional): RDF class URI for custom JSON-LD context mapping. Defaults to None.
    """
    meta = CATEGORY_HYDRA_METADATA.get(category_key, {})

    cat_title = (
        category_title or meta.get("title") or f"{category_key.capitalize()} Collection"
    )
    m_type = member_type or meta.get("type") or category_key.capitalize()
    c_uri = class_uri or meta.get("class_uri") or f"http://example.org/{m_type}"

    uri_prefix = meta.get("uri_prefix", f"{category_key}:")
    page_prefix = meta.get("page_prefix", "")

    page_context: list[Any] = [
        "http://www.w3.org/ns/hydra/context.jsonld",
        {
            m_type: c_uri,
        },
    ]

    resource_list = sorted(resources, key=str)

    page_size = max(1, page_size)
    total_items = len(resource_list)
    total_pages = max(1, (total_items + page_size - 1) // page_size)

    base_collection_uri = f"{URI_BASE}{uri_prefix}index"
    rel_folder = os.path.join(
        *[safe_segment(p) for p in uri_prefix.split(":") if p], "index"
    )

    for page_num in range(1, total_pages + 1):
        page_file = f"{page_prefix}page-{page_num}.json"
        page_uri = f"{base_collection_uri}/{page_file}"
        relative_path = os.path.join(rel_folder, page_file)

        start_index = (page_num - 1) * page_size
        end_index = page_num * page_size
        page_resources = resource_list[start_index:end_index]

        members = []
        for res in page_resources:
            members.append(
                {
                    "@id": str(res),
                    "@type": m_type,
                    "title": extract_title(graph, res),
                }
            )

        view: dict[str, Any] = {
            "@id": page_uri,
            "@type": "PartialCollectionView",
            "first": f"{base_collection_uri}/{page_prefix}page-1.json",
            "last": f"{base_collection_uri}/{page_prefix}page-{total_pages}.json",
        }

        if page_num > 1:
            view["previous"] = (
                f"{base_collection_uri}/{page_prefix}page-{page_num - 1}.json"
            )

        if page_num < total_pages:
            view["next"] = (
                f"{base_collection_uri}/{page_prefix}page-{page_num + 1}.json"
            )

        collection_doc = {
            "@context": page_context,
            "@id": page_uri,
            "@type": "Collection",
            "title": cat_title,
            "totalItems": total_items,
            "member": members,
            "view": view,
        }

        output_framed_json(
            collection_doc,
            relative_path,
            output_dir,
            gzipped,
            s3_client,
            s3_config,
        )


def generate_hydra_catalog(
    output_dir: str,
    gzipped: bool = False,
    s3_client: Any | None = None,
    s3_config: S3UploadConfig | None = None,
) -> None:
    """
    Generate top-level Globalise Dataset Catalog (catalog.json).

    Args:
        output_dir (str): Base output directory.
        gzipped (bool, optional): Whether output is gzipped. Defaults to False.
        s3_client (Any, optional): Boto3 S3 client. Defaults to None.
        s3_config (S3UploadConfig, optional): S3 configuration. Defaults to None.
    """
    catalog_uri = f"{URI_BASE}catalog.json"

    members = []
    for cat_key, meta in CATEGORY_HYDRA_METADATA.items():
        uri_prefix = meta.get("uri_prefix", f"{cat_key}:")
        page_prefix = meta.get("page_prefix", "")
        page1_uri = f"{URI_BASE}{uri_prefix}index/{page_prefix}page-1.json"

        members.append(
            {
                "@id": page1_uri,
                "title": meta["title"],
            }
        )

    catalog_doc = {
        "@context": "http://www.w3.org/ns/hydra/context.jsonld",
        "@id": catalog_uri,
        "@type": "Collection",
        "title": "Globalise Dataset Catalog",
        "member": members,
    }

    output_framed_json(
        catalog_doc,
        "catalog.json",
        output_dir,
        gzipped,
        s3_client,
        s3_config,
    )


def add_inverse_relations(
    g: Graph,
    ds: Dataset | Graph,
    uri: Node,
    predicates: Iterable[URIRef],
) -> Graph:
    """
    Add incoming triples for selected predicates and minimal context on their subjects.

    Args:
        g (Graph): Target RDF graph to update.
        ds (Dataset | Graph): Source RDF dataset or graph.
        uri (Node): Resource URI to find incoming statements for.
        predicates (Iterable[URIRef]): Predicates to inspect for incoming relations.

    Returns:
        Graph: Updated target RDF graph.

    Examples:
        >>> from rdflib import Graph, Literal, URIRef
        >>> source = Graph()
        >>> target = URIRef("https://example.org/target")
        >>> child = URIRef("https://example.org/child")
        >>> relation = URIRef("https://example.org/parentOf")
        >>> _ = source.add((child, relation, target))
        >>> _ = source.add((child, RDFS.label, Literal("Child")))
        >>> result = add_inverse_relations(Graph(), source, target, (relation,))
        >>> (child, relation, target) in result and (child, RDFS.label, Literal("Child")) in result
        True
    """
    for pred in predicates:
        for subj in ds.subjects(pred, uri):
            g.add((subj, pred, uri))

            for extra_pred in PREDICATES_FOR_SHALLOW_RESOURCE:
                for value in ds.objects(subj, extra_pred):
                    g.add((subj, extra_pred, value))

    return g


def add_relations(
    g: Graph,
    ds: Dataset | Graph,
    predicates: Iterable[URIRef] = (
        SKOS.broader,
        SKOS.topConceptOf,
    ),
    uri: Node | None = None,
) -> Graph:
    """
    Recursively add outgoing statements for selected predicates and shallow context on
    object nodes.

    Args:
        g (Graph): Target RDF graph to update.
        ds (Dataset | Graph): Source RDF dataset or graph.
        predicates (Iterable[URIRef], optional): Predicates to check.
        uri (Node, optional): Specific subject URI node.

    Returns:
        Graph: Updated target RDF graph.

    Examples:
        >>> from rdflib import Graph, Literal, URIRef
        >>> source = Graph()
        >>> parent = URIRef("https://example.org/parent")
        >>> child = URIRef("https://example.org/child")
        >>> relation = URIRef("https://example.org/contains")
        >>> _ = source.add((parent, relation, child))
        >>> _ = source.add((child, RDFS.label, Literal("Child")))
        >>> result = add_relations(Graph(), source, (relation,), parent)
        >>> (parent, relation, child) in result and (child, RDFS.label, Literal("Child")) in result
        True
    """
    targets = [uri] if uri else list(g.subjects())

    visited: set[Node] = set()
    predicates = tuple(predicates)

    while targets:
        target = targets.pop()
        if target in visited:
            continue
        visited.add(target)

        for pred in predicates:
            for obj in ds.objects(target, pred):
                g.add((target, pred, obj))
                targets.append(obj)  # Add object to targets for further traversal

                for extra_pred in PREDICATES_FOR_SHALLOW_RESOURCE:
                    for value in ds.objects(obj, extra_pred):
                        g.add((obj, extra_pred, value))

    return g


def add_referenced_data(g: Graph, ds: Dataset | Graph) -> Graph:
    """
    Add shallow label and type statements for all blank nodes and URI objects referenced in graph.

    Args:
        g (Graph): Target RDF graph to expand.
        ds (Dataset | Graph): Source dataset or graph to fetch labels/types from.

    Returns:
        Graph: Expanded RDF graph.

    Examples:
        >>> from rdflib import Graph, Literal, URIRef
        >>> source = Graph()
        >>> subject = URIRef("https://example.org/subject")
        >>> referenced = URIRef("https://example.org/referenced")
        >>> predicate = URIRef("https://example.org/references")
        >>> _ = source.add((subject, predicate, referenced))
        >>> _ = source.add((referenced, RDFS.label, Literal("Referenced")))
        >>> result = add_referenced_data(Graph() + source, source)
        >>> (referenced, RDFS.label, Literal("Referenced")) in result
        True
    """
    referenced_nodes = set(g.objects()) - set(g.subjects())

    for node in referenced_nodes:
        if isinstance(node, (URIRef, Node)):
            for pred in PREDICATES_FOR_SHALLOW_RESOURCE:
                for val in ds.objects(node, pred):
                    g.add((node, pred, val))

    return g


def add_labels(
    graph: Graph | Dataset,
    target_classes: tuple[URIRef, ...],
    fallback_predicates: tuple[URIRef, ...],
    label_predicate: URIRef = RDFS.label,
    default_language: str | None = None,
) -> Graph | Dataset:
    """
    Ensure resources of specified target classes have a designated label predicate.

    Args:
        graph (Graph | Dataset): RDF graph or dataset to update in place.
        target_classes (tuple[URIRef, ...]): Target RDF classes.
        fallback_predicates (tuple[URIRef, ...]): Alternative predicates to inspect for labels.
        label_predicate (URIRef, optional): Target label predicate. Defaults to RDFS.label.
        default_language (str, optional): Preferred language tag. Defaults to None.

    Returns:
        Graph | Dataset: Updated RDF graph or dataset.

    Examples:
        >>> from rdflib import Graph, Literal, URIRef
        >>> graph = Graph()
        >>> resource = URIRef("https://example.org/resource")
        >>> resource_class = URIRef("https://example.org/Resource")
        >>> alternate_label = URIRef("https://example.org/title")
        >>> _ = graph.add((resource, RDF.type, resource_class))
        >>> _ = graph.add((resource, alternate_label, Literal("Resource title")))
        >>> result = add_labels(graph, (resource_class,), (alternate_label,))
        >>> list(result.objects(resource, RDFS.label))
        [rdflib.term.Literal('Resource title')]
        >>> g2 = Graph()
        >>> r2 = URIRef("https://example.org/resource2")
        >>> _ = g2.add((r2, RDF.type, resource_class))
        >>> _ = g2.add((r2, alternate_label, Literal("English", lang="en")))
        >>> _ = g2.add((r2, alternate_label, Literal("Nederlands", lang="nl")))
        >>> res2 = add_labels(g2, (resource_class,), (alternate_label,), default_language="nl")
        >>> list(res2.objects(r2, RDFS.label))
        [rdflib.term.Literal('Nederlands')]
    """
    for target_class in target_classes:
        for subject in graph.subjects(RDF.type, target_class):
            if not list(graph.objects(subject, label_predicate)):
                candidates: list[Node] = []
                for fallback in fallback_predicates:
                    candidates.extend(graph.objects(subject, fallback))

                if not candidates:
                    continue

                chosen: Node | None = None
                if default_language:
                    matching = [
                        c
                        for c in candidates
                        if isinstance(c, Literal) and c.language == default_language
                    ]
                    if matching:
                        chosen = matching[0]

                if chosen is None:
                    chosen = candidates[0]

                label: Node
                if label_predicate == RDFS.label:
                    label = Literal(str(chosen))
                else:
                    label = chosen

                graph.add((subject, label_predicate, label))

    return graph


def main() -> int:
    """
    Execute main entry point for JSON export CLI.

    Returns:
        int: Exit status code (0 for success, 1 for error).
    """
    args = parse_args()
    s3_config = build_s3_upload_config(args)
    s3_client = create_s3_client(s3_config) if s3_config else None

    if args.mode == "entity":
        if not all(
            [args.input_ttl, args.output_dir, args.frame_jsonld, args.root_type_uri]
        ):
            print(
                "Error: entity mode requires input_ttl, output_dir, frame_jsonld, and root_type_uri arguments."
            )
            return 1

        return convert_entity(
            args,
            gzipped=args.gzipped,
            s3_client=s3_client,
            s3_config=s3_config,
        )

    elif args.mode == "thesaurus":
        input_path = os.path.abspath(
            os.path.expanduser(
                args.input_ttl
                or os.path.join(
                    BASE_DIR, "data", "output", "concept", "ttl", "thesaurus.ttl"
                )
            )
        )
        output_dir = os.path.abspath(
            os.path.expanduser(
                args.output_dir or os.path.join(BASE_DIR, "data", "output", "s3")
            )
        )
        frame_path = os.path.join(
            BASE_DIR,
            "data",
            "frames",
            "concept",
            "concept.jsonld",
        )
        scheme_frame_path = os.path.join(
            BASE_DIR,
            "data",
            "frames",
            "concept",
            "conceptscheme.jsonld",
        )
        collection_frame_path = os.path.join(
            BASE_DIR,
            "data",
            "frames",
            "concept",
            "collection.jsonld",
        )

        return convert_thesaurus(
            input_path,
            output_dir,
            frame_path,
            scheme_frame_path,
            collection_frame_path,
            gzipped=args.gzipped,
            s3_client=s3_client,
            s3_config=s3_config,
            page_size=args.page_size,
            links_parquet=args.links_parquet,
            skip_annotations=args.skip_annotations,
            annotation_page_size=args.annotation_page_size,
        )

    elif args.mode == "catalog":
        output_dir = os.path.abspath(
            os.path.expanduser(
                args.output_dir or os.path.join(BASE_DIR, "data", "output", "s3")
            )
        )
        generate_hydra_catalog(
            output_dir,
            gzipped=args.gzipped,
            s3_client=s3_client,
            s3_config=s3_config,
        )
        return 0

    return 1


if __name__ == "__main__":
    import doctest

    doctest.testmod()
    raise SystemExit(main())
