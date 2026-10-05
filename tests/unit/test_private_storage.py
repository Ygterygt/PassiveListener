import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from passivelistener.private_storage import private_directory


@unittest.skipUnless(os.name == "nt", "Windows ACLs required")
class PrivateStorageTests(unittest.TestCase):
    def test_private_creation_and_idempotence(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"
            with private_directory(root, create=True):
                (root / "synthetic.txt").write_text("synthetic: merhaba", encoding="utf-8")
            with private_directory(root, create=True):
                self.assertEqual((root / "synthetic.txt").read_text(), "synthetic: merhaba")

    def test_existing_inherited_acl_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "inherited"
            root.mkdir()
            with self.assertRaises(OSError), private_directory(root, create=True):
                self.fail("inherited directory accepted")
            self.assertEqual(list(root.iterdir()), [])

    def test_file_instead_of_directory_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "file"
            root.write_bytes(b"synthetic")
            with self.assertRaises(OSError), private_directory(root, create=True):
                self.fail("file accepted")
            self.assertEqual(root.read_bytes(), b"synthetic")

    def test_held_directory_cannot_be_renamed(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"
            with private_directory(root, create=True), self.assertRaises(OSError):
                root.rename(root.with_name("moved"))

    def test_broadened_acl_fails_closed(self) -> None:
        import subprocess

        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"
            with private_directory(root, create=True):
                pass
            changed = subprocess.run(
                ["icacls.exe", str(root), "/grant", "*S-1-1-0:(OI)(CI)R"],
                capture_output=True, check=False,
            )
            self.assertEqual(changed.returncode, 0)
            with self.assertRaises(OSError), private_directory(root):
                self.fail("broadened ACL accepted")
