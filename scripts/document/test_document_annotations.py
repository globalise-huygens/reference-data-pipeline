import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import duckdb

from document_annotations import (
    BASE_URI,
    NER_CATEGORIES,
    export_document_annotations,
)
from export_documents import output_framed_json


class DocumentAnnotationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.parquet = Path(self.directory.name) / "inventory.parquet"
        self.scan1 = "NL-HaNA_1.04.02_1053_0001"
        self.scan2 = "NL-HaNA_1.04.02_1053_0002"
        self.scan3 = "NL-HaNA_1.04.02_1053_0003"
        self.id1 = f"{BASE_URI}/annotations:entities:{self.scan1}#annotation:100"
        self.id2 = f"{BASE_URI}/annotations:entities:{self.scan1}#annotation:2"
        self.id3 = f"{BASE_URI}/annotations:entities:{self.scan2}#annotation:3"
        self.event = f"{BASE_URI}/annotations:events:{self.scan2}#annotation:4"
        rows = [
            (self.id1, "Name of Person", 30),
            (self.id2, "Name of Person", 5),
            (self.id2, "Name of Person", 5),
            (self.id3, "Name of Person", 1),
            (self.event, "EVENT", 2),
            (f"{BASE_URI}/annotations:entities:{self.scan3}#annotation:5", "Name of Location", 1),
            (f"{BASE_URI}/annotations:entities:{self.scan1}#annotation:6", "bad label", 1),
        ]
        with duckdb.connect() as conn:
            conn.execute("CREATE TABLE annotations (annotation_id VARCHAR, classified_as VARCHAR, offset_scan BIGINT)")
            conn.executemany("INSERT INTO annotations VALUES (?, ?, ?)", rows)
            conn.execute("COPY annotations TO ? (FORMAT PARQUET)", [str(self.parquet)])

    def document(self, identifier, scans):
        links = [
            SimpleNamespace(index=index, page=SimpleNamespace(scan=SimpleNamespace(filename=filename)))
            for index, filename in enumerate(scans)
        ]
        return SimpleNamespace(
            id=identifier,
            inventory=SimpleNamespace(inventory_number="1053"),
            pages=links,
            sub_documents=[],
        )

    def test_collections_pagination_order_deduplication_and_events(self):
        doc = self.document("root", [self.scan1, self.scan2, self.scan1])
        other = self.document("other", [self.scan2])
        empty = self.document("empty", [])
        emitted = {}

        with self.assertLogs("document_annotations", level="WARNING") as logs:
            export_document_annotations(
                [doc, other, empty], lambda data, path: emitted.update({path: data}),
                str(self.parquet), page_size=2,
            )
        self.assertIn("bad label", logs.output[0])
        prefix = "document/NL-HaNA_1.04.02_1053_0001-0002.annotations"
        collection = emitted[f"{prefix}.per_name.json"]
        first = emitted[f"{prefix}.per_name/page-1.json"]
        last = emitted[f"{prefix}.per_name/page-2.json"]
        self.assertEqual(collection["total"], 3)
        self.assertEqual(collection["first"], first["id"])
        self.assertEqual(collection["last"], last["id"])
        self.assertEqual(first["next"], last["id"])
        self.assertEqual(last["previous"], first["id"])
        self.assertEqual(first["partOf"], collection["id"])
        self.assertEqual([item["id"] for item in first["items"]], [self.id2, self.id1])
        self.assertEqual([item["id"] for item in last["items"]], [self.id3])
        self.assertEqual(first["items"][0]["partOf"]["id"], f"{BASE_URI}/canvas:{self.scan1}")
        self.assertEqual(first["items"][0]["partOf"]["partOf"]["id"], f"{BASE_URI}/inventory:1053.manifest")
        annotation_set = emitted[f"{prefix}.json"]
        self.assertEqual(len(annotation_set["member"]), 16)
        self.assertEqual(annotation_set["member"][0]["id"], collection["id"])
        self.assertEqual(collection["type"], ["DigitalObject", "BasicContainer", "AnnotationCollection"])
        self.assertEqual(first["type"], ["DigitalObject", "AnnotationPage"])
        self.assertEqual(emitted[f"{prefix}.events.json"]["total"], 1)
        self.assertEqual(emitted[f"{prefix}.events/page-1.json"]["items"][0]["id"], self.event)
        self.assertEqual(emitted[f"{prefix}.cmty_quant.json"]["total"], 0)
        self.assertEqual(emitted[f"{prefix}.cmty_quant/page-1.json"]["items"], [])
        self.assertEqual(emitted["document/empty.annotations.events.json"]["total"], 0)
        self.assertEqual(emitted["document/NL-HaNA_1.04.02_1053_0002-0002.annotations.per_name.json"]["total"], 1)
        self.assertEqual(len(NER_CATEGORIES), 15)

    def test_output_files_and_input_errors(self):
        doc = self.document("root", [self.scan2])
        output = Path(self.directory.name) / "output"
        emit = lambda data, path: output_framed_json(data, path, str(output), False, None, None)
        export_document_annotations([doc], emit, str(self.parquet), page_size=1)
        collection = output / "document/NL-HaNA_1.04.02_1053_0002-0002.annotations.events.json"
        page = output / "document/NL-HaNA_1.04.02_1053_0002-0002.annotations.events/page-1.json"
        self.assertEqual(json.loads(collection.read_text())["total"], 1)
        self.assertEqual(json.loads(page.read_text())["items"][0]["id"], self.event)
        with self.assertRaises(FileNotFoundError):
            export_document_annotations([doc], emit, str(self.parquet) + ".missing")
        with self.assertRaises(ValueError):
            export_document_annotations([doc], emit, str(self.parquet), page_size=0)

    def test_every_inventory_category_maps_to_its_collection(self):
        rows = [
            (
                f"{BASE_URI}/annotations:entities:{self.scan1}#annotation:{index}",
                label,
                index,
            )
            for index, (_, label) in enumerate(NER_CATEGORIES)
        ]
        with duckdb.connect() as conn:
            conn.execute("CREATE TABLE annotations (annotation_id VARCHAR, classified_as VARCHAR, offset_scan BIGINT)")
            conn.executemany("INSERT INTO annotations VALUES (?, ?, ?)", rows)
            conn.execute("COPY annotations TO ? (FORMAT PARQUET, OVERWRITE TRUE)", [str(self.parquet)])
        emitted = {}
        export_document_annotations(
            [self.document("root", [self.scan1])],
            lambda data, path: emitted.update({path: data}),
            str(self.parquet),
        )
        prefix = "document/NL-HaNA_1.04.02_1053_0001-0001.annotations"
        for index, (tag, _) in enumerate(NER_CATEGORIES):
            with self.subTest(tag=tag):
                collection = emitted[f"{prefix}.{tag.lower()}.json"]
                page = emitted[f"{prefix}.{tag.lower()}/page-1.json"]
                self.assertEqual(collection["total"], 1)
                self.assertEqual(page["items"][0]["id"], rows[index][0])


if __name__ == "__main__":
    unittest.main()
