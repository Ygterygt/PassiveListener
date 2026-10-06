"""Bounded, strict user configuration; no external routing or executable fields."""

import json
import math
import os
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from passivelistener.private_storage import private_directory
from passivelistener.storage_handles import source_lease

MAX_CONFIG_BYTES = 16384


def local_path(value: object) -> str:
    if not isinstance(value, str) or not re.match(r"^[A-Za-z]:\\", value):
        raise ValueError("absolute local path required")
    path = PureWindowsPath(value)
    # Check raw components before pathlib can normalize dots or repeated separators.
    parts = value[3:].split("\\")
    if (not parts or any(not part or part in (".", "..")
                        or part.endswith((" ", ".")) or PureWindowsPath(part).is_reserved()
                        or any(c in part for c in '/:<>"|?*')
                        or any(ord(c) < 32 for c in part) for part in parts)
            or str(path) != value):
        raise ValueError("canonical local path required")
    return value


@dataclass(frozen=True)
class Configuration:
    microphone: int | None = None
    model: str = "whisper-base"
    model_directory: str = r"C:\Program Files\PassiveListener\models"
    language: str = "tr"
    vad_threshold: float = 0.5
    segment_seconds: float = 10.0
    output_directory: str = r"C:\temp\LiveTranskripts"
    prompt_routing: bool = False
    tts: bool = False
    wake_word: bool = False


def parse_configuration(data: bytes) -> Configuration:
    if not data or len(data) > MAX_CONFIG_BYTES:
        raise ValueError("configuration size rejected")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate configuration field")
            result[key] = value
        return result

    try:
        values = json.loads(data.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, RecursionError) as error:
        raise ValueError("configuration encoding or depth rejected") from error
    defaults = asdict(Configuration())
    if not isinstance(values, dict) or set(values) - set(defaults):
        raise ValueError("configuration fields rejected")
    defaults.update(values)
    mic = defaults["microphone"]
    if mic is not None and (type(mic) is not int or not 0 <= mic <= 65535):
        raise ValueError("microphone rejected")
    if defaults["model"] != "whisper-base" or defaults["language"] != "tr":
        raise ValueError("unsupported model or language")
    for field, low, high in (("vad_threshold", 0.0, 1.0), ("segment_seconds", 0.5, 30.0)):
        value = defaults[field]
        if (type(value) not in (int, float) or not low <= value <= high
                or not math.isfinite(value)):
            raise ValueError("configuration number rejected")
    for field in ("prompt_routing", "tts", "wake_word"):
        if defaults[field] is not False:
            raise ValueError("inactive integration must remain disabled")
    for field in ("model_directory", "output_directory"):
        defaults[field] = local_path(defaults[field])
    return Configuration(**defaults)


def load_configuration(root: Path) -> Configuration:
    with private_directory(root), source_lease(root / "settings.json") as stream:
        return parse_configuration(stream.read(MAX_CONFIG_BYTES + 1))


def initialize_configuration(root: Path, config: Configuration | None = None) -> None:
    """Publish once, flush before rename, never replace existing user settings.

    Orphan .settings-*.partial files are retained for offline diagnosis. Existing
    settings must validate and equal the request for initialization to succeed.
    """
    config = Configuration() if config is None else config
    data = (json.dumps(asdict(config), sort_keys=True, indent=2) + "\n").encode("utf-8")
    parse_configuration(data)
    with private_directory(root, create=True):
        native = Path("\\\\?\\" + str(root))
        target = native / "settings.json"
        if target.exists():
            if load_configuration(root) != config:
                raise ValueError("existing configuration differs")
            return
        partial = native / f".settings-{uuid.uuid4().hex}.partial"
        with partial.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        partial.rename(target)

