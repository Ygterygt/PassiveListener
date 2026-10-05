"""Immutable UTF-8 transcript publication in the current user's private directory."""

import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from passivelistener.private_storage import private_directory

MAX_SEGMENT_BYTES = 1024 * 1024


def persist_segment(root: Path, text: str, *, fault: Callable[[str], None] = lambda _: None
                    ) -> Path:
    """Flush a complete segment before atomic, non-replacing Windows publication.

    A crash/fault leaves a .partial for explicit operator recovery; it is never
    automatically published, deleted, or archived. No transcript content is logged.
    Caller must bound segment duration; this function also bounds encoded size.
    """
    data = text.encode("utf-8", errors="strict")
    if not data or len(data) > MAX_SEGMENT_BYTES:
        raise ValueError("invalid transcript segment size")
    with private_directory(root, create=True):
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        name = f"transcript-{stamp}-{uuid.uuid4().hex}"
        native = Path("\\\\?\\" + str(root))
        partial = native / f"{name}.partial"
        target = native / f"{name}.txt"
        with partial.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            fault("after_flush")
        fault("before_publish")
        # Windows rename fails on collision; do not replace an existing transcript.
        partial.rename(target)
        return root / target.name


def orphan_count(root: Path) -> int:
    """Report unresolved partial files without interpreting their contents.

    This is a health indication, not proof that a partial is inactive. Recovery
    must be performed offline with capture stopped; no age-based deletion policy.
    """
    with private_directory(root):
        return sum(1 for _ in Path("\\\\?\\" + str(root)).glob("transcript-*.partial"))
