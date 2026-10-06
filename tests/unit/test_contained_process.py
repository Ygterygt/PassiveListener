import ctypes as c
import multiprocessing as mp
import os
import sys
import threading
from ctypes import wintypes as w
from pathlib import Path

import pytest

from passivelistener.contained_process import ContainedProcess, LaunchError

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows creation-time containment")


def launch(child, path, program):
    child.start(Path(sys.executable), ("-I", "-c", program, str(path)), cwd=path.parent)


def marker_program():
    return "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('synthetic')"


def test_suspended_child_is_in_specific_job_before_first_statement(tmp_path):
    child = ContainedProcess()
    path = tmp_path / "marker.txt"
    try:
        launch(child, path, marker_program())
        kernel = c.WinDLL("kernel32", use_last_error=True)
        kernel.IsProcessInJob.argtypes = [w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)]
        kernel.IsProcessInJob.restype = w.BOOL
        contained = w.BOOL()
        assert kernel.IsProcessInJob(child._info.process, child._job, c.byref(contained))
        assert contained.value
        assert child.wait(0.05) is None
        assert not path.exists()
        child.resume()
        assert child.wait(5) == 0
        assert path.read_text() == "synthetic"
        with pytest.raises(LaunchError, match="admission rejected"):
            child.resume()
    finally:
        child.close()
    assert not child._info.process and not child._info.thread and child._job is None
    child.close()


