"""Collect full notices for the Python runtime and PyInstaller bootloader."""

import importlib.metadata
import shutil
import sys
from pathlib import Path

notices = Path("dist/notices")
notices.mkdir(parents=True, exist_ok=True)
python_license = Path(sys.base_prefix) / "LICENSE.txt"
shutil.copyfile(python_license, notices / "Python-LICENSE.txt")
distribution = importlib.metadata.distribution("pyinstaller")
licenses = [p for p in distribution.files or [] if p.name == "COPYING.txt"]
if len(licenses) != 1:
    raise RuntimeError("missing or ambiguous PyInstaller license")
shutil.copyfile(str(distribution.locate_file(licenses[0])), notices / "PyInstaller-COPYING.txt")

for source in Path("packaging/notices").iterdir():
    if source.is_file():
        shutil.copyfile(source, notices / source.name)
