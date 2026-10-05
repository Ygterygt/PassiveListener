"""Offline, single-source ZIP transactions with retry reconciliation.

Only finalized *.txt files are eligible. Writers must use unique immutable names
and .partial files until finalization. Output ACL provisioning belongs to installer.
"""

import hashlib
import os
import shutil
import time
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from passivelistener.storage_handles import (
    archive_lock,
    delete_held_source,
    directory_lease,
    source_lease,
)

AGE_NS = 48 * 3600 * 1_000_000_000
CHUNK = 1024 * 1024
Fault = Callable[[str], None]


class InjectedArchiveFault(OSError):
    """Explicit local test fault; stop this invocation after preserving sources."""


@dataclass
class ArchiveResult:
    archived: int = 0
    active: int = 0
    recent: int = 0
    deferred: int = 0


def safe_basename(name: str) -> bool:
    return (bool(name) and name not in (".", "..") and not name.endswith((" ", "."))
            and not any(c in name for c in '/\\:<>"|?*\x00')
            and all(ord(c) >= 32 for c in name))


def _digest(stream: IO[bytes]) -> str:
    digest = hashlib.sha256()
    while block := stream.read(CHUNK):
        digest.update(block)
    return digest.hexdigest()


def _verify(path: Path, name: str, digest: str, size: int) -> None:
    with source_lease(path) as stream, zipfile.ZipFile(stream) as archive:
        entries = archive.infolist()
        if len(entries) != 1 or entries[0].filename != name or entries[0].file_size != size:
            raise OSError("archive manifest rejected")
        with archive.open(entries[0]) as member:
            # Reading to EOF also validates the ZIP CRC.
            if _digest(member) != digest:
                raise OSError("archive bytes rejected")


def _one(root: Path, path: Path, now_ns: int, fault: Fault) -> bool:
    with source_lease(path, delete=True) as source:
        metadata = os.fstat(source.fileno())
        if metadata.st_mtime_ns >= now_ns - AGE_NS:
            return False
        digest = _digest(source)
        source.seek(0)
        stamp = datetime.fromtimestamp(metadata.st_mtime_ns // 1_000_000_000, UTC)
        identity = hashlib.sha256(path.name.encode("utf-8")).hexdigest()
        previous = list(root.glob(f"archive-*-{identity}.zip"))
        if len(previous) > 1:
            raise OSError("duplicate archive identity requires recovery")
        # Immutable source names define identity. If a finalized source was later changed,
        # retain it for recovery rather than creating a second archive of the same member.
        target = (previous[0] if previous else
                  root / f"archive-{stamp:%Y%m%dT%H%M%SZ}-{identity}.zip")
        temporary = root / f".archive-{uuid.uuid4().hex}.partial"
        try:
            if not target.exists():
                fault("write")
                with temporary.open("xb") as output:
                    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                        with archive.open(path.name, "w", force_zip64=True) as member:
                            shutil.copyfileobj(source, member, CHUNK)
                    output.flush()
                    os.fsync(output.fileno())
                fault("verify")
                _verify(temporary, path.name, digest, metadata.st_size)
                # Windows rename does not replace a destination. A collision fails closed.
                temporary.rename(target)
            # Reconcile a crash between publish and delete by verifying the existing ZIP.
            # Hold the published archive through source deletion to prevent substitution.
            with source_lease(target) as published:
                with zipfile.ZipFile(published) as archive:
                    entries = archive.infolist()
                    if (len(entries) != 1 or entries[0].filename != path.name
                            or entries[0].file_size != metadata.st_size):
                        raise OSError("published archive rejected")
                    with archive.open(entries[0]) as member:
                        if _digest(member) != digest:
                            raise OSError("published archive bytes rejected")
                fault("before_delete")
                delete_held_source(source)
        finally:
            temporary.unlink(missing_ok=True)
    return True


def archive_transcripts(root: Path, *, now_ns: int | None = None,
                        active: frozenset[str] = frozenset(),
                        fault: Fault = lambda _: None) -> ArchiveResult:
    """Use an injected clock/fault callback only in local deterministic tests.

    No contents, paths or exception text are logged. Deferred counts signal retry.
    Root must already exist and must have the configured user's private ACL.
    """
    if any(not safe_basename(name) for name in active):
        raise ValueError("active entries must be basenames")
    instant = time.time_ns() if now_ns is None else now_ns
    if type(instant) is not int or instant < AGE_NS:
        raise ValueError("invalid archive clock")
    result = ArchiveResult()
    with directory_lease(root), archive_lock(root):
        # Native extended paths avoid dependence on a machine-wide long-path policy.
        root = Path("\\\\?\\" + str(root))
        for path in sorted(root.glob("*.txt")):
            if path.name in active:
                result.active += 1
                continue
            if not safe_basename(path.name):
                result.deferred += 1
                continue
            try:
                if _one(root, path, instant, fault):
                    result.archived += 1
                    fault("after_first_commit")
                else:
                    result.recent += 1
            except InjectedArchiveFault:
                result.deferred += 1
                break
            except (OSError, ValueError, zipfile.BadZipFile):
                result.deferred += 1
    return result
