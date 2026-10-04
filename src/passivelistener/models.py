"""Accepted model identities compiled into the application, never user overrides.

Protect the installed code/EXE with installer ACLs. A hash table in an application
that an attacker can replace is not a trust anchor against that attacker.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO, Final

from passivelistener.integrity import IntegrityError
from passivelistener.windows_input import verified_input


@dataclass(frozen=True)
class ModelPin:
    filename: str
    engine: str
    size: int
    sha256: str


MODEL_PINS: Final[Mapping[str, ModelPin]] = MappingProxyType({
    "whisper-base": ModelPin(
        "ggml-base.bin", "whisper.cpp v1.7.6", 147951465,
        "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
    ),
    "silero-v6": ModelPin(
        "silero_vad.onnx", "Silero v6.0", 2327524,
        "597d30b3ec076608d059477bb14cfeffdf951bf5cae370d38f65d33bbfe82004",
    ),
})


@contextmanager
def verified_model(model_id: str, directory: Path) -> Iterator[BinaryIO]:
    """Hold accepted bytes until context exit; never load an external manifest.

    The directory must be an absolute local path. No automatic download, fallback
    model, hash override or path resolution is performed. Protected staging and
    a native consumer retaining this context are separate requirements.
    """
    try:
        pin = MODEL_PINS[model_id]
    except KeyError:
        raise IntegrityError("unapproved model identity") from None
    with verified_input(directory / pin.filename, pin.sha256, pin.size) as stream:
        yield stream
