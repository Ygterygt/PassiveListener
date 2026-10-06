import json
import mmap
import os
import zipfile
from pathlib import Path

import pytest

from passivelistener.archive import AGE_NS, InjectedArchiveFault, archive_transcripts
from passivelistener.archive_driver import run_request
from passivelistener.storage_handles import archive_lock, directory_lease, source_lease

pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows handles required")
NOW = 1791136800000000000


def fixture(root: Path, name: str = "old.txt", age: int = AGE_NS + 100) -> Path:
    path = root / name
    path.write_text("synthetic Turkish: merhaba dünya", encoding="utf-8")
    os.utime(path, ns=(NOW - age, NOW - age))
    return path


def contents(root: Path) -> dict[str, bytes]:
    result = {}
    for path in root.glob("*.zip"):
        with zipfile.ZipFile(path) as archive:
            assert archive.testzip() is None
            for member in archive.namelist():
                assert member not in result
                result[member] = archive.read(member)
    return result


def test_boundary_and_repeat(tmp_path: Path) -> None:
    old = fixture(tmp_path)
    expected = old.read_bytes()
    exact = fixture(tmp_path, "exact.txt", AGE_NS)
    young = fixture(tmp_path, "young.txt", AGE_NS - 100)
    assert exact.stat().st_mtime_ns == NOW - AGE_NS
    result = archive_transcripts(tmp_path, now_ns=NOW)
    assert (result.archived, result.recent, result.deferred) == (1, 2, 0)
    assert not old.exists() and exact.exists() and young.exists()
    assert contents(tmp_path) == {"old.txt": expected}
    assert archive_transcripts(tmp_path, now_ns=NOW).archived == 0
    assert contents(tmp_path) == {"old.txt": expected}


def test_active_locked_partial_and_hardlink(tmp_path: Path) -> None:
    active = fixture(tmp_path, "active.txt")
    locked = fixture(tmp_path, "locked.txt")
    partial = fixture(tmp_path, "writing.partial")
    linked = fixture(tmp_path, "linked.txt")
    os.link(linked, tmp_path / "alias.bin")
    with source_lease(locked):
        result = archive_transcripts(tmp_path, now_ns=NOW, active=frozenset({active.name}))
    assert (result.archived, result.active, result.deferred) == (0, 1, 2)
    assert all(p.exists() for p in (active, locked, partial, linked))
    assert not contents(tmp_path)


@pytest.mark.parametrize("stage", ["write", "verify", "before_delete", "after_first_commit"])
def test_failure_retry(tmp_path: Path, stage: str) -> None:
    old = fixture(tmp_path)
    other = fixture(tmp_path, "second.txt")
    expected = {p.name: p.read_bytes() for p in (old, other)}

    def fail(point: str) -> None:
        if point == stage:
            raise InjectedArchiveFault("synthetic fault")

    result = archive_transcripts(tmp_path, now_ns=NOW, fault=fail)
    assert result.deferred == 1
    assert len(list(tmp_path.glob("*.txt"))) == (1 if stage == "after_first_commit" else 2)
    assert len(list(tmp_path.glob("*.zip"))) == (1 if stage in (
        "before_delete", "after_first_commit") else 0)
    assert not list(tmp_path.glob("*.partial"))
    archive_transcripts(tmp_path, now_ns=NOW)
    archive_transcripts(tmp_path, now_ns=NOW)
    assert not list(tmp_path.glob("*.txt"))
    assert contents(tmp_path) == expected


def test_corrupt_published_archive_retains_source(tmp_path: Path) -> None:
    old = fixture(tmp_path)

    def fail(stage: str) -> None:
        if stage == "before_delete":
            raise InjectedArchiveFault("synthetic fault")

    archive_transcripts(tmp_path, now_ns=NOW, fault=fail)
    next(tmp_path.glob("*.zip")).write_bytes(b"corrupted synthetic archive")
    assert archive_transcripts(tmp_path, now_ns=NOW).deferred == 1
    assert old.exists()


def test_concurrent_archiver_fails_without_source_change(tmp_path: Path) -> None:
    old = fixture(tmp_path)
    with directory_lease(tmp_path), archive_lock(tmp_path), pytest.raises(OSError):
        archive_transcripts(tmp_path, now_ns=NOW)
    assert old.exists() and not contents(tmp_path)


@pytest.mark.parametrize("active", ["../old.txt", "x:y", "x\\y", "trailing."])
def test_reject_unsafe_active(tmp_path: Path, active: str) -> None:
    fixture(tmp_path)
    with pytest.raises(ValueError):
        archive_transcripts(tmp_path, now_ns=NOW, active=frozenset({active}))


def test_driver_runs_production_and_validates_schema(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    fixture(root)
    request = tmp_path / "request.json"
    body = dict(schema=1, operation="archive", root=str(root), now_ns=NOW, active=[], fault=None)
    request.write_text(json.dumps(body), encoding="utf-8")
    assert run_request(request)["archived"] == 1
    body["operation"] = "delete"
    request.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError):
        run_request(request)


def test_surviving_writable_mapping_cannot_lose_source(tmp_path: Path) -> None:
    old = fixture(tmp_path)
    with old.open("r+b") as writable:
        view = mmap.mmap(writable.fileno(), 0, access=mmap.ACCESS_WRITE)
    try:
        def change(stage: str) -> None:
            if stage == "before_delete":
                view[0:1] = b"X"
                view.flush()

        result = archive_transcripts(tmp_path, now_ns=NOW, fault=change)
        assert result.deferred == 1
        assert old.exists()
    finally:
        view.close()


def test_extended_path_without_machine_policy(tmp_path: Path) -> None:
    # Fixed name components, > MAX_PATH overall. No global long-path setting changes.
    root = Path("\\\\?\\" + str(tmp_path)) / ("a" * 80) / ("b" * 80)
    root.mkdir(parents=True)
    fixture(root)
    canonical = Path(str(root)[4:])
    assert archive_transcripts(canonical, now_ns=NOW).archived == 1
    assert len(contents(root)) == 1


def test_changed_finalized_source_is_retained_without_duplicate(tmp_path: Path) -> None:
    old = fixture(tmp_path)

    def fail(stage: str) -> None:
        if stage == "before_delete":
            raise InjectedArchiveFault("synthetic fault")

    archive_transcripts(tmp_path, now_ns=NOW, fault=fail)
    old.write_bytes(b"synthetic unexpected revision")
    os.utime(old, ns=(NOW - AGE_NS - 1000000000, NOW - AGE_NS - 1000000000))
    assert archive_transcripts(tmp_path, now_ns=NOW).deferred == 1
    assert old.exists()
    assert len(list(tmp_path.glob("*.zip"))) == 1
