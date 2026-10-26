"""Check graph-only packaging, trusted downloads, and raw-file exclusion."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


packaging = load_script("package_dataset")
downloader = load_script("download_dataset")
guard = load_script("check_release")


class DatasetReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "source"
        self.source.mkdir()
        graph = {"board_boundary": {}, "ic_library": [], "ic_position": [], "ic_dimension": [], "netlist": []}
        (self.source / "fixture_graph.json").write_text(json.dumps(graph), encoding="utf-8")
        # A source directory can contain raw private files; the packager must
        # select only graph JSON, never copy those files or an existing archive.
        (self.source / "PRIVATE.BRD").write_text("raw private board", encoding="utf-8")
        (self.source / "old.zip").write_bytes(b"private archive")
        self.metadata = self.directory / "metadata"
        self.release = packaging.package_dataset(self.source, self.directory / "dist", self.metadata)
        self.archive = self.directory / "dist" / self.release["archive"]
        self.manifest = json.loads((self.metadata / "manifest.json").read_text(encoding="utf-8"))
        self.summary = json.loads((self.metadata / "summary.json").read_text(encoding="utf-8"))

    def install(self, archive=None, release=None, manifest=None):
        downloader.install_archive(archive or self.archive, self.directory / "installed", release or self.release, manifest or self.manifest, self.summary)

    def test_archive_contains_only_graphs_and_notices(self):
        with zipfile.ZipFile(self.archive) as bundle:
            self.assertEqual(set(bundle.namelist()), {"graph/fixture_graph.json", "manifest.json", "summary.json", "LICENSE_DATA.md"})
        self.install()
        self.assertTrue((self.directory / "installed" / "graph" / "fixture_graph.json").is_file())

    def test_archive_checksum_mismatch_does_not_install(self):
        damaged = self.directory / "damaged.zip"
        damaged.write_bytes(self.archive.read_bytes() + b"tamper")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.install(archive=damaged)
        self.assertFalse((self.directory / "installed").exists())

    def test_unexpected_raw_board_member_rejected(self):
        damaged = self.directory / "unexpected.zip"
        damaged.write_bytes(self.archive.read_bytes())
        with zipfile.ZipFile(damaged, "a") as bundle:
            bundle.writestr("PRIVATE.BRD", b"raw board")
        release = dict(self.release, bytes=damaged.stat().st_size, sha256=hashlib.sha256(damaged.read_bytes()).hexdigest())
        with self.assertRaisesRegex(ValueError, "unexpected"):
            self.install(archive=damaged, release=release)
        self.assertFalse((self.directory / "installed").exists())

    def test_partial_prediction_is_not_ground_truth(self):
        content = json.dumps({"ic_position": [], "board_boundary": {}}).encode()
        with self.assertRaisesRegex(ValueError, "five-section"):
            packaging.validate_graph(content, "prediction_graph.json")

    def test_existing_dataset_is_never_overwritten(self):
        self.install()
        with self.assertRaises(FileExistsError):
            self.install()

    def test_release_guard_blocks_raw_boards_case_insensitively(self):
        for name in ("private.BRD", "nested/private.brd.backup", "dataset.zip", "datasets/graph.json", "keys"):
            with self.subTest(name=name):
                self.assertIsNotNone(guard.check_file(name, b"content"))
        self.assertIsNone(guard.check_file("docs/DATASET.md", b"Raw .brd files are excluded."))


if __name__ == "__main__":
    unittest.main()
