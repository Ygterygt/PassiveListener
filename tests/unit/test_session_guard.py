import ctypes
import itertools
from dataclasses import replace

import pytest

from passivelistener import session_guard as guard


def test_only_active_unelevated_interactive_nonservice_session_is_eligible():
    for session, elevated, interactive, service, active in itertools.product(
            [0, 1], [False, True], [False, True], [False, True], [False, True]):
        state = guard._Snapshot(session, elevated, interactive, service, active)
        assert guard._eligible(state) == (
            session == 1 and not elevated and interactive and not service and active)


@pytest.mark.parametrize("field,value", [
    ("session", 0), ("session", -1), ("elevated", True),
    ("interactive", False), ("service", True), ("active", False),
])
def test_public_guard_rejects_each_boundary(monkeypatch, field, value):
    good = guard._Snapshot(1, False, True, False, True)
    monkeypatch.setattr(guard, "_snapshot", lambda: replace(good, **{field: value}))
    with pytest.raises(guard.SessionRejected, match="^capture session rejected$"):
        guard.require_capture_session()


def test_public_guard_accepts_eligible_snapshot(monkeypatch):
    monkeypatch.setattr(guard, "_snapshot", lambda: guard._Snapshot(1, False, True, False, True))
    assert guard.require_capture_session() is None


def test_query_failure_has_fixed_diagnostic(monkeypatch):
    def fail():
        raise OSError("SYNTHETIC_PRIVATE_IDENTITY")
    monkeypatch.setattr(guard, "_snapshot", fail)
    with pytest.raises(guard.SessionRejected) as caught:
        guard.require_capture_session()
    assert str(caught.value) == "capture session rejected"
    assert caught.value.__suppress_context__


def test_native_current_token_and_session():
    # Read-only observation; no capture, elevation, session switch or impersonation.
    state = guard._snapshot()
    assert state.session >= 0
    security = ctypes.WinDLL("shell32")
    security.IsUserAnAdmin.restype = ctypes.c_int
    if security.IsUserAnAdmin():
        assert not guard._eligible(state)
    if guard._eligible(state):
        guard.require_capture_session()
    else:
        with pytest.raises(guard.SessionRejected):
            guard.require_capture_session()


def test_native_repeated_queries_release_handles():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetProcessHandleCount.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    def count():
        result = ctypes.c_ulong()
        assert kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(result))
        return result.value
    guard._snapshot()
    before = count()
    for _ in range(100):
        guard._snapshot()
    assert count() == before


@pytest.mark.parametrize("fault", ["token_query", "token_size", "wts_size",
                                  "membership", "close", "free"])
def test_native_failures_reject_and_cleanup(monkeypatch, fault):
    real_dll = ctypes.WinDLL
    calls = []

    class Function:
        def __init__(self, name, function):
            self.name, self.function = name, function

        def __call__(self, *args):
            self.function.argtypes = getattr(self, "argtypes", None)
            self.function.restype = getattr(self, "restype", ctypes.c_int)
            result = self.function(*args)
            calls.append(self.name)
            if self.name == "GetTokenInformation":
                if fault == "token_query":
                    return 0
                if fault == "token_size":
                    args[4]._obj.value = 0
            if self.name == "WTSQuerySessionInformationW" and fault == "wts_size":
                args[4]._obj.value = 0
            if self.name == "CheckTokenMembership" and fault == "membership":
                return 0
            # Actually release native resources before simulating failed cleanup.
            if self.name == "CloseHandle" and fault == "close":
                return 0
            if self.name == "LocalFree" and fault == "free":
                return 1
            return result

    class Library:
        def __init__(self, name, **kwargs):
            self.dll = real_dll(name, **kwargs)
            self.functions = {}

        def __getattr__(self, name):
            if name not in self.functions:
                self.functions[name] = Function(name, getattr(self.dll, name))
            return self.functions[name]

    monkeypatch.setattr(ctypes, "WinDLL", Library)
    with pytest.raises(guard.SessionRejected, match="^capture session rejected$"):
        guard.require_capture_session()
    assert "CloseHandle" in calls
    if fault == "wts_size":
        assert "WTSFreeMemory" in calls
    if fault in ("membership", "free"):
        assert "LocalFree" in calls
