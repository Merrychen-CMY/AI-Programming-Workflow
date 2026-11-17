"""Candidate integrity tests; these do not execute or accept a product."""
import hashlib
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from dot_runtime import snapshot


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.product = self.root / "product"
        self.product.mkdir()
        (self.product / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
        self.candidate = self.root / "candidate"

    def test_freeze_and_verify_real_files(self):
        manifest = snapshot.snapshot_project(self.product, self.candidate)
        self.assertEqual(manifest["files"]["main.c"], hashlib.sha256((self.product / "main.c").read_bytes()).hexdigest())
        self.assertTrue(snapshot.verify_snapshot(self.candidate, manifest))
        self.assertEqual((self.candidate / "main.c").read_bytes(), (self.product / "main.c").read_bytes())

    def test_add_modify_delete_each_invalidates_candidate(self):
        manifest = snapshot.snapshot_project(self.product, self.candidate)
        main = self.candidate / "main.c"
        original = main.read_bytes()
        main.write_bytes(b"tampered")
        self.assertFalse(snapshot.verify_snapshot(self.candidate, manifest))
        main.write_bytes(original)
        (self.candidate / "hidden.h").write_bytes(b"new")
        self.assertFalse(snapshot.verify_snapshot(self.candidate, manifest))
        (self.candidate / "hidden.h").unlink()
        main.unlink()
        self.assertFalse(snapshot.verify_snapshot(self.candidate, manifest))

    def test_manifest_id_is_verified_not_trusted(self):
        manifest = snapshot.snapshot_project(self.product, self.candidate)
        manifest["id"] = "0" * 64
        self.assertFalse(snapshot.verify_snapshot(self.candidate, manifest))

    def test_firmware_build_inputs_are_retained_and_caches_excluded(self):
        for directory in (".git", "__pycache__", "build"):
            (self.product / directory).mkdir()
            (self.product / directory / "data.bin").write_bytes(b"content")
        manifest = snapshot.snapshot_project(self.product, self.candidate)
        self.assertIn("build/data.bin", manifest["files"])
        self.assertNotIn(".git/data.bin", manifest["files"])
        self.assertNotIn("__pycache__/data.bin", manifest["files"])

    def test_symlink_input_is_rejected_even_if_target_is_inside(self):
        try:
            (self.product / "linked.c").symlink_to(self.product / "main.c")
        except OSError:
            self.skipTest("Symlink creation is unavailable on this host")
        with self.assertRaises(ValueError):
            snapshot.snapshot_project(self.product, self.candidate)

    def test_windows_junction_reparse_flag_is_rejected(self):
        fake = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        with patch.object(Path, "lstat", return_value=fake):
            with self.assertRaises(ValueError):
                snapshot._reject_link(self.product)

    def test_destination_cannot_be_inside_product_or_contain_it(self):
        with self.assertRaises(ValueError):
            snapshot.snapshot_project(self.product, self.product / "candidate")
        with self.assertRaises(ValueError):
            snapshot.snapshot_project(self.product, self.root)

    def test_existing_candidate_is_never_overwritten(self):
        manifest = snapshot.snapshot_project(self.product, self.candidate)
        with self.assertRaises(ValueError):
            snapshot.snapshot_project(self.product, self.candidate)
        self.assertTrue(snapshot.verify_snapshot(self.candidate, manifest))

    def test_concurrent_source_change_rejects_copy(self):
        original_digest = snapshot._stable_digest
        def change_after_copy(source, destination=None):
            digest = original_digest(source, destination)
            if destination is not None:
                source.write_bytes(b"concurrent writer")
            return digest
        with patch.object(snapshot, "_stable_digest", side_effect=change_after_copy):
            with self.assertRaises(ValueError):
                snapshot.snapshot_project(self.product, self.candidate)

    def test_empty_product_rejected(self):
        (self.product / "main.c").unlink()
        with self.assertRaises(ValueError):
            snapshot.snapshot_project(self.product, self.candidate)

    def test_manifest_path_escape_cannot_validate(self):
        manifest = snapshot.snapshot_project(self.product, self.candidate)
        manifest = snapshot._manifest({"../product/main.c": manifest["files"]["main.c"]},
                                      {"../product/main.c": manifest["modes"]["main.c"]})
        self.assertFalse(snapshot.verify_snapshot(self.candidate, manifest))

    @unittest.skipIf(os.name == "nt", "POSIX executable bits are not available on Windows")
    def test_executable_mode_is_copied_and_bound_to_candidate(self):
        source = self.product / "run.sh"
        source.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        source.chmod(0o755)
        manifest = snapshot.snapshot_project(self.product, self.candidate)
        self.assertEqual(manifest["modes"]["run.sh"], 0o755)
        self.assertTrue(snapshot.verify_snapshot(self.candidate, manifest))
        (self.candidate / "run.sh").chmod(0o644)
        self.assertFalse(snapshot.verify_snapshot(self.candidate, manifest))

    def test_mode_manifest_cannot_omit_a_file(self):
        manifest = snapshot.snapshot_project(self.product, self.candidate)
        manifest["modes"].clear()
        self.assertFalse(snapshot.verify_snapshot(self.candidate, manifest))


if __name__ == "__main__":
    unittest.main()
