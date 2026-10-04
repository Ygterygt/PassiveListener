"""Explicit integrity checks, including when Python optimization is enabled."""

import hashlib
import hmac
import re
from pathlib import Path
from typing import BinaryIO


class IntegrityError(ValueError):
    """Artifact is absent, invalid, or differs from its approved manifest."""


def verify_file(path: Path, expected_sha256: str, expected_size: int) -> None:
    """Point-in-time diagnostic only; use a held lease for model consumption."""
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or expected_size <= 0:
        raise IntegrityError("invalid artifact manifest")
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise IntegrityError("artifact must be a regular local file")
    try:
        with path.open("rb") as stream:
            verify_stream(stream, expected_sha256, expected_size)
    except OSError as exc:
        raise IntegrityError("artifact could not be read") from exc


def verify_stream(stream: BinaryIO, expected_sha256: str, expected_size: int) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or expected_size <= 0:
        raise IntegrityError("invalid artifact manifest")
    digest = hashlib.sha256()
    size = 0
    try:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            if size > expected_size:
                raise IntegrityError("artifact integrity mismatch")
            digest.update(block)
    except OSError as exc:
        raise IntegrityError("artifact could not be read") from exc
    if size != expected_size or not hmac.compare_digest(digest.hexdigest(), expected_sha256):
        raise IntegrityError("artifact integrity mismatch")
