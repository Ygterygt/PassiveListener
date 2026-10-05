import hashlib
import multiprocessing as mp
import os
import threading
import time
from functools import partial
from multiprocessing.synchronize import Event
from pathlib import Path

import pytest

from passivelistener.supervisor import SupervisorError, WorkerResult, WorkerSupervisor


def cooperative(stop: Event) -> None:
    stop.wait()


def failing(stop: Event) -> None:
    print("SYNTHETIC_PRIVATE_DIAGNOSTIC", flush=True)
    os.write(2, b"SYNTHETIC_PRIVATE_DIAGNOSTIC")
    raise RuntimeError("SYNTHETIC_PRIVATE_DIAGNOSTIC")


def hanging(ready: Event, stop: Event) -> None:
    ready.set()
    threading.Event().wait()


def stuck_finalizer(stop: Event) -> None:
    threading.Thread(target=threading.Event().wait).start()
    raise RuntimeError("synthetic cleanup failure")


def explicit_flush(path: Path, stop: Event) -> None:
    stop.wait()
    with path.open("wb") as stream:
        stream.write(b"synthetic fixture only")
        stream.flush()
        os.fsync(stream.fileno())


@pytest.mark.parametrize("worker,expected", [
    (cooperative, WorkerResult.STOPPED),
    (failing, WorkerResult.FAILED),
    (stuck_finalizer, WorkerResult.FAILED),
])
def test_real_spawn_exit_and_no_restart(worker, expected, capfd):
    host = WorkerSupervisor(worker)
    host.start()
    try:
        assert host.stop() == expected
        assert host.stop() == expected
        with pytest.raises(SupervisorError, match="already attempted"):
            host.start()
        assert "SYNTHETIC_PRIVATE_DIAGNOSTIC" not in str(capfd.readouterr())
    finally:
        host.stop()


def test_hung_worker_is_reaped_with_bounded_stop():
    ready = mp.get_context("spawn").Event()
    host = WorkerSupervisor(partial(hanging, ready), grace_seconds=0.1, kill_seconds=2)
    host.start()
    try:
        assert ready.wait(5), "child never entered simulated native call"
        started = time.monotonic()
        assert host.stop() == WorkerResult.TERMINATED
        assert time.monotonic() - started < 4
        assert host.stop() == WorkerResult.TERMINATED
    finally:
        host.stop()


def test_graceful_stop_allows_explicit_persistence(tmp_path):
    path = tmp_path / "synthetic.txt"
    host = WorkerSupervisor(partial(explicit_flush, path))
    host.start()
    try:
        assert host.stop() == WorkerResult.STOPPED
        assert path.read_bytes() == b"synthetic fixture only"
    finally:
        host.stop()


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True, 301])
def test_invalid_timeout(value):
    with pytest.raises(ValueError, match="timeout rejected"):
        WorkerSupervisor(cooperative, grace_seconds=value)
    with pytest.raises(ValueError, match="timeout rejected"):
        WorkerSupervisor(cooperative, kill_seconds=value)


def test_stop_without_start():
    host = WorkerSupervisor(cooperative)
    with pytest.raises(SupervisorError, match="not started"):
        host.stop()


def test_termination_failure_retains_ownership(monkeypatch):
    ready = mp.get_context("spawn").Event()
    host = WorkerSupervisor(partial(hanging, ready), grace_seconds=0.1, kill_seconds=2)
    host.start()
    try:
        assert ready.wait(5)

        def denied():
            raise OSError("SYNTHETIC_PRIVATE_DIAGNOSTIC")

        with monkeypatch.context() as patch:
            patch.setattr(host._process, "terminate", denied)
            with pytest.raises(SupervisorError, match="termination unconfirmed") as error:
                host.stop()
            assert error.value.__suppress_context__
            assert host._process.is_alive()
            with pytest.raises(SupervisorError, match="already attempted"):
                host.start()
        assert host.stop() == WorkerResult.TERMINATED
    finally:
        host.stop()


