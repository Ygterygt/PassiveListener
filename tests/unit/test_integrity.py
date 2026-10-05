import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from passivelistener.integrity import IntegrityError, verify_file

PAYLOAD = b"synthetic fixture only"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


def test_valid_and_corrupt_same_size(tmp_path: Path) -> None:
    artifact = tmp_path / "model.bin"
    artifact.write_bytes(PAYLOAD)
    verify_file(artifact, DIGEST, len(PAYLOAD))
    artifact.write_bytes(b"x" * len(PAYLOAD))
    with pytest.raises(IntegrityError, match="mismatch"):
        verify_file(artifact, DIGEST, len(PAYLOAD))


@pytest.mark.parametrize("digest,size", [("", 1), ("0" * 64, 0), ("G" * 64, 1)])
def test_reject_invalid_manifest(tmp_path: Path, digest: str, size: int) -> None:
    with pytest.raises(IntegrityError, match="manifest"):
        verify_file(tmp_path / "missing", digest, size)


def test_missing_directory_and_wrong_size(tmp_path: Path) -> None:
    for path in (tmp_path / "missing", tmp_path):
        with pytest.raises(IntegrityError):
            verify_file(path, DIGEST, len(PAYLOAD))
    artifact = tmp_path / "model.bin"
    artifact.write_bytes(PAYLOAD)
    with pytest.raises(IntegrityError, match="mismatch"):
        verify_file(artifact, DIGEST, len(PAYLOAD) + 1)


def test_optimized_python_fails_closed(tmp_path: Path) -> None:
    artifact = tmp_path / "model.bin"
    artifact.write_bytes(PAYLOAD)
    result = subprocess.run(
        [sys.executable, "-O", "-m", "passivelistener.cli", "verify-artifact",
         str(artifact), "--sha256", "0" * 64, "--size", str(len(PAYLOAD))],
        env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert result.stderr.strip() == "artifact verification failed"
    assert str(artifact) not in result.stderr
