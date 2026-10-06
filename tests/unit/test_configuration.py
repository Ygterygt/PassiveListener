import json
import os
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from passivelistener.configuration import (
    MAX_CONFIG_BYTES,
    Configuration,
    initialize_configuration,
    load_configuration,
    parse_configuration,
)
from passivelistener.private_storage import private_directory
from passivelistener.provisioning import provision_output


def test_defaults_and_turkish_settings() -> None:
    assert parse_configuration(b"{}") == Configuration()
    assert parse_configuration(b'{"microphone":2,"segment_seconds":0.5}').microphone == 2


@pytest.mark.parametrize("data", [
    b'[]', b'{"extra":1}', b'{"language":"en"}', b'{"model":"arbitrary"}',
    b'{"tts":true}', b'{"prompt_routing":true}', b'{"wake_word":true}',
    b'{"tts":0}', b'{"microphone":true}', b'{"microphone":-1}',
    b'{"vad_threshold":NaN}', b'{"segment_seconds":Infinity}',
    b'{"segment_seconds":31}', b'{"vad_threshold":false}',
    b'{"vad_threshold":1.1}', b'{"language":"tr","language":"en"}',
    b'\xff', b' ' * (MAX_CONFIG_BYTES + 1), b'{',
])
def test_invalid_configuration(data: bytes) -> None:
    with pytest.raises(ValueError):
        parse_configuration(data)


@pytest.mark.parametrize("path", [
    "relative", "C:relative", "\\\\server\\share", "C:\\x\\..\\y", "C:\\x\\.\\y",
    "C:\\x\\y.", "C:\\x\\y ", "C:\\x:ads", "C:\\NUL", "C:\\x\\CON.txt",
    "C:\\x\\\\y", "C:/x", "C:\\", "C:\\x\x00",
])
def test_reject_paths_before_normalization(path: str) -> None:
    with pytest.raises(ValueError):
        parse_configuration(json.dumps({"output_directory": path}).encode())


@pytest.mark.skipif(os.name != "nt", reason="Windows native leases")
class TestNativeConfiguration:
    def test_idempotent_and_no_overwrite(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "config"
            initialize_configuration(root)
            before = (root / "settings.json").read_bytes()
            initialize_configuration(root)
            assert load_configuration(root) == Configuration()
            with pytest.raises(ValueError):
                initialize_configuration(root, replace(Configuration(), microphone=1))
            assert (root / "settings.json").read_bytes() == before

    def test_failed_flush_never_publishes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "config"
            with patch("passivelistener.configuration.os.fsync", side_effect=OSError):
                with pytest.raises(OSError):
                    initialize_configuration(root)
            assert not (root / "settings.json").exists()
            assert len(list(root.glob(".settings-*.partial"))) == 1
            initialize_configuration(root)
            assert load_configuration(root) == Configuration()

    def test_untrusted_existing_config_directory_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            with pytest.raises(OSError):
                initialize_configuration(Path(directory))
            assert list(Path(directory).iterdir()) == []

    def test_nested_private_provisioning(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "new" / "nested" / "transcripts"
            provision_output(root)
            provision_output(root)
            for folder in (root.parent.parent, root.parent, root):
                with private_directory(folder):
                    pass

    def test_existing_public_leaf_is_not_repaired(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "public"
            root.mkdir()
            (root / "sentinel").write_bytes(b"synthetic")
            with pytest.raises(OSError):
                provision_output(root)
            assert (root / "sentinel").read_bytes() == b"synthetic"

    def test_configuration_hardlink_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "config"
            initialize_configuration(root)
            os.link(root / "settings.json", root / "alias")
            with pytest.raises(OSError):
                load_configuration(root)

    def test_oversized_read_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "config"
            initialize_configuration(root)
            (root / "settings.json").write_bytes(b" " * (MAX_CONFIG_BYTES + 1))
            with pytest.raises(ValueError):
                load_configuration(root)

    def test_file_ancestor_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            parent = Path(directory) / "file"
            parent.write_bytes(b"synthetic")
            with pytest.raises(OSError):
                provision_output(parent / "transcripts")
            assert parent.read_bytes() == b"synthetic"

    def test_cli_provisions_only_selected_synthetic_output(self, capsys: object) -> None:
        from passivelistener.cli import main

        with TemporaryDirectory() as directory:
            root = Path(directory) / "config"
            output = Path(directory) / "synthetic" / "transcripts"
            initialize_configuration(root, replace(Configuration(), output_directory=str(output)))
            for command in ("validate-config", "provision-output", "provision-output"):
                with patch("sys.argv", ["PassiveListener", command, str(root)]):
                    assert main() == 0
            with private_directory(output):
                assert list(output.iterdir()) == []
            (root / "settings.json").write_bytes(b'{"tts":true}')
            with patch("sys.argv", ["PassiveListener", "validate-config", str(root)]):
                assert main() == 2


def test_schema_defaults_match_runtime() -> None:
    import re

    schema = json.loads((Path(__file__).parents[2] / "config/settings.schema.json").read_text())
    defaults = {key: item["default"] for key, item in schema["properties"].items()}
    assert parse_configuration(json.dumps(defaults).encode()) == Configuration()
    for key in ("model_directory", "output_directory"):
        assert re.match(schema["properties"][key]["pattern"], defaults[key])


@pytest.mark.skipif(os.name != "nt", reason="Windows native leases")
def test_concurrent_configuration_publish_never_replaces() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory) / "config"
        original_rename = Path.rename
        competing = b'{"microphone":7}'

        def competing_publish(source: Path, target: Path) -> Path:
            target.write_bytes(competing)
            return original_rename(source, target)

        with patch.object(Path, "rename", competing_publish):
            with pytest.raises(FileExistsError):
                initialize_configuration(root)
        assert (root / "settings.json").read_bytes() == competing
        assert load_configuration(root).microphone == 7


@pytest.mark.skipif(os.name != "nt", reason="Windows native leases")
def test_configuration_read_rejects_open_writer() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory) / "config"
        initialize_configuration(root)
        with (root / "settings.json").open("r+b"):
            with pytest.raises(OSError):
                load_configuration(root)
        assert load_configuration(root) == Configuration()
