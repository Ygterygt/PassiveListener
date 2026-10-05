import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows security APIs required")


def run_isolated(body: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", body], cwd=Path(__file__).resolve().parents[2] / "src",
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("operation", ["identity", "descriptor", "sddl"])
def test_failed_local_free_quarantines_real_allocation(operation: str) -> None:
    run_isolated('''
import ctypes
from types import SimpleNamespace
from unittest.mock import Mock, patch
from passivelistener import private_storage as s
kernel = s._kernel()
real_free = kernel.LocalFree
real_free.argtypes = [ctypes.c_void_p]
real_free.restype = ctypes.c_void_p
proxy = SimpleNamespace(
    LocalFree=Mock(side_effect=lambda p: p.value),
    CloseHandle=kernel.CloseHandle, GetCurrentProcess=kernel.GetCurrentProcess,
)
sid = s.current_user_sid()
with s._descriptor(f"O:{sid}D:P(A;;GA;;;{sid})") as descriptor:
    with patch.object(s, "_kernel", return_value=proxy):
        try:
            if OP == "identity":
                s.current_user_sid()
            elif OP == "sddl":
                s._sddl(descriptor)
            else:
                with s._descriptor(f"O:{sid}D:P(A;;GA;;;{sid})"):
                    pass
        except s.SecurityCleanupError:
            pass
        else:
            raise AssertionError("release failure hidden")
    assert len(s._failed_resources) == 1
    kind, address = s._failed_resources[0]
    assert kind == "allocation" and address
    assert ctypes.string_at(address, 1) is not None
    for acquire in (s.current_user_sid, lambda: s._descriptor("D:P").__enter__(),
                    lambda: s._sddl(descriptor)):
        try:
            acquire()
        except s.SecurityCleanupError:
            pass
        else:
            raise AssertionError("quarantine bypassed")
# No production retry: OS process teardown owns retained resources.
'''.replace('OP', repr(operation)))


def test_failed_token_close_retains_real_handle_and_blocks_identity() -> None:
    run_isolated('''
import ctypes
from ctypes import wintypes
from types import SimpleNamespace
from unittest.mock import Mock, patch
from passivelistener import private_storage as s
kernel = s._kernel()
proxy = SimpleNamespace(LocalFree=kernel.LocalFree, CloseHandle=Mock(return_value=0),
                        GetCurrentProcess=kernel.GetCurrentProcess)
with patch.object(s, "_kernel", return_value=proxy):
    try:
        s.current_user_sid()
    except s.SecurityCleanupError:
        pass
    else:
        raise AssertionError("token cleanup failure hidden")
kind, handle = s._failed_resources[0]
assert kind == "token"
flags = wintypes.DWORD()
kernel.GetHandleInformation.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
assert kernel.GetHandleInformation(handle, ctypes.byref(flags))
try:
    s.current_user_sid()
except s.SecurityCleanupError:
    pass
else:
    raise AssertionError("identity returned after cleanup failure")
''')


def test_native_success_does_not_quarantine() -> None:
    run_isolated('''
from passivelistener import private_storage as s
for _ in range(100):
    sid = s.current_user_sid()
    with s._descriptor(f"O:{sid}D:P(A;;GA;;;{sid})") as descriptor:
        assert s._sddl(descriptor)
assert s._failed_resources == []
''')


def test_combined_release_failures_retain_both_resources() -> None:
    run_isolated('''
from types import SimpleNamespace
from unittest.mock import Mock, patch
from passivelistener import private_storage as s
kernel = s._kernel()
proxy = SimpleNamespace(LocalFree=Mock(side_effect=lambda p: p.value),
                        CloseHandle=Mock(return_value=0),
                        GetCurrentProcess=kernel.GetCurrentProcess)
with patch.object(s, "_kernel", return_value=proxy):
    try:
        s.current_user_sid()
    except s.SecurityCleanupError as error:
        assert str(error) == "security resource cleanup failed"
    else:
        raise AssertionError("combined failure hidden")
assert [kind for kind, value in s._failed_resources] == ["allocation", "token"]
assert all(value for kind, value in s._failed_resources)
assert proxy.LocalFree.call_count == proxy.CloseHandle.call_count == 1
''')


def test_event_descriptor_exit_failure_preserves_owned_event() -> None:
    run_isolated('''
import ctypes
from types import SimpleNamespace
from unittest.mock import Mock, patch
from passivelistener import private_storage as s
from passivelistener.control_event import PrivateEvent, event_name
kernel = s._kernel()
real_free = kernel.LocalFree
real_free.argtypes = [ctypes.c_void_p]
real_free.restype = ctypes.c_void_p
calls = 0
def fail_final_descriptor(pointer):
    global calls
    calls += 1
    # Identity string, two policy strings, GetSecurityInfo descriptor,
    # then the enclosing creation descriptor on context exit.
    if calls == 5:
        return pointer.value
    return real_free(pointer)
proxy = SimpleNamespace(LocalFree=Mock(side_effect=fail_final_descriptor),
                        CloseHandle=kernel.CloseHandle,
                        GetCurrentProcess=kernel.GetCurrentProcess)
event = PrivateEvent()
with patch.object(s, "_kernel", return_value=proxy):
    try:
        event.acquire(event_name(), create=True)
    except s.SecurityCleanupError:
        pass
    else:
        raise AssertionError("descriptor cleanup failure hidden")
assert calls == 5 and len(s._failed_resources) == 1
assert event._handle is not None
# Existing handles remain usable; quarantine is not cancellation.
event.signal()
assert event.wait()
event.close()
assert event._handle is None
try:
    PrivateEvent().acquire(event_name(), create=True)
except s.SecurityCleanupError:
    pass
else:
    raise AssertionError("later event acquisition admitted")
''')
