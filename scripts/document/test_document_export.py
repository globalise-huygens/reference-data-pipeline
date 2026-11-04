import unittest
import tempfile
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from types import SimpleNamespace
from urllib.parse import urldefrag
from unittest.mock import patch

import duckdb
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import export_documents
import export_manifests
from document_annotations import export_document_annotations
from export import (
    create_document_range,
    document_identified_by_jsonld,
    document_physical_to_jsonld,
)
from models import Base, Document, DocumentIdentificationMethod, Inventory


class ConcurrentAnnotationEmitterTests(unittest.TestCase):
    def test_writes_run_concurrently_and_are_drained(self):
        barrier = Barrier(2, timeout=5)
        written = {}
        lock = Lock()

        def emit(data, path):
            barrier.wait()
            with lock:
                written[path] = data

        with export_documents.concurrent_annotation_emitter(emit, 2) as submit:
            submit({"value": 1}, "one")
            submit({"value": 2}, "two")
        self.assertEqual(written, {"one": {"value": 1}, "two": {"value": 2}})

    def test_same_path_preserves_write_order(self):
        started = Event()
        release = Event()
        second_started = Event()
        written = []

        def emit(data, path):
            if data["value"] == 1:
                started.set()
                if not release.wait(5):
                    raise TimeoutError("first write was not released")
            else:
                second_started.set()
            written.append((path, data["value"]))

        def write_both():
            with export_documents.concurrent_annotation_emitter(emit, 2) as submit:
                submit({"value": 1}, "same")
                submit({"value": 2}, "same")

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(write_both)
            try:
                self.assertTrue(started.wait(5))
                self.assertFalse(second_started.wait(0.1))
            finally:
                release.set()
            future.result(timeout=5)
        self.assertEqual(written, [("same", 1), ("same", 2)])

    def test_pending_writes_are_bounded(self):
        started = Event()
        release = Event()
        attempting_fifth = Event()
        fifth_submitted = Event()

        def emit(data, path):
            started.set()
            if not release.wait(5):
                raise TimeoutError("writes were not released")

        def produce():
            with export_documents.concurrent_annotation_emitter(emit, 2) as submit:
                for index in range(5):
                    if index == 4:
                        attempting_fifth.set()
                    submit({}, str(index))
                fifth_submitted.set()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(produce)
            try:
                self.assertTrue(started.wait(5))
                self.assertTrue(attempting_fifth.wait(5))
                self.assertFalse(fifth_submitted.wait(0.1))
            finally:
                release.set()
            future.result(timeout=5)
        self.assertTrue(fifth_submitted.is_set())

    def test_write_failures_and_invalid_worker_counts_are_reported(self):
        def fail(data, path):
            raise RuntimeError("upload failed")

        with self.assertRaisesRegex(RuntimeError, "upload failed"):
            with export_documents.concurrent_annotation_emitter(fail, 2) as submit:
                submit({}, "failed")
        with self.assertRaisesRegex(ValueError, "positive"):
            with export_documents.concurrent_annotation_emitter(fail, 0):
                pass

    def test_one_worker_writes_synchronously(self):
        written = []
        with export_documents.concurrent_annotation_emitter(
            lambda data, path: written.append(path), 1
        ) as submit:
            submit({}, "one")
            self.assertEqual(written, ["one"])

    def test_worker_configuration(self):
        with (
            patch.dict(os.environ, {"DOCUMENT_ANNOTATION_UPLOAD_WORKERS": "3"}),
            patch.object(sys, "argv", ["export_documents.py"]),
        ):
            self.assertEqual(export_documents.parse_args().annotation_upload_workers, 3)
        with (
            patch.dict(os.environ, {"DOCUMENT_ANNOTATION_UPLOAD_WORKERS": "0"}),
            patch.object(sys, "argv", ["export_documents.py"]),
            patch("sys.stderr"),
            self.assertRaises(SystemExit),
        ):
            export_documents.parse_args()
        with (
            patch.dict(os.environ, {"DOCUMENT_ANNOTATION_UPLOAD_WORKERS": "3"}),
            patch.object(sys, "argv", ["export_documents.py", "--annotation-upload-workers", "1"]),
        ):
            self.assertEqual(export_documents.parse_args().annotation_upload_workers, 1)


class DocumentExportTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            segmentation = DocumentIdentificationMethod(
                id="segmentation", name="Segmentation model"
            )
            manual = DocumentIdentificationMethod(id="manual", name="Manual")
            inventory = Inventory(id="inventory", inventory_number="1053")
            empty_inventory = Inventory(id="empty", inventory_number="1054")
            root = Document(
                id="root", title="Root", inventory=inventory, method=segmentation
            )
            child = Document(
                id="child",
                title="Child",
                inventory=inventory,
                method=manual,
                part_of=root,
            )
            grandchild = Document(
                id="grandchild",
                title="Grandchild",
                inventory=inventory,
                method=manual,
                part_of=child,
            )
            other_root = Document(
                id="other", title="Other", inventory=inventory, method=manual
            )
            session.add_all([root, child, grandchild, other_root, empty_inventory])
            session.commit()

    def tearDown(self):
        self.engine.dispose()

    def test_nt_external_identifier_has_same_classification_as_nt_index(self):
        external = SimpleNamespace(context="NT", identifier="179762", URL=None)
        document = SimpleNamespace(
            external_ids=[SimpleNamespace(external=external)], nt_index=42
        )
        identifiers = document_identified_by_jsonld(document)
        self.assertEqual([item["content"] for item in identifiers], ["179762", "42"])
        expected = {
            "id": "https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/thesaurus:bdb0e8ac-b1d7-47b6-84d0-cf99b667d051",
            "type": "Type",
            "_label": "NT Index Identifier",
        }
        self.assertEqual([item["classified_as"] for item in identifiers], [expected, expected])

        external.context = "OTHER"
        self.assertNotIn("classified_as", document_identified_by_jsonld(document)[0])

    def test_inventory_and_document_exports_select_roots(self):
        emitted = {}

        def capture(data, path, *args, **kwargs):
            emitted[path] = data

        with (
            patch.object(export_documents, "create_engine", return_value=self.engine),
            patch.object(export_documents, "output_framed_json", side_effect=capture),
            patch.object(export_documents, "generate_hydra_index_pages") as index,
            patch.object(export_documents, "export_document_annotations") as annotations,
            patch("export.load_scan_entities_from_parquet", return_value={}),
        ):
            export_documents.export_documents("unused", False, None, None)

        self.assertIn("inventory/1054.json", emitted)
        inventory_parts = emitted["inventory/1053.json"]["part"]
        self.assertEqual([part["_label"] for part in inventory_parts], ["Root"])
        self.assertEqual(
            [path for path in emitted if path.startswith("document/")],
            ["document/root.json"],
        )
        root = emitted["document/root.json"]
        child = root["part"][0]
        self.assertEqual(child["id"], root["id"] + "#document-child")
        self.assertEqual(child["part"][0]["id"], root["id"] + "#document-grandchild")
        document_members = index.call_args_list[-1].args[0]
        self.assertEqual([member["title"] for member in document_members], ["Root"])
        self.assertEqual([doc.title for doc in annotations.call_args.args[0]], ["Root"])

    def test_manifest_selects_roots_and_keeps_all_descendants(self):
        emitted = {}

        def capture(data, path, *args, **kwargs):
            emitted[path] = data

        with (
            patch.object(export_manifests, "create_engine", return_value=self.engine),
            patch.object(export_manifests, "output_framed_json", side_effect=capture),
        ):
            export_manifests.export_all_manifests("unused", False, None, None)

        self.assertIn("1054.manifest.json", emitted)
        ranges = emitted["1053.manifest.json"]["structures"][0]["items"]
        self.assertEqual([item["label"]["en"] for item in ranges], [["Root"]])
        child = ranges[0]["items"][0]
        self.assertEqual(child["label"]["en"], ["Child"])
        self.assertEqual(child["items"][0]["label"]["en"], ["Grandchild"])
        root_uri = ranges[0]["seeAlso"][0]["id"]
        self.assertEqual(child["seeAlso"][0]["id"], root_uri + "#document-child")
        self.assertEqual(
            child["items"][0]["seeAlso"][0]["id"], root_uri + "#document-grandchild"
        )

    def test_document_export_emits_annotation_files(self):
        emitted = {}

        with tempfile.TemporaryDirectory() as directory:
            parquet = f"{directory}/inventory.parquet"
            with duckdb.connect() as conn:
                conn.execute(
                    "COPY (SELECT NULL::VARCHAR AS annotation_id, "
                    "NULL::VARCHAR AS classified_as, NULL::BIGINT AS offset_scan, "
                    "NULL::VARCHAR AS filename "
                    "WHERE false) TO ? (FORMAT PARQUET)",
                    [parquet],
                )
            with (
                patch.object(export_documents, "create_engine", return_value=self.engine),
                patch.object(
                    export_documents, "output_framed_json",
                    side_effect=lambda data, path, **kwargs: emitted.update({path: data}),
                ),
                patch.object(export_documents, "generate_hydra_index_pages"),
                patch.object(
                    export_documents, "export_document_annotations",
                    side_effect=lambda docs, emit: export_document_annotations(docs, emit, parquet),
                ),
                patch("export.load_scan_entities_from_parquet", return_value={}),
            ):
                export_documents.export_documents("unused", False, None, None)

        self.assertEqual(len(emitted["document/root.annotations.json"]["member"]), 16)
        self.assertEqual(emitted["document/root.annotations.per_name.json"]["total"], 0)
        self.assertEqual(emitted["document/root.annotations.events/page-1.json"]["items"], [])

    def test_s3_annotation_uploads_finish_before_document_index(self):
        emitted = {}
        lock = Lock()

        def capture(data, path, **kwargs):
            with lock:
                emitted[path] = data

        def index(members, **kwargs):
            if kwargs["cat_title"] == "Document Collection":
                self.assertIn("document/root.annotations.events/page-1.json", emitted)
                self.assertEqual(
                    len([path for path in emitted if path.startswith("document/root.annotations")]),
                    33,
                )

        with tempfile.TemporaryDirectory() as directory:
            parquet = f"{directory}/inventory.parquet"
            with duckdb.connect() as conn:
                conn.execute(
                    "COPY (SELECT NULL::VARCHAR AS annotation_id, "
                    "NULL::VARCHAR AS classified_as, NULL::BIGINT AS offset_scan, "
                    "NULL::VARCHAR AS filename "
                    "WHERE false) TO ? (FORMAT PARQUET)",
                    [parquet],
                )
            with (
                patch.object(export_documents, "create_engine", return_value=self.engine),
                patch.object(export_documents, "output_framed_json", side_effect=capture),
                patch.object(export_documents, "generate_hydra_index_pages", side_effect=index),
                patch.object(
                    export_documents, "export_document_annotations",
                    side_effect=lambda docs, emit: export_document_annotations(docs, emit, parquet),
                ),
                patch("export.load_scan_entities_from_parquet", return_value={}),
            ):
                export_documents.export_documents(
                    "unused", False, object(), SimpleNamespace(bucket="test"), 2
                )

    def test_scan_range_fragments_are_unique_and_match_manifest_identifiers(self):
        scan = SimpleNamespace(
            filename="NL-HaNA_1.04.02_1053_0001",
            width=100,
            height=200,
            languages=None,
            get_image_url=lambda size: "https://example.org/image",
        )
        page = SimpleNamespace(id="page", scan=scan, scan_id="scan", recto_verso=None)

        def doc(identifier, children=(), pages=True):
            return SimpleNamespace(
                id=identifier,
                title=identifier,
                location=None,
                pages=[SimpleNamespace(page=page, index=0)] if pages else [],
                sub_documents=list(children),
                external_ids=[],
                document_types_linked=[],
            )

        grandchild = doc("c")
        child_a = doc("a", [grandchild])
        child_b = doc("b")
        no_scan = doc("no/scan", pages=False)
        ranged = doc("range")
        scan2 = SimpleNamespace(
            filename="NL-HaNA_1.04.02_1053_0002",
            width=100,
            height=200,
            languages=None,
            get_image_url=lambda size: "https://example.org/image",
        )
        page2 = SimpleNamespace(id="page2", scan=scan2, scan_id="scan2", recto_verso=None)
        ranged.pages.append(SimpleNamespace(page=page2, index=1))
        root = doc("root", [child_b, no_scan, ranged, child_a])
        root.pages.append(SimpleNamespace(page=page2, index=1))
        physical = document_physical_to_jsonld(root, links_parquet={})
        children = {part["_label"]: part for part in physical["part"] if "id" in part}
        root_id = physical["id"]
        self.assertEqual(children["a (Document)"]["id"], root_id + "#0001-0001")
        self.assertEqual(children["b (Document)"]["id"], root_id + "#0001-0001-2")
        self.assertEqual(children["range (Document)"]["id"], root_id + "#0001-0002")
        self.assertEqual(children["no/scan (Document)"]["id"], root_id + "#document-no%2Fscan")
        nested = children["a (Document)"]["part"][0]
        self.assertEqual(nested["id"], root_id + "#0001-0001-3")
        self.assertNotIn("referred_to_by", nested["carries"])
        self.assertEqual(urldefrag(nested["id"]).url, root_id)

        doc_range = create_document_range(root, "1053")
        self.assertEqual(doc_range["seeAlso"][0]["id"], root_id)
        child_ranges = {
            item["label"]["en"][0]: item
            for item in doc_range["items"]
            if item["type"] == "Range"
        }
        ids = [doc_range["id"]] + [item["id"] for item in child_ranges.values()]
        nested_range = next(
            item for item in child_ranges["a"]["items"] if item["type"] == "Range"
        )
        ids.append(nested_range["id"])
        self.assertEqual(len(ids), len(set(ids)))
        for identifier, item in child_ranges.items():
            metadata = next(m for m in item["metadata"] if m["label"]["en"] == ["Identifier"])
            self.assertEqual(
                root_id.rsplit("/document:", 1)[0] + "/document:" + metadata["value"]["none"][0],
                children[f"{identifier} (Document)"]["id"],
            )
            self.assertEqual(item["seeAlso"][0]["id"], children[f"{identifier} (Document)"]["id"])
        self.assertEqual(nested_range["seeAlso"][0]["id"], nested["id"])

        root.sub_documents.reverse()
        reordered = document_physical_to_jsonld(root, links_parquet={})
        reordered_ids = {
            part["_label"]: part["id"] for part in reordered["part"] if "id" in part
        }
        self.assertEqual(reordered_ids, {label: part["id"] for label, part in children.items()})


if __name__ == "__main__":
    unittest.main()
