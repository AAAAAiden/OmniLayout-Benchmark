"""Offline provenance checks using synthetic boards and temporary inventories."""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "audit_dataset_provenance", ROOT / "scripts" / "audit_dataset_provenance.py"
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class ProvenanceAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.source = self.directory / "private"
        self.dataset = self.source / "datasets"
        self.dataset.mkdir(parents=True)
        self.metadata = self.directory / "public_metadata"
        self.metadata.mkdir()
        self.manifest = self.metadata / "manifest.json"
        self.graphs = []

    def add_graph(self, graph_id, board):
        path = self.dataset / f"{graph_id}.brd"
        path.write_bytes(board)
        self.graphs.append({
            "path": f"graph/{graph_id}_graph.json",
            "sha256": hashlib.sha256(graph_id.encode()).hexdigest(),
        })
        return path

    def run_audit(self):
        self.manifest.write_text(
            json.dumps({"version": "v0.1.0", "files": self.graphs}),
            encoding="utf-8",
        )
        with redirect_stdout(io.StringIO()):
            return audit.audit(self.source, self.manifest, self.metadata)

    def test_library_notice_and_filename_brand_do_not_clear_design(self):
        self.add_graph("Adafruit Synthetic", b"""<eagle><drawing><board>
          <libraries><library><description>CC BY-SA 4.0</description></library></libraries>
          <plain><text>PRIVATE_DESIGN_TEXT_DO_NOT_COPY</text></plain>
        </board></drawing></eagle>""")
        inventory, summary = self.run_audit()
        graph = inventory["graphs"][0]
        self.assertEqual(graph["board_or_schematic_notice_signal_count"], 0)
        self.assertGreater(graph["embedded_library_notice_signal_count"], 0)
        self.assertEqual(graph["brand_hints"][0]["basis"], "graph_filename_only")
        self.assertFalse(graph["release_clearance"])
        self.assertIsNone(graph["dataset_license"])
        self.assertEqual(summary["graphs_cleared_for_redistribution"], 0)
        self.assertNotIn("PRIVATE_DESIGN_TEXT_DO_NOT_COPY", json.dumps(inventory))

    def test_lineage_and_urls_omit_private_paths_credentials_and_raw_xml(self):
        board = b"""<eagle><drawing><board><description>MIT License
          https://example.org/source?tracking=1#part
          https://user:password@example.org/private
          http://localhost/private http://internal.local/private
          https://example.org/key?api_key=do-not-copy
        </description></board></drawing></eagle>"""
        self.add_graph("Synthetic", board)
        pair = self.dataset / "paired" / "Synthetic"
        pair.mkdir(parents=True)
        (pair / "sources.txt").write_text(
            "output_base=Synthetic\nsource_group=synthetic_test\n"
            "source_pair_dir=C:\\Users\\private_person\\source_pair\n"
            "src_brd=/Users/private_person/private/source.brd\n"
            "src_sch=C:\\Users\\private_person\\source.sch\n",
            encoding="utf-8",
        )
        inventory, _ = self.run_audit()
        text = json.dumps(inventory)
        for private in ("private_person", "password", "api_key", "do-not-copy", "internal.local", "<eagle"):
            self.assertNotIn(private, text)
        evidence = inventory["local_source_evidence"][hashlib.sha256(board).hexdigest()]
        self.assertEqual(evidence["public_urls"][0]["url"], "https://example.org/source")
        self.assertEqual(len(evidence["public_urls"]), 1)
        self.assertTrue(all(not Path(path).is_absolute() for path in evidence["paths"]))
        graph = inventory["graphs"][0]
        self.assertGreater(graph["board_or_schematic_notice_signal_count"], 0)
        self.assertFalse(graph["release_clearance"])
        for private in ("C:\\Users\\private_person", "C:/Users/private_person", "\\\\server\\private", "/Users/private_person", "../private"):
            with self.subTest(private_path_type=private.split(":")[0]):
                self.assertFalse(audit.safe_lineage_value(private))

    def test_exact_external_claim_requires_matching_local_hash_for_count(self):
        board = b"<eagle><drawing><board/></drawing></eagle>"
        self.add_graph("Matching", board)
        self.add_graph("Mismatching", board)
        entries = [
            {"graph_id": "Matching", "local_raw_board_sha256": hashlib.sha256(board).hexdigest()},
            {"graph_id": "Mismatching", "local_board_sha256": "0" * 64},
        ]
        for entry in entries:
            entry.update(match_status="verified_exact_source", license_id="MIT")
        (self.metadata / "license_evidence_test.json").write_text(
            json.dumps({"entries": entries}), encoding="utf-8"
        )
        inventory, summary = self.run_audit()
        self.assertEqual(summary["graphs_with_verified_exact_upstream_source_evidence"], 1)
        self.assertEqual(summary["external_exact_source_claims_with_mismatching_local_hash"], 1)
        self.assertEqual(summary["graphs_cleared_for_redistribution"], 0)
        self.assertTrue(all(not graph["release_clearance"] for graph in inventory["graphs"]))

    def test_duplicate_source_copies_and_revision_conflicts_are_separate(self):
        board = b"<eagle><drawing><board/></drawing></eagle>"
        self.add_graph("Synthetic", board)
        copies = self.dataset / "copies"
        copies.mkdir()
        (copies / "Synthetic.brd").write_bytes(board)
        revisions = self.dataset / "revisions"
        revisions.mkdir()
        (revisions / "Synthetic.brd").write_bytes(board.replace(b"<board/>", b"<board><plain/></board>"))
        (self.dataset / "Synthetic.sch").write_bytes(b"<eagle><drawing><schematic/></drawing></eagle>")
        inventory, summary = self.run_audit()
        self.assertEqual(summary["local_raw_source_file_copies"], 4)
        self.assertEqual(summary["unique_local_raw_source_hashes"], 3)
        self.assertEqual(summary["graphs_with_multiple_distinct_local_board_candidates"], 1)
        self.assertEqual(inventory["graphs"][0]["board_revision_status"], "multiple_distinct_local_candidates")

    def test_archives_are_indexed_without_raw_extraction_or_unsafe_paths(self):
        self.add_graph("Synthetic", b"<eagle><drawing><board/></drawing></eagle>")
        with zipfile.ZipFile(self.dataset / "private.zip", "w") as bundle:
            bundle.writestr("boards/Synthetic.brd", b"PRIVATE_ARCHIVE_BODY")
            bundle.writestr("metadata/sources.txt", b"PRIVATE_METADATA_BODY")
            bundle.writestr("../private/sources.txt", b"private traversal")
            bundle.writestr("C:/Users/private_person/sources.txt", b"private absolute")
        inventory, _ = self.run_audit()
        archive = inventory["source_archive_inventory"][0]
        self.assertEqual(archive["member_type_counts"]["brd"], 1)
        self.assertEqual(archive["candidate_metadata_members"], ["metadata/sources.txt"])
        serialized = json.dumps(inventory)
        for private in ("PRIVATE_ARCHIVE_BODY", "PRIVATE_METADATA_BODY", "private_person", "../private"):
            self.assertNotIn(private, serialized)
        self.assertFalse((self.dataset / "boards").exists())

    def test_external_refresh_does_not_reread_sources_or_grant_clearance(self):
        board = b"<eagle><drawing><board/></drawing></eagle>"
        self.add_graph("Synthetic", board)
        inventory, summary = self.run_audit()
        for filename, value in (("provenance.json", inventory), ("provenance_summary.json", summary)):
            (self.metadata / filename).write_text(json.dumps(value), encoding="utf-8")
        (self.metadata / "license_evidence_test.json").write_text(json.dumps({"entries": [{
            "graph_id": "Synthetic", "local_board_sha256": hashlib.sha256(board).hexdigest(),
            "match_status": "verified_exact_source", "license_id": "MIT",
        }]}), encoding="utf-8")
        with mock.patch("sys.argv", ["audit_dataset_provenance.py", "--refresh-external-evidence", "--output", str(self.metadata)]), mock.patch.object(audit, "audit") as source_scan, redirect_stdout(io.StringIO()):
            audit.main()
            source_scan.assert_not_called()
        refreshed = json.loads((self.metadata / "provenance_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(refreshed["graphs_with_verified_exact_upstream_source_evidence"], 1)
        self.assertEqual(refreshed["graphs_cleared_for_redistribution"], 0)


if __name__ == "__main__":
    unittest.main()