def quarantined_worker(directory: Path, ready: Event, stop: Event) -> None:
    from passivelistener import models, native_lifetime
    from passivelistener.models import ModelPin

    pins = {}
    for identity in ("whisper-base", "silero-v6"):
        data = identity.encode()
        pins[identity] = ModelPin(
            identity, "synthetic", len(data), hashlib.sha256(data).hexdigest(),
        )
    # Test-only identities installed inside the spawn child, never production overrides.
    models.MODEL_PINS = pins
    native_lifetime.MODEL_PINS = pins

    class FailingCleanup:
        def open(self, whisper, vad):
            ready.set()

        def transcribe(self, samples):
            return "synthetic"

        def close(self):
            raise RuntimeError("SYNTHETIC_PRIVATE_DIAGNOSTIC")

    with native_lifetime.native_session(directory, FailingCleanup()):
        stop.wait()


@pytest.mark.skipif(os.name != "nt", reason="Windows native lease integration")
def test_quarantined_model_handles_released_only_by_child_exit(tmp_path):
    for identity in ("whisper-base", "silero-v6"):
        (tmp_path / identity).write_bytes(identity.encode())
    ready = mp.get_context("spawn").Event()
    host = WorkerSupervisor(partial(quarantined_worker, tmp_path, ready))
    host.start()
    try:
        assert ready.wait(5)
        for path in tmp_path.iterdir():
            with pytest.raises(OSError):
                path.write_bytes(b"mutation")
        assert host.stop() == WorkerResult.FAILED
        for path in tmp_path.iterdir():
            path.unlink()
    finally:
        host.stop()


def test_close_failure_retains_handle_until_explicit_retry(monkeypatch):
    host = WorkerSupervisor(cooperative)
    host.start()
    try:
        def denied():
            raise OSError("SYNTHETIC_PRIVATE_DIAGNOSTIC")

        with monkeypatch.context() as patch:
            patch.setattr(host._process, "close", denied)
            with pytest.raises(SupervisorError, match="termination unconfirmed") as error:
                host.stop()
            assert error.value.__suppress_context__
            assert not host._process.is_alive()
            assert host._result is None
            with pytest.raises(SupervisorError, match="already attempted"):
                host.start()
        assert host.stop() == WorkerResult.STOPPED
        assert host._process._closed
    finally:
        host.stop()


def test_start_failure_is_sanitized_and_not_retryable(monkeypatch):
    host = WorkerSupervisor(cooperative)

    def denied():
        raise OSError("SYNTHETIC_PRIVATE_DIAGNOSTIC")

    monkeypatch.setattr(host._process, "start", denied)
    with pytest.raises(SupervisorError, match="worker start failed") as error:
        host.start()
    assert str(error.value) == "worker start failed"
    assert error.value.__suppress_context__
    with pytest.raises(SupervisorError, match="already attempted"):
        host.start()
    with pytest.raises(SupervisorError, match="not started"):
        host.stop()


def test_partial_start_retains_child_without_admitting_worker(monkeypatch, tmp_path):
    marker = tmp_path / "must-not-exist.txt"
    host = WorkerSupervisor(partial(explicit_flush, marker), grace_seconds=0.2)
    original = host._process.start

    def partial_start(self):
        original()
        raise OSError("SYNTHETIC_PRIVATE_DIAGNOSTIC")

    monkeypatch.setattr(type(host._process), "start", partial_start)
    with pytest.raises(SupervisorError, match="worker start failed"):
        host.start()
    try:
        assert host._started
        assert not host._admitted.is_set()
        assert host.stop() in (WorkerResult.FAILED, WorkerResult.TERMINATED)
        assert not marker.exists()
    finally:
        host.stop()


def host_for_parent_death(ready, connection):
    host = WorkerSupervisor(partial(hanging, ready))
    host.start()
    connection.send(host._process.pid)
    connection.close()
    threading.Event().wait()


