"""Actual Windows filesystem operations using only generated fixture bytes."""

import ctypes
import hashlib
import os
import subprocess
from ctypes import wintypes
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


@pytest.mark.parametrize("close_mapping_handle", [False, True])
def test_writable_view_without_file_handle_rejected(
    tmp_path: Path, close_mapping_handle: bool,
) -> None:
    """Exercise a surviving native view, without Python mmap's duplicate handle."""
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileMappingW.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                        wintypes.DWORD, wintypes.DWORD,
                                        wintypes.DWORD, wintypes.LPCWSTR]
    kernel.CreateFileMappingW.restype = wintypes.HANDLE
    kernel.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                   wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
    kernel.MapViewOfFile.restype = ctypes.c_void_p
    kernel.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    kernel.UnmapViewOfFile.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL

    folder = tmp_path / "models"
    folder.mkdir()
    model = folder / "model.bin"
    model.write_bytes(PAYLOAD)
    mapping = None
    view = None
    try:
        with model.open("r+b") as original:
            mapping = kernel.CreateFileMappingW(
                msvcrt.get_osfhandle(original.fileno()), None, 0x04, 0, 0, None,
            )  # PAGE_READWRITE
            assert mapping, ctypes.get_last_error()
            view = kernel.MapViewOfFile(mapping, 0x02, 0, 0, 0)  # FILE_MAP_WRITE
            assert view, ctypes.get_last_error()
        assert original.closed
        if close_mapping_handle:
            assert kernel.CloseHandle(mapping)
            mapping = None  # The view alone must retain the write conflict.
        assert ctypes.string_at(view, len(PAYLOAD)) == PAYLOAD
        with pytest.raises(IntegrityError, match="locked"):
            with verified_input(model, DIGEST, len(PAYLOAD)):
                pytest.fail("surviving writable view must prevent lease")
    finally:
        if view:
            assert kernel.UnmapViewOfFile(view)
        if mapping:
            assert kernel.CloseHandle(mapping)

    # A rejected acquisition must not leak ancestor handles; retry must work.
    moved = tmp_path / "moved"
    folder.rename(moved)
    model = moved / "model.bin"
    with verified_input(model, DIGEST, len(PAYLOAD)) as stream:
        assert stream.read() == PAYLOAD
    model.unlink()
    moved.rmdir()


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
