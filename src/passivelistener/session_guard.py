"""Fail-closed eligibility check for the calling user worker, never a broker.

This is a point-in-time check, not microphone consent or a session monitor.
"""

import ctypes as c
import os
from ctypes import wintypes as w
from dataclasses import dataclass


class SessionRejected(RuntimeError):
    """Fixed diagnostic; never include identity or underlying OS exceptions."""


@dataclass(frozen=True)
class _Snapshot:
    session: int
    elevated: bool
    interactive: bool
    service: bool
    active: bool


def _eligible(state: _Snapshot) -> bool:
    return (state.session > 0 and not state.elevated and state.interactive
            and not state.service and state.active)


def _snapshot() -> _Snapshot:
    if os.name != "nt":
        raise SessionRejected("capture session rejected")
    kernel = c.WinDLL("kernel32", use_last_error=True)
    security = c.WinDLL("advapi32", use_last_error=True)
    terminal = c.WinDLL("wtsapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.GetCurrentThread.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    kernel.LocalFree.argtypes = [c.c_void_p]
    kernel.LocalFree.restype = c.c_void_p
    security.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    security.OpenProcessToken.restype = w.BOOL
    security.OpenThreadToken.argtypes = [w.HANDLE, w.DWORD, w.BOOL, c.POINTER(w.HANDLE)]
    security.OpenThreadToken.restype = w.BOOL
    security.GetTokenInformation.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD,
                                           c.POINTER(w.DWORD)]
    security.GetTokenInformation.restype = w.BOOL
    security.ConvertStringSidToSidW.argtypes = [w.LPCWSTR, c.POINTER(c.c_void_p)]
    security.ConvertStringSidToSidW.restype = w.BOOL
    security.CheckTokenMembership.argtypes = [w.HANDLE, c.c_void_p, c.POINTER(w.BOOL)]
    security.CheckTokenMembership.restype = w.BOOL
    terminal.WTSQuerySessionInformationW.argtypes = [w.HANDLE, w.DWORD, c.c_int,
                                                   c.POINTER(c.c_void_p), c.POINTER(w.DWORD)]
    terminal.WTSQuerySessionInformationW.restype = w.BOOL
    terminal.WTSFreeMemory.argtypes = [c.c_void_p]
    terminal.WTSFreeMemory.restype = None

    def checked(ok: int) -> None:
        if not ok:
            raise SessionRejected("capture session rejected")

    # A worker must not impersonate another identity. Access denied is rejection,
    # not evidence of absence. Thread identity must remain immutable in the host.
    thread_token = w.HANDLE()
    if security.OpenThreadToken(kernel.GetCurrentThread(), 8, True, c.byref(thread_token)):
        checked(kernel.CloseHandle(thread_token))
        raise SessionRejected("capture session rejected")
    if c.get_last_error() != 1008:  # ERROR_NO_TOKEN
        raise SessionRejected("capture session rejected")

    token = w.HANDLE()
    checked(security.OpenProcessToken(kernel.GetCurrentProcess(), 8, c.byref(token)))
    try:
        def integer(kind: int) -> int:
            value, size = w.DWORD(), w.DWORD()
            checked(security.GetTokenInformation(token, kind, c.byref(value),
                                                c.sizeof(value), c.byref(size)))
            checked(size.value == c.sizeof(value))
            return int(value.value)

        session = integer(12)  # TokenSessionId
        elevated = integer(20) != 0  # TokenElevation

        def member(sid_text: str) -> bool:
            sid = c.c_void_p()
            checked(security.ConvertStringSidToSidW(sid_text, c.byref(sid)))
            try:
                result = w.BOOL()
                checked(security.CheckTokenMembership(None, sid, c.byref(result)))
                return bool(result.value)
            finally:
                checked(not kernel.LocalFree(sid))

        interactive, service = member("S-1-5-4"), member("S-1-5-6")
        buffer, size = c.c_void_p(), w.DWORD()
        checked(terminal.WTSQuerySessionInformationW(None, session, 8,
                                                    c.byref(buffer), c.byref(size)))
        try:
            checked(bool(buffer) and size.value == c.sizeof(c.c_int))
            active = c.cast(buffer, c.POINTER(c.c_int)).contents.value == 0  # WTSActive
        finally:
            terminal.WTSFreeMemory(buffer)
        return _Snapshot(session, elevated, interactive, service, active)
    finally:
        checked(kernel.CloseHandle(token))


def require_capture_session() -> None:
    """Check the current token/session; return no reusable authorization token.

    Call inside the worker before device open and regularly during capture. The
    trusted host must stop capture on lock/logoff/disconnect events independently.
    No user-supplied session id, impersonation, token creation or elevation here.
    """
    try:
        if not _eligible(_snapshot()):
            raise SessionRejected("capture session rejected")
    except Exception:
        raise SessionRejected("capture session rejected") from None