def test_parent_termination_kills_contained_worker():
    import ctypes as c
    from ctypes import wintypes as w

    context = mp.get_context("spawn")
    ready = context.Event()
    receiver, sender = context.Pipe(duplex=False)
    parent = context.Process(target=host_for_parent_death, args=(ready, sender))
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.WaitForSingleObject.restype = w.DWORD
    kernel.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = None
    parent.start()
    sender.close()
    try:
        assert receiver.poll(10)
        pid = receiver.recv()
        handle = kernel.OpenProcess(0x100001, False, pid)
        assert handle
        assert ready.wait(10), "worker did not pass containment"
        assert kernel.WaitForSingleObject(handle, 0) == 258
        parent.terminate()
        parent.join(5)
        assert not parent.is_alive()
        assert kernel.WaitForSingleObject(handle, 5000) == 0
    finally:
        if parent.is_alive():
            parent.terminate()
            parent.join(5)
        if handle:
            kernel.TerminateProcess(handle, 1)
            kernel.CloseHandle(handle)
        receiver.close()
        parent.close()


def test_missing_job_rejects_before_worker(tmp_path):
    from passivelistener.supervisor import _entry

    context = mp.get_context("spawn")
    stop, admitted = context.Event(), context.Event()
    admitted.set()
    stop.set()
    marker = tmp_path / "must-not-exist.txt"
    child = context.Process(target=_entry, args=(
        partial(explicit_flush, marker), stop, admitted, "Local\\PassiveListener-absent-test",
    ))
    child.start()
    try:
        child.join(5)
        assert child.exitcode == 70
        assert not marker.exists()
    finally:
        if child.is_alive():
            child.terminate()
            child.join(5)
        child.close()


def test_job_name_collision_rejected():
    from uuid import uuid4

    from passivelistener.worker_job import ContainmentError, WorkerJob

    name = "Local\\PassiveListener-test-" + uuid4().hex
    job = WorkerJob(name)
    job.open()
    duplicate = WorkerJob(name)
    try:
        with pytest.raises(ContainmentError):
            duplicate.open()
    finally:
        duplicate.close()
        job.close()
        job.close()


def test_pre_spawn_failure_job_cleanup_can_be_retried(monkeypatch):
    from passivelistener.worker_job import WorkerJob

    host = WorkerSupervisor(cooperative)
    original = WorkerJob.close

    def denied_start():
        raise OSError("synthetic start failure")

    def denied_close(self):
        raise OSError("synthetic close failure")

    monkeypatch.setattr(host._process, "start", denied_start)
    with monkeypatch.context() as patch:
        patch.setattr(WorkerJob, "close", denied_close)
        with pytest.raises(SupervisorError, match="containment cleanup failed"):
            host.start()
    try:
        assert host.stop() == WorkerResult.FAILED
        assert host._job._handle is None
        assert host.stop() == WorkerResult.FAILED
    finally:
        original(host._job)


def test_initialization_and_cleanup_failure_retains_job(monkeypatch):
    from passivelistener import worker_job

    kernel = worker_job._kernel()
    real_close = kernel.CloseHandle
    host = WorkerSupervisor(cooperative)
    monkeypatch.setattr(worker_job, "_kernel", lambda: kernel)
    with monkeypatch.context() as patch:
        patch.setattr(kernel, "SetInformationJobObject", lambda *args: 0)
        patch.setattr(kernel, "CloseHandle", lambda handle: 0)
        with pytest.raises(SupervisorError, match="containment cleanup failed"):
            host.start()
        assert host._job._handle
        with pytest.raises(SupervisorError, match="containment cleanup failed"):
            host.stop()
        assert host._result is None
    try:
        assert host.stop() == WorkerResult.FAILED
        assert host._job._handle is None
    finally:
        if host._job._handle:
            real_close(host._job._handle)
