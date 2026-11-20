"""File filter compatibility without decrypting or changing endpoint policy."""
import errno
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dot_runtime import file_access as f, snapshot
from dot_runtime.controller import atomic_json, read_json


class FileAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_same_commit_retries_without_repeating_producer(self):
        target = self.root / 'record.json'
        atomic_json(target, {'old': True})
        original = f.os.replace
        calls = []
        def busy_once(source, destination):
            calls.append(source)
            if len(calls) == 1:
                raise PermissionError(errno.EACCES, 'busy')
            return original(source, destination)
        with patch.object(f.os, 'replace', side_effect=busy_once), patch.object(f.time, 'sleep'):
            atomic_json(target, {'new': True})
        self.assertEqual(read_json(target), {'new': True})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])

    def test_permanent_denial_keeps_original_and_staged_evidence(self):
        target = self.root / 'record.json'
        atomic_json(target, {'old': True})
        with patch.object(f.os, 'replace', side_effect=PermissionError(errno.EACCES, 'busy')), patch.object(f.time, 'sleep'):
            with self.assertRaisesRegex(f.FileAccessError, 'FILE_REPLACE_BLOCKED'):
                atomic_json(target, {'new': True})
        self.assertEqual(read_json(target), {'old': True})
        stages = list(self.root.glob('*.tmp'))
        self.assertEqual(len(stages), 1)
        self.assertEqual(read_json(stages[0]), {'new': True})

    def test_encrypted_content_is_not_json_or_candidate(self):
        path = self.root / 'file.c'
        path.write_bytes(b'%ONEDLP_HEADER%opaque')
        with self.assertRaisesRegex(f.FileAccessError, 'FILE_ENCRYPTED_VIEW'):
            f.read_content(path)
        with self.assertRaisesRegex(f.FileAccessError, 'FILE_ENCRYPTED_VIEW'):
            snapshot._stable_digest(path)

    def test_readback_difference_is_explicit(self):
        path = self.root / 'file.json'; path.write_bytes(b'changed')
        with self.assertRaisesRegex(f.FileAccessError, 'FILE_WRITE_VERIFY_FAILED'):
            f.verify_written(path, b'expected')

    def test_executable_extensions_survive_freezing(self):
        product = self.root / 'product'; product.mkdir()
        for extension in ['.exe', '.bat', '.cmd', '.c', '.h']:
            (product / ('inert' + extension)).write_bytes(b'not executable fixture\n')
        candidate = self.root / 'candidate'
        manifest = snapshot.snapshot_project(product, candidate)
        self.assertEqual(len(manifest['files']), 5)
        self.assertTrue(snapshot.verify_snapshot(candidate, manifest))

    def test_path_and_handle_can_have_different_stable_size_and_mode(self):
        path = self.root / 'inert.exe'; path.write_bytes(b'logical content')
        real = snapshot._reject_link
        def filtered(value):
            s = real(value)
            return SimpleNamespace(st_dev=s.st_dev, st_ino=s.st_ino, st_size=s.st_size+4096,
                                   st_mtime_ns=s.st_mtime_ns, st_mode=s.st_mode | 0o111)
        with patch.object(snapshot, '_reject_link', side_effect=filtered):
            self.assertEqual(snapshot._stable_digest(path), hashlib.sha256(b'logical content').hexdigest())

    def test_change_within_same_observation_method_still_rejected(self):
        path = self.root / 'file.c'; path.write_bytes(b'original')
        s = snapshot._reject_link(path)
        changed = SimpleNamespace(st_dev=s.st_dev, st_ino=s.st_ino, st_size=s.st_size+1,
                                  st_mtime_ns=s.st_mtime_ns, st_mode=s.st_mode)
        with patch.object(snapshot, '_reject_link', side_effect=[s, changed]):
            with self.assertRaisesRegex(ValueError, 'changed while reading'):
                snapshot._stable_digest(path)
