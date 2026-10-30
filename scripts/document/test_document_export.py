import unittest
from types import SimpleNamespace
from urllib.parse import urldefrag
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import export_documents
import export_manifests
from export import create_document_range, document_physical_to_jsonld
from models import Base, Document, DocumentIdentificationMethod, Inventory


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

    def test_inventory_and_document_exports_select_roots(self):
        emitted = {}

        def capture(data, path, *args, **kwargs):
            emitted[path] = data

        with (
            patch.object(export_documents, "create_engine", return_value=self.engine),
            patch.object(export_documents, "output_framed_json", side_effect=capture),
            patch.object(export_documents, "generate_hydra_index_pages") as index,
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
