"""Actual Windows filesystem operations using only generated fixture bytes."""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from passivelistener.integrity import IntegrityError
from passivelistener.windows_input import verified_input

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics")
PAYLOAD = b"synthetic model fixture"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


def test_read_and_block_write_replace_ancestor_rename(tmp_path: Path) -> None:
    folder = tmp_path / "models"
    folder.mkdir()
    model = folder / "model.bin"
    model.write_bytes(PAYLOAD)
    other = tmp_path / "replacement.bin"
    other.write_bytes(b"x" * len(PAYLOAD))
    with verified_input(model, DIGEST, len(PAYLOAD)) as stream:
        assert stream.read() == PAYLOAD
        with pytest.raises(OSError):
            model.write_bytes(b"corruption")
        with pytest.raises(OSError):
            other.replace(model)
        with pytest.raises(OSError):
            folder.rename(tmp_path / "moved")
        # A native read-only consumer can open while the lease is retained.
        assert model.read_bytes() == PAYLOAD
    other.replace(model)
    assert model.read_bytes() != PAYLOAD
    assert stream.closed


def test_existing_writer_rejected(tmp_path: Path) -> None:
    model = tmp_path / "model.bin"
    model.write_bytes(PAYLOAD)
    with model.open("r+b"), pytest.raises(IntegrityError, match="locked"):
        with verified_input(model, DIGEST, len(PAYLOAD)):
            pytest.fail("writer must prevent lease")


def test_wrong_hash_and_consumer_failure_release_handles(tmp_path: Path) -> None:
    model = tmp_path / "model.bin"
    model.write_bytes(PAYLOAD)
    with pytest.raises(IntegrityError, match="mismatch"):
        with verified_input(model, "0" * 64, len(PAYLOAD)):
            pytest.fail("corrupt input must not reach consumer")
    with pytest.raises(RuntimeError):
        with verified_input(model, DIGEST, len(PAYLOAD)):
            raise RuntimeError("synthetic consumer failure")
    model.unlink()


def test_hard_link_rejected(tmp_path: Path) -> None:
    model = tmp_path / "model.bin"
    model.write_bytes(PAYLOAD)
    alias = tmp_path / "alias.bin"
    alias.hardlink_to(model)
    with pytest.raises(IntegrityError, match="hard links"):
        with verified_input(alias, DIGEST, len(PAYLOAD)):
            pytest.fail("hard link must not reach consumer")


@pytest.mark.parametrize("leaf", [False, True])
def test_real_junction_rejected(tmp_path: Path, leaf: bool) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "model.bin").write_bytes(PAYLOAD)
    junction = tmp_path / "junction"
    # cmd mklink is creation only; removal uses nonrecursive os.rmdir below.
    subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(junction), str(target)],
                   check=True, capture_output=True)
    try:
        with pytest.raises(IntegrityError, match="reparse"):
            with verified_input(junction if leaf else junction / "model.bin",
                                DIGEST, len(PAYLOAD)):
                pytest.fail("junction must not reach consumer")
    finally:
        os.rmdir(junction)
    assert (target / "model.bin").read_bytes() == PAYLOAD


@pytest.mark.parametrize("name", ["relative.bin", r"C:\model.bin:stream",
                                  r"\\server\share\model.bin", r"C:\folder\..\model.bin"])
def test_noncanonical_input_rejected(name: str) -> None:
    with pytest.raises(IntegrityError, match="canonical"):
        with verified_input(Path(name), DIGEST, len(PAYLOAD)):
            pytest.fail("noncanonical path must not reach consumer")
