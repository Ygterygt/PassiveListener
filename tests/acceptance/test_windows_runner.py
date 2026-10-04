import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class WindowsRunnerTests(unittest.TestCase):
    def invoke(self, driver, cases=None, suite='task'):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = Path(__file__).parent
            for name in ('Invoke-WindowsAcceptance.ps1', 'archive_contract.py'):
                shutil.copyfile(source / name, root / name)
            (root / 'windows_cases.json').write_text(json.dumps({'task': ['check'] if cases is None else cases}))
            (root / 'driver.ps1').write_text(driver)
            return subprocess.run(['pwsh', '-NoProfile', '-File', str(root / 'Invoke-WindowsAcceptance.ps1'),
                                   '-Driver', str(root / 'driver.ps1'), '-EvidenceDirectory', str(root / 'evidence'),
                                   '-Suite', suite], capture_output=True, text=True).returncode

    def test_evidence_exists_nonempty_and_contained(self):
        contract = "@{Status='pass';Command='controlled double';Expected='fixture';Observed='fixture';Evidence=$name}"
        for setup, expected in [
                ("$name='missing.txt'", 1),
                ("$name='empty.txt'; New-Item (Join-Path $EvidenceDirectory $name) | Out-Null", 1),
                ("$name='../outside.txt'; Set-Content (Join-Path $EvidenceDirectory $name) 'synthetic'", 1),
                ("$name='ok.txt'; Set-Content (Join-Path $EvidenceDirectory $name) 'synthetic'", 0)]:
            with self.subTest(setup=setup):
                code = self.invoke('param($Case,$EvidenceDirectory)\n' + setup + '\n' + contract)
                self.assertEqual(code, expected)

    def test_empty_suite_rejected(self):
        self.assertNotEqual(self.invoke("throw 'must not execute'", cases=[]), 0)

    def test_archive_noop_cannot_claim_pass(self):
        self.assertNotEqual(self.invoke("param($RequestPath)\n@{Status='pass'}", suite='archive'), 0)
