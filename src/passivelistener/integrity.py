"""Explicit integrity checks, including when Python optimization is enabled."""

import hashlib
import hmac
import re
from pathlib import Path


class IntegrityError(ValueError):
    """Artifact is absent, invalid, or differs from its approved manifest."""


def verify_file(path: Path, expected_sha256: str, expected_size: int) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or expected_size <= 0:
        raise IntegrityError("invalid artifact manifest")
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise IntegrityError("artifact must be a regular local file")
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(block)
                digest.update(block)
    except OSError as exc:
        raise IntegrityError("artifact could not be read") from exc
    if size != expected_size or not hmac.compare_digest(digest.hexdigest(), expected_sha256):
        raise IntegrityError("artifact integrity mismatch")
