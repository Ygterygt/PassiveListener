import tempfile
import unittest
from pathlib import Path
import zipfile
from archive_contract import AGE_NS, CASES, exercise


class ControlledArchive:
    """Test double only. Not a production archive implementation."""
    def __init__(self, broken=None):
        self.broken = broken

    def __call__(self, request):
        root = Path(request['root'])
        count = 0
        for path in sorted(root.glob('*.txt')):
            age = request['now_ns'] - path.stat().st_mtime_ns
            if age < AGE_NS or (age == AGE_NS and self.broken != 'boundary'):
                continue
            if path.name in request['active'] and self.broken != 'active':
                continue
            try:
                data = path.read_bytes()
            except PermissionError:
                if self.broken == 'locked':
                    raise AssertionError('broken locked-file recovery')
                continue
            if request['fault'] in ('verify', 'write'):
                if self.broken == 'delete_on_failure':
                    path.unlink()
                return
            archive = root / (path.stem + '.zip')
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr(path.name, b'wrong' if self.broken == 'zip_bytes' else data)
            path.unlink()
            count += 1
            if request['fault'] == 'after_first_commit' and count == 1:
                if self.broken == 'partial_loss':
                    for pending in root.glob('*.txt'):
                        pending.unlink()
                return
        if self.broken == 'duplicate' and list(root.glob('*.zip')):
            source = next(root.glob('*.zip'))
            (root / 'duplicate.zip').write_bytes(source.read_bytes())


class ArchiveContractTests(unittest.TestCase):
    def test_controlled_outcomes(self):
        for case in CASES:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                exercise(ControlledArchive(), case, Path(directory) / 'fixtures')

    def test_intentionally_broken_outcomes_rejected(self):
        for broken, case in [('boundary', 'boundary'), ('active', 'exclusions'),
                             ('locked', 'exclusions'), ('zip_bytes', 'boundary'),
                             ('delete_on_failure', 'verification_failure'),
                             ('delete_on_failure', 'write_failure'),
                             ('partial_loss', 'partial_retry'), ('duplicate', 'partial_retry')]:
            with self.subTest(broken=broken, case=case), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(AssertionError):
                    exercise(ControlledArchive(broken), case, Path(directory) / 'fixtures')
