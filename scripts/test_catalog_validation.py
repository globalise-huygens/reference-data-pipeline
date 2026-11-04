"""Unit tests for catalog generation, URI validation, and empty JSON-LD document filtering."""

import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rdflib import Graph, Literal, RDF, RDFS, URIRef

from utils import (
    is_empty_jsonld,
    is_valid_entity_uri,
    output_framed_json,
    generate_hydra_index_pages,
)
from convert_to_json import (
    generate_hydra_collection,
    generate_hydra_collection_from_persons_csv,
    generate_hydra_catalog,
)


class CatalogValidationTests(unittest.TestCase):
    def test_is_valid_entity_uri(self):
        # Valid URIs
        self.assertTrue(
            is_valid_entity_uri(
                "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/person:123"
            )
        )
        self.assertTrue(
            is_valid_entity_uri(
                "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/place:GLOB2_1001"
            )
        )
        self.assertTrue(
            is_valid_entity_uri(
                "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/catalog.json"
            )
        )
        self.assertTrue(
            is_valid_entity_uri(
                "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/inventory:collection"
            )
        )

        # Invalid / empty URIs
        self.assertFalse(
            is_valid_entity_uri(
                "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/person:"
            )
        )
        self.assertFalse(
            is_valid_entity_uri(
                "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/place:"
            )
        )
        self.assertFalse(
            is_valid_entity_uri(
                "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/group:"
            )
        )
        self.assertFalse(
            is_valid_entity_uri(
                "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/"
            )
        )
        self.assertFalse(is_valid_entity_uri(""))
        self.assertFalse(is_valid_entity_uri(None))

    def test_is_empty_jsonld(self):
        self.assertTrue(is_empty_jsonld(None))
        self.assertTrue(is_empty_jsonld({}))
        self.assertTrue(is_empty_jsonld([]))
        self.assertTrue(
            is_empty_jsonld({"@context": "https://example.org/context.json"})
        )
        # Empty entity with trailing colon in @id
        self.assertTrue(
            is_empty_jsonld(
                {
                    "@context": "https://example.org/context.json",
                    "id": "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/person:",
                    "type": "Person",
                }
            )
        )
        self.assertTrue(
            is_empty_jsonld(
                {
                    "@context": "https://example.org/context.json",
                    "@id": "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/place:",
                    "@type": "Place",
                }
            )
        )

        # Valid entity document
        self.assertFalse(
            is_empty_jsonld(
                {
                    "@context": "https://example.org/context.json",
                    "id": "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/person:123",
                    "type": "Person",
                    "label": "John Doe",
                }
            )
        )

    def test_output_framed_json_skips_empty_doc(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target_file = os.path.join(tmpdir, "person.json")
            empty_doc = {
                "@context": "https://example.org/context.json",
                "id": "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/person:",
                "type": "Person",
            }
            output_framed_json(empty_doc, "person.json", tmpdir, False, None, None)
            self.assertFalse(os.path.exists(target_file))

            valid_doc = {
                "@context": "https://example.org/context.json",
                "id": "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/person:123",
                "type": "Person",
                "label": "John Doe",
            }
            output_framed_json(valid_doc, "person/123.json", tmpdir, False, None, None)
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "person/123.json")))

    def test_generate_hydra_collection_from_persons_csv_skips_empty_and_duplicates(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "persons.csv")
            with open(csv_path, "w", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["id", "URI", "comment", "rdfs:label"])
                writer.writerow(["1", "Person_1", "", "Anna Bijns"])
                writer.writerow(["2", "", "", "Unknown"])  # Empty URI
                writer.writerow(["3", "Person_2", "", "Dirk van Cloon"])
                writer.writerow(["4", "Person_1", "", "Anna Bijns (Duplicate)"])  # Duplicate
                writer.writerow(["5", "   ", "", "Unknown"])  # Whitespace URI

            generate_hydra_collection_from_persons_csv(csv_path, tmpdir, page_size=10)

            page1_path = os.path.join(tmpdir, "person", "index", "page-1.json")
            self.assertTrue(os.path.exists(page1_path))

            with open(page1_path, "r", encoding="utf-8") as f:
                page_doc = json.load(f)

            members = page_doc["member"]
            # Only Person_1 and Person_2 should be present
            self.assertEqual(len(members), 2)
            self.assertEqual(page_doc["totalItems"], 2)
            member_ids = [m["@id"] for m in members]
            self.assertIn(
                "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/person:Person_1",
                member_ids,
            )
            self.assertIn(
                "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/person:Person_2",
                member_ids,
            )
            self.assertNotIn(
                "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/person:",
                member_ids,
            )

    def test_generate_hydra_collection_skips_empty_and_duplicates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            g = Graph()
            r1 = URIRef(
                "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/place:GLOB2_1001"
            )
            r2 = URIRef(
                "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/place:"
            )  # Empty URI
            r3 = URIRef(
                "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/place:GLOB2_1002"
            )
            g.add((r1, RDFS.label, Literal("Batavia")))
            g.add((r3, RDFS.label, Literal("Bantam")))

            resources = [r1, r2, r3, r1]  # Contains empty r2 and duplicate r1
            generate_hydra_collection(g, resources, "place", tmpdir, page_size=10)

            page1_path = os.path.join(tmpdir, "place", "index", "page-1.json")
            self.assertTrue(os.path.exists(page1_path))

            with open(page1_path, "r", encoding="utf-8") as f:
                page_doc = json.load(f)

            members = page_doc["member"]
            self.assertEqual(len(members), 2)
            self.assertEqual(page_doc["totalItems"], 2)
            member_ids = [m["@id"] for m in members]
            self.assertNotIn(
                "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/place:",
                member_ids,
            )

    def test_generate_hydra_catalog_no_duplicates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            generate_hydra_catalog(tmpdir)
            catalog_path = os.path.join(tmpdir, "catalog.json")
            self.assertTrue(os.path.exists(catalog_path))

            with open(catalog_path, "r", encoding="utf-8") as f:
                cat_doc = json.load(f)

            member_ids = [m["@id"] for m in cat_doc["member"]]
            self.assertEqual(len(member_ids), len(set(member_ids)))


if __name__ == "__main__":
    unittest.main()
