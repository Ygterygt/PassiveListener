"""Manifest boundary checks use synthetic bytes; no models or audio are fetched."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from passivelistener.integrity import IntegrityError
from passivelistener.models import MODEL_PINS, verified_model


def test_research_inventory_matches_compiled_pins() -> None:
    inventory = json.loads(Path("config/models.json").read_text(encoding="utf-8"))
    expected = {(p.filename, p.engine, p.size, p.sha256) for p in MODEL_PINS.values()}
    observed = {(p["name"], p["engine"], p["size"], p["sha256"])
                for p in inventory["artifacts"]}
    assert observed == expected


@pytest.mark.parametrize("model_id", ["", "base.en", "../ggml-base.bin", "WHISPER-BASE"])
def test_unapproved_identity_rejected_before_open(tmp_path: Path, model_id: str) -> None:
    with pytest.raises(IntegrityError, match="identity"):
        with verified_model(model_id, tmp_path / "absent"):
            pytest.fail("unapproved model reached consumer")


@pytest.mark.skipif(os.name != "nt", reason="Windows input lease")
@pytest.mark.parametrize("model_id", list(MODEL_PINS))
def test_substituted_model_rejected_and_handles_released(tmp_path: Path, model_id: str) -> None:
    model = tmp_path / MODEL_PINS[model_id].filename
    model.write_bytes(b"synthetic invalid model")
    with pytest.raises(IntegrityError, match="mismatch"):
        with verified_model(model_id, tmp_path):
            pytest.fail("substituted bytes reached consumer")
    model.unlink()


@pytest.mark.skipif(os.name != "nt", reason="Windows input lease")
def test_optimized_cli_ignores_external_manifest(tmp_path: Path) -> None:
    (tmp_path / "ggml-base.bin").write_bytes(b"synthetic invalid model")
    # A caller cannot replace the compiled pins using a same-named local file.
    (tmp_path / "models.json").write_text('{"artifacts": []}', encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-O", "-m", "passivelistener.cli", "verify-model",
         "whisper-base", str(tmp_path)],
        env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert result.stderr.strip() == "artifact verification failed"
    assert str(tmp_path) not in result.stderr
