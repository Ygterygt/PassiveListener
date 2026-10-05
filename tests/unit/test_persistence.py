import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from passivelistener.persistence import orphan_count, persist_segment
from passivelistener.windows_input import verified_input


@unittest.skipUnless(os.name == "nt", "Windows atomic rename and ACLs required")
class PersistenceTests(unittest.TestCase):
    def test_utf8_immutable_unique_publication(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"
            paths = [persist_segment(root, "sentetik: ığüşöç İĞÜŞÖÇ") for _ in range(2)]
            self.assertNotEqual(*paths)
            for path in paths:
                self.assertEqual(path.read_bytes(), "sentetik: ığüşöç İĞÜŞÖÇ".encode())
            self.assertEqual(orphan_count(root), 0)

    def test_interruption_retains_partial_never_publishes(self) -> None:
        for stage in ("after_flush", "before_publish"):
            with self.subTest(stage=stage), TemporaryDirectory() as temporary:
                root = Path(temporary) / "private"

                def fail(point: str, expected: str = stage) -> None:
                    if point == expected:
                        raise OSError("synthetic interruption")

                with self.assertRaises(OSError):
                    persist_segment(root, "sentetik", fault=fail)
                self.assertEqual(list(root.glob("*.txt")), [])
                self.assertEqual(orphan_count(root), 1)
                self.assertEqual(next(root.glob("*.partial")).read_bytes(), b"sentetik")
                persist_segment(root, "next synthetic segment")
                self.assertEqual(orphan_count(root), 1)

    def test_reject_broad_directory_before_writing(self) -> None:
        with TemporaryDirectory() as temporary:
            with self.assertRaises(OSError):
                persist_segment(Path(temporary), "synthetic")
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_invalid_input_does_not_create_directory(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"
            for text in ("", "x" * (1024 * 1024 + 1), "\ud800"):
                with self.assertRaises((ValueError, UnicodeError)):
                    persist_segment(root, text)
                self.assertFalse(root.exists())

    def test_model_ancestor_cannot_be_renamed(self) -> None:
        import hashlib

        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "models"
            root.mkdir()
            model = root / "synthetic.bin"
            model.write_bytes(b"synthetic model")
            with verified_input(model, hashlib.sha256(model.read_bytes()).hexdigest(), 15):
                with self.assertRaises(OSError):
                    root.rename(root.with_name("moved"))


    def test_archive_consumes_only_finalized_private_segment(self) -> None:
        from passivelistener.archive import AGE_NS, archive_transcripts

        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"
            path = persist_segment(root, "synthetic archive input")
            now = 1791136800000000000
            os.utime(path, ns=(now - AGE_NS - 100, now - AGE_NS - 100))
            result = archive_transcripts(root, now_ns=now)
            self.assertEqual(result.archived, 1)
            self.assertFalse(path.exists())
            self.assertEqual(archive_transcripts(root, now_ns=now).archived, 0)
