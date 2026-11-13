#!/usr/bin/env python3
"""
Merge individual turtle files into one knowledge graph for any category.
"""

import argparse
import glob
import logging
import os
import sys

from rdflib import RDF, RDFS, SKOS, Graph, Literal, Namespace, XSD, URIRef

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import (
    convert_uris_to_bnodes,
    expand_date_literal,
    normalize_uris,
    replace_concept_uris,
)

import re
from concurrent.futures import ProcessPoolExecutor

CRM = Namespace("http://www.cidoc-crm.org/cidoc-crm/")
AAT = Namespace("http://vocab.getty.edu/aat/")
AAAO = Namespace("https://ontology.swissartresearch.net/aaao/")

TIMESTAMP_SUFFIXES = {
    CRM["P82a_begin_of_the_begin"]: "T00:00:00",
    CRM["P81b_begin_of_the_end"]: "T00:00:00",
    CRM["P81a_end_of_the_begin"]: "T23:59:59",
    CRM["P82b_end_of_the_end"]: "T23:59:59",
}

logging.getLogger("rdflib.term").disabled = True


def get_default_paths(category: str) -> tuple[str, str]:
    """
    Get default input directory and output filepath for an entity category.

    Args:
        category (str): The entity category (e.g. "person", "place", "organization").

    Returns:
        tuple[str, str]: Tuple containing (input_dir, output_path).

    Examples:
        >>> input_dir, output_path = get_default_paths("person")
        >>> input_dir.endswith("data/output/person/rdf")
        True
        >>> output_path.endswith("data/output/person/person.ttl")
        True
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    input_dir = os.path.join(base_dir, "data", "output", category, "rdf")
    output_path = os.path.join(base_dir, "data", "output", category, f"{category}.ttl")

    return input_dir, output_path


def normalize_dates_to_datetimes(graph: Graph) -> None:
    """
    Normalize XSD.date and XSD.dateTime literals in an RDF graph.

    Predicates in TIMESTAMP_SUFFIXES get specific timestamp suffixes (e.g. P81a_end_of_the_begin -> T23:59:59,
    P82b_end_of_the_end -> T23:59:59), and all other XSD.date literals are expanded to XSD.dateTime with T00:00:00.

    Args:
        graph (Graph): The RDF graph to process and normalize in place.

    Examples:
        >>> from rdflib import Graph, Literal, URIRef, XSD, Namespace
        >>> CRM_NS = Namespace("http://www.cidoc-crm.org/cidoc-crm/")
        >>> g = Graph()
        >>> _ = g.add((URIRef("http://example.org/event"), CRM_NS["P82a_begin_of_the_begin"], Literal("1704-02-27", datatype=XSD.date)))
        >>> _ = g.add((URIRef("http://example.org/event"), CRM_NS["P81a_end_of_the_begin"], Literal("1704-02-27", datatype=XSD.date)))
        >>> normalize_dates_to_datetimes(g)
        >>> sorted(str(o) for o in g.objects(None, CRM_NS["P82a_begin_of_the_begin"]))
        ['1704-02-27T00:00:00']
        >>> sorted(str(o) for o in g.objects(None, CRM_NS["P81a_end_of_the_begin"]))
        ['1704-02-27T23:59:59']
    """
    # 1. Clean up floats ending in .0
    for s, p, o in list(graph.triples((None, None, None))):
        if isinstance(o, Literal) and o.datatype in (XSD.date, XSD.dateTime):
            if str(o).endswith(".0"):
                graph.remove((s, p, o))
                normalized_date = str(o)[:-2]
                graph.add((s, p, Literal(normalized_date, datatype=o.datatype)))

    # 2. Process specific CRM timestamp predicates FIRST
    for predicate, suffix in TIMESTAMP_SUFFIXES.items():
        for subject, obj in list(graph.subject_objects(predicate)):
            if not isinstance(obj, Literal):
                continue

            raw_date = str(obj).split("T")[0]
            try:
                normalized_date = expand_date_literal(raw_date, suffix)
            except ValueError as e:
                print(e)
                continue

            new_literal = Literal(
                f"{normalized_date}{suffix}", datatype=XSD.dateTime, normalize=False
            )
            graph.remove((subject, predicate, obj))
            graph.add((subject, predicate, new_literal))

    # 3. Process remaining XSD.date literals
    for s, p, o in list(graph.triples((None, None, None))):
        if isinstance(o, Literal) and o.datatype == XSD.date:
            raw_date = str(o).split("T")[0]
            try:
                normalized_date = expand_date_literal(raw_date, "T00:00:00")
            except ValueError as e:
                print(e)
                continue

            new_literal = Literal(
                f"{normalized_date}T00:00:00", datatype=XSD.dateTime, normalize=False
            )
            graph.remove((s, p, o))
            graph.add((s, p, new_literal))


def _replace_uri(graph: Graph, old: URIRef, new: URIRef) -> None:
    triples = set(graph.triples((old, None, None))) | set(
        graph.triples((None, None, old))
    )
    for subject, predicate, obj in triples:
        graph.remove((subject, predicate, obj))
        graph.add(
            (
                new if subject == old else subject,
                predicate,
                new if obj == old else obj,
            )
        )


def other_replacements(graph: Graph, category: str) -> None:
    """
    Convert ship- and polity-related places in person graphs to their entity types.

    Args:
        graph (Graph): The RDF graph to modify in place.
        category (str): Entity category; only person graphs are changed.

    Examples:
        >>> from rdflib import BNode
        >>> base = "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/"
        >>> markers = ("DAS_ship", "gzm_ship", "monsoon_traders")
        >>> places = [URIRef(base + "place:" + marker) for marker in markers]
        >>> ships = [URIRef(base + "ship:" + marker) for marker in markers]
        >>> ordinary = URIRef(base + "place:ordinary")
        >>> untyped = URIRef(base + "place:DAS_ship_untyped")
        >>> no_place_prefix = URIRef(base + "ship:DAS_ship_existing")
        >>> blank = BNode("place")
        >>> g = Graph()
        >>> for place in [*places, ordinary, no_place_prefix, blank]:
        ...     _ = g.add((place, RDF.type, CRM["E53_Place"]))
        >>> _ = g.add((places[0], RDFS.label, Literal("DAS_ship")))
        >>> _ = g.add((ordinary, RDFS.seeAlso, places[0]))
        >>> _ = g.add((places[0], RDFS.seeAlso, places[0]))
        >>> _ = g.add((places[1], RDFS.seeAlso, places[2]))
        >>> _ = g.add((untyped, RDFS.label, Literal("untyped")))
        >>> original = set(g)
        >>> other_replacements(g, "place")
        >>> set(g) == original
        True
        >>> other_replacements(g, "person")
        >>> expected = {
        ...     (ordinary, RDF.type, CRM["E53_Place"]),
        ...     (no_place_prefix, RDF.type, CRM["E53_Place"]),
        ...     (blank, RDF.type, CRM["E53_Place"]),
        ...     (ships[0], RDFS.label, Literal("DAS_ship")),
        ...     (ordinary, RDFS.seeAlso, ships[0]),
        ...     (ships[0], RDFS.seeAlso, ships[0]),
        ...     (ships[1], RDFS.seeAlso, ships[2]),
        ...     (untyped, RDFS.label, Literal("untyped")),
        ...     (AAT["300082981"], RDF.type, SKOS.Concept),
        ...     (AAT["300082981"], RDFS.label, Literal("ship")),
        ... }
        >>> for ship in ships:
        ...     expected.add((ship, RDF.type, CRM["E22_Human-Made_Object"]))
        ...     expected.add((ship, CRM["P2_has_type"], AAT["300082981"]))
        >>> set(g) == expected
        True
        >>> other_replacements(g, "person")
        >>> set(g) == expected
        True
    """

    if category != "person":
        return

    ship_markers = ("DAS_ship", "gzm_ship", "monsoon_traders")
    for place in list(graph.subjects(RDF.type, CRM["E53_Place"])):
        if not isinstance(place, URIRef):
            continue

        uri = str(place)
        if "place:" not in uri or not any(marker in uri for marker in ship_markers):
            continue

        ship = URIRef(uri.replace("place:", "ship:"))
        _replace_uri(graph, place, ship)
        graph.remove((ship, RDF.type, CRM["E53_Place"]))
        graph.add((ship, RDF.type, CRM["E22_Human-Made_Object"]))
        graph.add((ship, CRM["P2_has_type"], AAT["300082981"]))
        graph.add((AAT["300082981"], RDF.type, SKOS.Concept))
        graph.add((AAT["300082981"], RDFS.label, Literal("ship")))

    for place in list(graph.subjects(RDF.type, CRM["E53_Place"])):
        if not isinstance(place, URIRef):
            continue

        uri = str(place)
        if "place:" not in uri or "polity" not in uri.lower():
            continue

        polity = URIRef(uri.replace("place:", "polity:"))
        _replace_uri(graph, place, polity)
        graph.remove((polity, RDF.type, CRM["E53_Place"]))
        graph.add((polity, RDF.type, AAAO["ZE39_Polity"]))

        for subject, predicate, obj in list(
            graph.triples((None, AAAO["ZP114_has_geographic_scope"], polity))
        ):
            graph.remove((subject, predicate, obj))
            graph.add(
                (
                    subject,
                    AAAO["ZP70_ascribes_social_status_in_relation_to"],
                    polity,
                )
            )


def load_graph(rdf_dir: str, category: str) -> Graph:
    """
    Load all Turtle files in a directory into a single normalized RDF graph.

    Deprecated concept URIs are replaced with new URIs, URIs in the SARI/GLOBALISE
    namespace are transformed from slash-separated to colon-hash format, and dates
    are normalized to dateTime literals.

    Args:
        rdf_dir (str): Directory containing .ttl files.

    Returns:
        Graph: Merged, URI-transformed, and date-normalized RDF graph.

    Raises:
        FileNotFoundError: If no .ttl files are found in the directory.
    """
    ttl_files = sorted(glob.glob(os.path.join(rdf_dir, "**", "*.ttl"), recursive=True))
    if not ttl_files:
        raise FileNotFoundError(f"No .ttl files found in {rdf_dir}")

    graph = Graph()
    for ttl_file in ttl_files:
        graph.parse(ttl_file, format="turtle")

    replace_concept_uris(graph)
    transformed_count = normalize_uris(graph)
    bnode_count = convert_uris_to_bnodes(graph)
    if transformed_count > 0 or bnode_count > 0:
        logging.info(
            f"Transformed {transformed_count} URIs and converted {bnode_count} hash URIs to BNodes."
        )

    normalize_dates_to_datetimes(graph)

    other_replacements(graph, category)

    return graph


def combine_single_chunk(
    chunk_files: list[str], output_path: str, category: str
) -> str:
    """
    Combine a list of chunk Turtle files into a single normalized Turtle file.

    Args:
        chunk_files (list[str]): List of Turtle file paths for a single chunk.
        output_path (str): Target output file path.

    Returns:
        str: Output file path upon completion.
    """
    graph = Graph()
    for ttl_file in chunk_files:
        graph.parse(ttl_file, format="turtle")

    replace_concept_uris(graph)
    normalize_uris(graph)
    convert_uris_to_bnodes(graph)
    normalize_dates_to_datetimes(graph)

    other_replacements(graph, category)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    graph.serialize(destination=output_path, format="turtle")
    return output_path


def combine_chunk_ttls(
    category: str,
    input_dir: str,
    output_dir: str,
    nprocs: int = 6,
) -> int:
    """
    Combine Turtle files grouped by chunk ID suffix (_part0001, _part0002, etc.) in parallel.

    Args:
        category (str): Entity category name (e.g. "person").
        input_dir (str): Input directory containing chunked .ttl files.
        output_dir (str): Output directory for combined chunk .ttl files.
        nprocs (int): Maximum parallel workers.

    Returns:
        int: Exit status code (0 for success).
    """
    all_ttl_files = sorted(
        glob.glob(os.path.join(input_dir, "**", "*.ttl"), recursive=True)
    )

    chunk_groups: dict[str, list[str]] = {}
    for f in all_ttl_files:
        match = re.search(r"_part(\d+)\.ttl$", f)
        if match:
            part = match.group(1)
            chunk_groups.setdefault(part, []).append(f)

    if not chunk_groups:
        raise FileNotFoundError(f"No chunked .ttl files found in {input_dir}")

    os.makedirs(output_dir, exist_ok=True)
    tasks = []
    for part in sorted(chunk_groups.keys()):
        files = chunk_groups[part]
        out_path = os.path.join(output_dir, f"{category}_part{part}.ttl")
        tasks.append((files, out_path))

    print(f"Combining {len(tasks)} TTL chunk sets for category '{category}'...")

    with ProcessPoolExecutor(max_workers=nprocs) as executor:
        futures = [
            executor.submit(combine_single_chunk, files, out_path, category)
            for files, out_path in tasks
        ]
        for future in futures:
            future.result()

    print(f"Successfully combined {len(tasks)} chunk Turtle files in '{output_dir}'.")
    return 0


def parse_args(args_list: list[str] | None = None) -> argparse.Namespace:
    """
    Parse CLI arguments for Turtle graph conversion.

    Args:
        args_list (list[str], optional): CLI arguments list for testing. Defaults to None.

    Returns:
        argparse.Namespace: Parsed command-line arguments.

    Examples:
        >>> parse_args(["person"]).category
        'person'
    """
    parser = argparse.ArgumentParser(
        description="Merge RDF files into Turtle knowledge graphs for any category, normalizing URIs and dates."
    )
    parser.add_argument(
        "category",
        help="The entity category (e.g., person, place, organization, ship, polity, measurement)",
    )
    parser.add_argument("input_dir", nargs="?")
    parser.add_argument("output_path", nargs="?")
    parser.add_argument(
        "--by-chunk",
        action="store_true",
        help="Combine RDF files grouped by chunk suffix (_partXXXX) into separate TTL files.",
    )
    parser.add_argument(
        "--nprocs",
        type=int,
        default=int(os.getenv("NPROCS", "6")),
        help="Number of parallel processes for chunk combining (default: 6).",
    )
    return parser.parse_args(args_list)


def main() -> int:
    """
    Execute main entry point for Turtle graph generation.

    Returns:
        int: Exit status code (0 for success, 1 for error).
    """
    args = parse_args()
    category = args.category

    default_input, default_output = get_default_paths(category)
    input_dir = os.path.abspath(os.path.expanduser(args.input_dir or default_input))

    if args.by_chunk or (
        category == "person"
        and (args.output_path is None or not args.output_path.endswith(".ttl"))
    ):
        output_dir = os.path.abspath(
            os.path.expanduser(
                args.output_path
                or os.path.join(os.path.dirname(os.path.dirname(input_dir)), "ttl")
            )
        )
        try:
            return combine_chunk_ttls(category, input_dir, output_dir, args.nprocs)
        except FileNotFoundError as e:
            print(f"Error: {e}")
            return 1

    output_path = os.path.abspath(
        os.path.expanduser(args.output_path or default_output)
    )

    if not os.path.isdir(input_dir):
        print(f"Error: directory '{input_dir}' not found.")
        return 1

    try:
        graph = load_graph(input_dir, category)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return 1

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    graph.serialize(destination=output_path, format="turtle")

    rdf_count = len(glob.glob(os.path.join(input_dir, "**", "*.ttl"), recursive=True))
    print(f"Loaded and normalized {rdf_count} Turtle files into one graph.")
    print(f"Turtle written to: {output_path}")

    return 0


if __name__ == "__main__":
    import doctest

    doctest.testmod()
    raise SystemExit(main())
