"""Point-in-time local unlock check; never microphone consent or a lock monitor."""

import ctypes as c
import os
import sys
from ctypes import wintypes as w

from passivelistener.session_guard import SessionRejected, require_capture_session


class _Level1(c.Structure):
    _fields_ = [
        ('session', w.ULONG), ('state', c.c_int), ('flags', w.LONG),
        ('station', w.WCHAR * 33), ('user', w.WCHAR * 21), ('domain', w.WCHAR * 18),
        ('times', c.c_longlong * 5), ('counters', w.DWORD * 6),
    ]


class _Info(c.Structure):
    # The level-one union currently has one member with eight-byte alignment.
    _fields_ = [('level', w.DWORD), ('data', _Level1)]


def _query_unlocked() -> bool:
    if os.name != 'nt' or sys.getwindowsversion().major < 10:
        # No support for the reversed flags on Windows 7 / Server 2008 R2.
        raise SessionRejected('unlocked session required')
    kernel = c.WinDLL('kernel32', use_last_error=True)
    terminal = c.WinDLL('wtsapi32', use_last_error=True)
    kernel.GetCurrentProcessId.restype = w.DWORD
    kernel.ProcessIdToSessionId.argtypes = [w.DWORD, c.POINTER(w.DWORD)]
    kernel.ProcessIdToSessionId.restype = w.BOOL
    terminal.WTSQuerySessionInformationW.argtypes = [
        w.HANDLE, w.DWORD, c.c_int, c.POINTER(c.c_void_p), c.POINTER(w.DWORD)]
    terminal.WTSQuerySessionInformationW.restype = w.BOOL
    terminal.WTSFreeMemory.argtypes = [c.c_void_p]
    terminal.WTSFreeMemory.restype = None
    session = w.DWORD()
    if not kernel.ProcessIdToSessionId(kernel.GetCurrentProcessId(), c.byref(session)):
        raise SessionRejected('unlocked session required')
    if not session.value:
        return False
    buffer, size = c.c_void_p(), w.DWORD()
    try:
        ok = terminal.WTSQuerySessionInformationW(
            None, session.value, 25, c.byref(buffer), c.byref(size))
        if not ok or not buffer or size.value != c.sizeof(_Info):
            raise SessionRejected('unlocked session required')
        info = c.cast(buffer, c.POINTER(_Info)).contents
        # Never copy/format identity strings or times from this OS-owned buffer.
        return bool(info.level == 1 and info.data.session == session.value
                and info.data.state == 0 and info.data.flags == 1)
    finally:
        if buffer:
            # API has a void return. No success status exists to inspect.
            terminal.WTSFreeMemory(buffer)


def require_unlocked_session() -> None:
    """Require eligible token AND active/unlocked current process session.

    Register and pump session notifications before calling. This snapshot can
    become stale immediately; callers must honor cancellation and independently
    obtain user consent before opening capture. No unlock-triggered restart.
    """
    try:
        require_capture_session()
        if not _query_unlocked():
            raise SessionRejected('unlocked session required')
    except Exception:
        raise SessionRejected('unlocked session required') from None
