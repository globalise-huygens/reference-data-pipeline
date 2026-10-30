#!/usr/bin/env python3
"""Frame a small person sample without writing to production output."""

import argparse
import json
from pathlib import Path

from rdflib import URIRef
from rdflib.namespace import RDF
from tqdm import tqdm

from convert_to_json import (
    BASE_DIR,
    EXCLUDED_PREDICATES,
    PROJECT_URI_TOKEN,
    add_referenced_data,
    frame_graph,
    parse_rdf_graph,
)
from utils import local_identifier_path, output_framed_json


PERSON_TYPE = URIRef("http://www.cidoc-crm.org/cidoc-crm/E21_Person")
SAMPLE_SIZE = 100
ROOT = Path(BASE_DIR)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / "data/output/person/ttl/person_part0001.ttl",
        help="First combined person TTL chunk",
    )
    parser.add_argument(
        "--frame",
        type=Path,
        default=ROOT / "data/frames/person/person.jsonld",
        help="Person JSON-LD frame",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data/output/person/frame-test",
        help="Separate directory for sample output",
    )
    args = parser.parse_args()

    print(f"Parsing {args.input} (this can take a while)...", flush=True)
    graph = parse_rdf_graph(str(args.input))
    for predicate in EXCLUDED_PREDICATES:
        graph.remove((None, predicate, None))

    resources = sorted(
        {
            resource
            for resource in graph.subjects(RDF.type, PERSON_TYPE)
            if isinstance(resource, URIRef) and PROJECT_URI_TOKEN in str(resource)
        },
        key=str,
    )[:SAMPLE_SIZE]
    if not resources:
        parser.error(f"No project persons found in {args.input}")

    with args.frame.open(encoding="utf-8") as infile:
        frame_doc = json.load(infile)

    print(
        f"Framing {len(resources)} persons into {args.output}; "
        "annotations and collection indexes are not generated.",
        flush=True,
    )
    for resource in tqdm(resources, desc="Framing persons", unit="person"):
        entity_graph = graph.cbd(resource, include_reifications=False)
        entity_graph = add_referenced_data(entity_graph, graph)
        framed = frame_graph(entity_graph, frame_doc, str(resource))
        output_name = f"{local_identifier_path(resource)}.json"
        output_framed_json(framed, output_name, str(args.output), True, None, None)

    print(f"Framed {len(resources)} persons into {args.output}.")


if __name__ == "__main__":
    main()
