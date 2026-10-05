"""Synthetic native consumer, with actual Windows sharing violations."""

import hashlib
import os
from collections.abc import Sequence
from pathlib import Path
from threading import Event, Thread

import pytest

from passivelistener import models, native_lifetime
from passivelistener.integrity import IntegrityError
from passivelistener.models import ModelPin
from passivelistener.native_lifetime import NativeLifetimeError, native_session

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows model leases")


@pytest.fixture
def inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    pins = {}
    for identity, filename in (("whisper-base", "ggml-base.bin"), ("silero-v6", "vad.onnx")):
        data = identity.encode()
        (tmp_path / filename).write_bytes(data)
        pins[identity] = ModelPin(
            filename, "synthetic", len(data), hashlib.sha256(data).hexdigest(),
        )
    monkeypatch.setattr(models, "MODEL_PINS", pins)
    monkeypatch.setattr(native_lifetime, "MODEL_PINS", pins)
    return tmp_path


class Consumer:
    def __init__(self, fail_open: bool = False, fail_close: bool = False) -> None:
        self.paths: tuple[Path, ...] = ()
        self.events: list[str] = []
        self.fail_open = fail_open
        self.fail_close = fail_close
        self.entered = Event()
        self.release = Event()
        self.release.set()

    def assert_locked(self) -> None:
        for path in self.paths:
            assert path.read_bytes()
            with pytest.raises(OSError):
                path.write_bytes(b"mutation")
            with pytest.raises(OSError):
                path.rename(path.with_suffix(".moved"))

    def open(self, whisper: Path, vad: Path) -> None:
        self.paths = (whisper, vad)
        self.events.append("open")
        self.assert_locked()
        if self.fail_open:
            raise RuntimeError("synthetic open failure")

    def transcribe(self, samples: Sequence[float]) -> str:
        self.events.append("infer")
        self.assert_locked()
        self.entered.set()
        assert self.release.wait(5)
        return "synthetic"

    def close(self) -> None:
        self.events.append("close")
        self.assert_locked()
        if self.fail_close:
            raise RuntimeError("sensitive backend diagnostic")


def test_locks_cover_open_inference_cleanup_and_then_release(inputs: Path) -> None:
    backend = Consumer()
    with native_session(inputs, backend) as session:
        assert session.transcribe([0.0]) == "synthetic"
        with pytest.raises(OSError):
            inputs.rename(inputs.with_name(inputs.name + "-moved"))
    assert backend.events == ["open", "infer", "close"]
    for path in backend.paths:
        path.unlink()
    with pytest.raises(NativeLifetimeError, match="closed"):
        session.transcribe([0.0])
    session.close()
    assert backend.events.count("close") == 1


def test_second_model_mismatch_never_initializes_native(inputs: Path) -> None:
    (inputs / "vad.onnx").write_bytes(b"bad")
    backend = Consumer()
    with pytest.raises(IntegrityError):
        with native_session(inputs, backend):
            pytest.fail("invalid model reached native code")
    assert backend.events == []
    (inputs / "ggml-base.bin").unlink()


def test_partial_initialization_cleanup_holds_both_leases(inputs: Path) -> None:
    backend = Consumer(fail_open=True)
    with pytest.raises(RuntimeError, match="open failure"):
        with native_session(inputs, backend):
            pytest.fail("open unexpectedly succeeded")
    assert backend.events == ["open", "close"]
    for path in backend.paths:
        path.unlink()


def test_body_failure_still_cleans_up(inputs: Path) -> None:
    backend = Consumer()
    with pytest.raises(ValueError, match="synthetic"):
        with native_session(inputs, backend):
            raise ValueError("synthetic")
    assert backend.events == ["open", "close"]
    for path in backend.paths:
        path.unlink()


def test_cleanup_failure_quarantines_until_process_exit(inputs: Path) -> None:
    backend = Consumer(fail_close=True)
    count = len(native_lifetime._QUARANTINE)
    try:
        with pytest.raises(NativeLifetimeError, match="restart worker") as error:
            with native_session(inputs, backend) as session:
                pass
        assert "sensitive" not in str(error.value)
        assert len(native_lifetime._QUARANTINE) == count + 1
        backend.assert_locked()
        session.close()
        assert backend.events.count("close") == 1
        with pytest.raises(NativeLifetimeError, match="closed"):
            session.transcribe([])
    finally:
        # Only this synthetic consumer has no native state; production must exit.
        if len(native_lifetime._QUARANTINE) > count:
            leases, _ = native_lifetime._QUARANTINE.pop()
            leases.close()


def test_close_waits_for_running_inference(inputs: Path) -> None:
    backend = Consumer()
    backend.release.clear()
    results: list[str] = []
    finished = Event()
    with native_session(inputs, backend) as session:
        inference = Thread(target=lambda: results.append(session.transcribe([0.0])))
        inference.start()
        assert backend.entered.wait(5)
        def close() -> None:
            session.close()
            finished.set()
        shutdown = Thread(target=close)
        shutdown.start()
        try:
            assert not finished.wait(0.05)
            assert "close" not in backend.events
        finally:
            backend.release.set()
            inference.join(5)
            shutdown.join(5)
        assert not inference.is_alive() and not shutdown.is_alive()
        assert finished.is_set()
    assert results == ["synthetic"]
    assert backend.events == ["open", "infer", "close"]


def test_partial_open_and_cleanup_failure_retains_both_models(inputs: Path) -> None:
    backend = Consumer(fail_open=True, fail_close=True)
    count = len(native_lifetime._QUARANTINE)
    try:
        with pytest.raises(NativeLifetimeError, match="restart worker"):
            with native_session(inputs, backend):
                pytest.fail("partial initialization yielded a session")
        assert backend.events == ["open", "close"]
        assert len(native_lifetime._QUARANTINE) == count + 1
        backend.assert_locked()
        with pytest.raises(OSError):
            inputs.rename(inputs.with_name(inputs.name + "-moved"))
    finally:
        if len(native_lifetime._QUARANTINE) > count:
            leases, _ = native_lifetime._QUARANTINE.pop()
            leases.close()


def test_inference_error_releases_lock_and_allows_cleanup(inputs: Path) -> None:
    class FailingConsumer(Consumer):
        def transcribe(self, samples: Sequence[float]) -> str:
            self.assert_locked()
            raise RuntimeError("synthetic inference failure")

    backend = FailingConsumer()
    with pytest.raises(RuntimeError, match="inference failure"):
        with native_session(inputs, backend) as session:
            session.transcribe([])
    assert backend.events == ["open", "close"]
    for path in backend.paths:
        path.unlink()
