"""Owned WTS registration for a trusted user-host window/message loop.

The caller owns the window and pumps messages on its creating thread. Register
before worker startup; forward WM_WTSSESSION_CHANGE to dispatch. Any session
transition revokes the one-shot host. Unlock/connect never automatically resume.
This does not establish initial desktop unlock or microphone consent.
"""

import ctypes as c
import os
from ctypes import wintypes as w
from typing import Protocol

WM_WTSSESSION_CHANGE = 0x02B1
_QUARANTINE: list['SessionNotifications'] = []


class CancelTarget(Protocol):
    def request_cancel(self) -> None: ...


def _libraries() -> tuple[c.WinDLL, c.WinDLL, c.WinDLL]:
    if os.name != 'nt':
        raise RuntimeError('session notifications unavailable')
    kernel = c.WinDLL('kernel32', use_last_error=True)
    user = c.WinDLL('user32', use_last_error=True)
    terminal = c.WinDLL('wtsapi32', use_last_error=True)
    kernel.GetCurrentProcessId.restype = w.DWORD
    kernel.GetCurrentThreadId.restype = w.DWORD
    user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
    user.GetWindowThreadProcessId.restype = w.DWORD
    terminal.WTSRegisterSessionNotification.argtypes = [w.HWND, w.DWORD]
    terminal.WTSRegisterSessionNotification.restype = w.BOOL
    terminal.WTSUnRegisterSessionNotification.argtypes = [w.HWND]
    terminal.WTSUnRegisterSessionNotification.restype = w.BOOL
    return kernel, user, terminal


class SessionNotifications:
    """One-shot registration; lifecycle calls and dispatch stay on window thread.

    Cancellation is nonblocking, but the lifecycle owner MUST close/reap the host
    outside WndProc. Unregister only after host reap, before DestroyWindow. Keep
    this object AND its window alive on failed unregister and retry close; failed
    cleanup permanently quarantines new registrations. No destructor cleanup.
    The supplied window must be dedicated and not already registered elsewhere.
    """

    def __init__(self, target: CancelTarget) -> None:
        self._target = target
        self._hwnd: int | None = None
        self._attempted = False
        self._thread: int | None = None

    def acquire(self, hwnd: int) -> None:
        if self._attempted or _QUARANTINE:
            self._target.request_cancel()
            raise RuntimeError('session notification admission rejected')
        self._attempted = True
        try:
            kernel, user, terminal = _libraries()
            process = w.DWORD()
            thread = int(user.GetWindowThreadProcessId(hwnd, c.byref(process)))
            if (not thread or thread != kernel.GetCurrentThreadId()
                    or process.value != kernel.GetCurrentProcessId()):
                raise RuntimeError('window ownership rejected')
            self._thread = thread
            if not terminal.WTSRegisterSessionNotification(hwnd, 0):
                raise RuntimeError('registration rejected')
            self._hwnd = hwnd
        except BaseException:
            self._target.request_cancel()
            raise RuntimeError('session notification registration failed') from None

    def dispatch(self, message: int) -> bool:
        """WndProc hook: conservative revocation, no native cleanup or restart.

        Intentionally ignore event/session payload: forged messages can only
        revoke admission. Unknown future session events also fail closed.
        """
        if message != WM_WTSSESSION_CHANGE:
            return False
        self._target.request_cancel()
        return True

    def close(self) -> None:
        self._target.request_cancel()
        if self._hwnd is None:
            return
        try:
            kernel, _, terminal = _libraries()
            if kernel.GetCurrentThreadId() != self._thread:
                raise RuntimeError('window thread required')
            if not terminal.WTSUnRegisterSessionNotification(self._hwnd):
                raise RuntimeError('unregistration rejected')
            self._hwnd = None
        except BaseException:
            if self not in _QUARANTINE:
                _QUARANTINE.append(self)
            raise RuntimeError('session notification cleanup unconfirmed') from None
