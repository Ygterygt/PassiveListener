"""Local QA request decoder; the actual operation lives in archive.py."""

import json
from dataclasses import asdict
from pathlib import Path

from passivelistener.archive import InjectedArchiveFault, archive_transcripts


def run_request(path: Path) -> dict[str, int]:
    if path.stat().st_size > 65536:
        raise ValueError("QA request too large")
    request = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(request, dict) or set(request) != {
        "schema", "operation", "root", "now_ns", "active", "fault"
    }:
        raise ValueError("invalid QA schema")
    if (type(request["schema"]) is not int or request["schema"] != 1
            or request["operation"] != "archive" or not isinstance(request["root"], str)
            or type(request["now_ns"]) is not int
            or not isinstance(request["active"], list)
            or not all(isinstance(name, str) for name in request["active"])
            or request["fault"] not in (None, "write", "verify", "after_first_commit")):
        raise ValueError("invalid QA fields")

    def inject(stage: str) -> None:
        if request["fault"] == stage:
            raise InjectedArchiveFault("local synthetic fault")

    result = archive_transcripts(Path(request["root"]), now_ns=request["now_ns"],
                                 active=frozenset(request["active"]), fault=inject)
    return asdict(result)