def test_environment_and_quoting(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_PRIVATE_ENV", "must not inherit")
    path = tmp_path / 'space & percent % unicode İ.txt'
    child = ContainedProcess()
    try:
        launch(child, path, "import os,pathlib,sys; "
               "assert 'SYNTHETIC_PRIVATE_ENV' not in os.environ; "
               "pathlib.Path(sys.argv[1]).write_text('synthetic')")
        child.resume()
        assert child.wait(5) == 0
        assert path.read_text() == "synthetic"
    finally:
        child.close()


def test_close_reaps_unadmitted_child(tmp_path):
    child = ContainedProcess()
    path = tmp_path / "never.txt"
    launch(child, path, marker_program())
    child.close()
    assert child.wait(0) == 70
    assert not path.exists()
    with pytest.raises(LaunchError, match="already attempted"):
        launch(child, path, marker_program())


def test_partial_create_failure_after_native_publication_is_reaped(tmp_path, monkeypatch):
    child = ContainedProcess()
    original = child._kernel.CreateProcessW
    path = tmp_path / "never.txt"

    def fail_after_create(*args):
        assert original(*args)
        raise OSError("SYNTHETIC_PRIVATE_DIAGNOSTIC")

    monkeypatch.setattr(child._kernel, "CreateProcessW", fail_after_create)
    with pytest.raises(LaunchError, match="contained launch failed") as error:
        launch(child, path, marker_program())
    assert str(error.value) == "contained launch failed"
    assert error.value.__suppress_context__
    assert child._closed
    assert child._exit == 70
    assert not path.exists()


def test_failed_termination_retains_handles_for_retry(tmp_path, monkeypatch):
    child = ContainedProcess()
    path = tmp_path / "never.txt"
    launch(child, path, marker_program())
    try:
        with monkeypatch.context() as patch:
            patch.setattr(child._kernel, "TerminateJobObject", lambda *args: 0)
            with pytest.raises(LaunchError, match="cleanup unconfirmed"):
                child.close()
            assert child._info.process and child._job
            assert not child._closed
        child.close()
        assert child._closed and child._exit == 70
        assert not path.exists()
    finally:
        child.close()


def test_attribute_failure_never_creates_child(tmp_path, monkeypatch):
    child = ContainedProcess()
    monkeypatch.setattr(child._kernel, "UpdateProcThreadAttribute", lambda *args: 0)
    with pytest.raises(LaunchError, match="launch failed"):
        launch(child, tmp_path / "never.txt", marker_program())
    assert child._info.pid == 0
    assert child._closed and child._job is None


def test_failed_resume_reaps_child(tmp_path, monkeypatch):
    child = ContainedProcess()
    path = tmp_path / "never.txt"
    launch(child, path, marker_program())
    monkeypatch.setattr(child._kernel, "ResumeThread", lambda *args: 0xFFFFFFFF)
    with pytest.raises(LaunchError, match="admission rejected"):
        child.resume()
    assert child._closed and child._exit == 70
    assert not path.exists()


def test_bad_executable_cleans_job(tmp_path):
    child = ContainedProcess()
    with pytest.raises(LaunchError, match="launch failed"):
        child.start(tmp_path / "absent.exe", (), cwd=tmp_path)
    assert child._closed and child._job is None


@pytest.mark.parametrize("value", [-1, 301, True, float("nan"), float("inf")])
def test_bad_wait_bound(value):
    with pytest.raises(ValueError, match="timeout rejected"):
        ContainedProcess(kill_seconds=value)


def suspended_parent(path, sender):
    child = ContainedProcess()
    launch(child, path, marker_program())
    sender.send(child._info.pid)
    sender.close()
    threading.Event().wait()


def test_host_death_before_python_bootstrap_reaps_child(tmp_path):
    context = mp.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    path = tmp_path / "never.txt"
    parent = context.Process(target=suspended_parent, args=(path, sender))
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
        assert kernel.WaitForSingleObject(handle, 0) == 258
        parent.terminate()
        parent.join(5)
        assert not parent.is_alive()
        assert kernel.WaitForSingleObject(handle, 5000) == 0
        assert not path.exists()
    finally:
        if parent.is_alive():
            parent.terminate()
            parent.join(5)
        if handle:
            kernel.TerminateProcess(handle, 70)
            kernel.CloseHandle(handle)
        receiver.close()
        parent.close()


def test_cleanup_failure_revokes_admission(tmp_path, monkeypatch):
    child = ContainedProcess()
    path = tmp_path / "never.txt"
    launch(child, path, marker_program())
    try:
        with monkeypatch.context() as patch:
            patch.setattr(child._kernel, "TerminateJobObject", lambda *args: 0)
            with pytest.raises(LaunchError, match="cleanup unconfirmed"):
                child.close()
            with pytest.raises(LaunchError, match="admission rejected"):
                child.resume()
        child.close()
        assert not path.exists()
    finally:
        child.close()


def test_unconfirmed_wait_retains_ownership(tmp_path, monkeypatch):
    child = ContainedProcess()
    launch(child, tmp_path / "never.txt", marker_program())
    try:
        with monkeypatch.context() as patch:
            patch.setattr(child._kernel, "WaitForSingleObject", lambda *args: 258)
            with pytest.raises(LaunchError, match="cleanup unconfirmed"):
                child.close()
            assert child._info.process and child._info.thread and child._job
            assert not child._closed
        child.close()
        assert child._exit == 70
    finally:
        child.close()


def test_partial_create_and_cleanup_failure_retain_native_handles(tmp_path, monkeypatch):
    child = ContainedProcess()
    original = child._kernel.CreateProcessW

    def fail_after_create(*args):
        assert original(*args)
        raise OSError("SYNTHETIC_PRIVATE_DIAGNOSTIC")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(child._kernel, "CreateProcessW", fail_after_create)
            patch.setattr(child._kernel, "TerminateJobObject", lambda *args: 0)
            with pytest.raises(LaunchError, match="launch cleanup unconfirmed"):
                launch(child, tmp_path / "never.txt", marker_program())
            assert child._info.process and child._info.thread and child._job
            assert not child._closed
        child.close()
        assert child._exit == 70
    finally:
        child.close()


def test_owned_native_handles_are_invalid_after_cleanup(tmp_path):
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.GetHandleInformation.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
    kernel.GetHandleInformation.restype = w.BOOL
    for _ in range(20):
        child = ContainedProcess()
        try:
            launch(child, tmp_path / "never.txt", marker_program())
            handles = (child._info.process, child._info.thread, child._job)
        finally:
            child.close()
        for handle in handles:
            flags = w.DWORD()
            assert not kernel.GetHandleInformation(handle, c.byref(flags))
            assert c.get_last_error() == 6  # ERROR_INVALID_HANDLE


@pytest.mark.parametrize("failed_field", ["thread", "process", "job"])
def test_close_handle_failure_retains_only_unclosed_ownership(tmp_path, monkeypatch, failed_field):
    child = ContainedProcess()
    launch(child, tmp_path / "never.txt", marker_program())
    failed = child._job if failed_field == "job" else getattr(child._info, failed_field)
    original = child._kernel.CloseHandle
    try:
        with monkeypatch.context() as patch:
            patch.setattr(child._kernel, "CloseHandle",
                          lambda handle: 0 if handle == failed else original(handle))
            with pytest.raises(LaunchError, match="cleanup unconfirmed"):
                child.close()
            assert not child._closed
            assert (child._job if failed_field == "job"
                    else getattr(child._info, failed_field)) == failed
            with pytest.raises(LaunchError, match="admission rejected"):
                child.resume()
        child.close()
        assert child._closed
        assert not child._info.thread and not child._info.process and child._job is None
    finally:
        child.close()


def test_owned_handles_are_not_inheritable(tmp_path):
    child = ContainedProcess()
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.GetHandleInformation.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
    kernel.GetHandleInformation.restype = w.BOOL
    try:
        launch(child, tmp_path / "never.txt", marker_program())
        for handle in (child._info.process, child._info.thread, child._job):
            flags = w.DWORD()
            assert kernel.GetHandleInformation(handle, c.byref(flags))
            assert not flags.value & 1
    finally:
        child.close()
