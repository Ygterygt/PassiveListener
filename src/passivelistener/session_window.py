"""Owned hidden user-session window, bounded pump, and ordered host teardown.

Acquire and pump on a dedicated window thread while another thread starts the
host. This owner never starts capture or infers consent/unlock from registration.
No destructor: live callback/window owners are retained until verified teardown.
"""

import ctypes as c
import os
import uuid
from ctypes import wintypes as w
from typing import Protocol

from passivelistener.session_notifications import SessionNotifications

_PROC = c.WINFUNCTYPE(c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
_OWNERS: list['SessionWindow'] = []
_QUARANTINE: list['SessionWindow'] = []


class Host(Protocol):
    def request_cancel(self) -> None: ...
    def close(self) -> None: ...


class _Class(c.Structure):
    _fields_ = [('size', w.UINT), ('style', w.UINT), ('procedure', _PROC),
                ('class_extra', c.c_int), ('window_extra', c.c_int),
                ('instance', w.HINSTANCE), ('icon', w.HICON), ('cursor', w.HANDLE),
                ('background', w.HBRUSH), ('menu', w.LPCWSTR), ('name', w.LPCWSTR),
                ('small_icon', w.HICON)]


def _libraries() -> tuple[c.WinDLL, c.WinDLL]:
    if os.name != 'nt':
        raise RuntimeError('session window unavailable')
    kernel = c.WinDLL('kernel32', use_last_error=True)
    user = c.WinDLL('user32', use_last_error=True)
    kernel.GetCurrentThreadId.restype = w.DWORD
    kernel.GetModuleHandleW.argtypes = [w.LPCWSTR]
    kernel.GetModuleHandleW.restype = w.HMODULE
    user.RegisterClassExW.argtypes = [c.POINTER(_Class)]
    user.RegisterClassExW.restype = w.ATOM
    user.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                                    c.c_int, c.c_int, c.c_int, c.c_int,
                                    w.HWND, w.HMENU, w.HINSTANCE, c.c_void_p]
    user.CreateWindowExW.restype = w.HWND
    user.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.DefWindowProcW.restype = c.c_ssize_t
    user.PeekMessageW.argtypes = [c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
    user.PeekMessageW.restype = w.BOOL
    user.DispatchMessageW.argtypes = [c.POINTER(w.MSG)]
    user.DispatchMessageW.restype = c.c_ssize_t
    user.DestroyWindow.argtypes = [w.HWND]
    user.DestroyWindow.restype = w.BOOL
    user.UnregisterClassW.argtypes = [w.LPCWSTR, w.HINSTANCE]
    user.UnregisterClassW.restype = w.BOOL
    return kernel, user


class SessionWindow:
    """One-shot owner; all calls except host.request_cancel use window thread.

    WndProc only latches revocation; pump/close reap outside the callback. On
    cleanup uncertainty the remaining window/class/callback stay strongly owned,
    and future owners fail closed. Retry close on the creating thread. Same-user
    trusted boundary; messages can revoke but can never grant admission.
    """

    def __init__(self, host: Host) -> None:
        self._host = host
        self._notifications = SessionNotifications(host)
        self._kernel: c.WinDLL | None = None
        self._user: c.WinDLL | None = None
        self._thread: int | None = None
        self._instance: int | None = None
        self._hwnd: int | None = None
        self._registered = False
        self._attempted = False
        self._closed = False
        self._revoked = False
        self._callback_failed = False
        self._name = 'PassiveListener-' + uuid.uuid4().hex
        self._callback = _PROC(self._dispatch)

    def _revoke(self) -> None:
        self._revoked = True
        self._host.request_cancel()

    def _dispatch(self, hwnd: int, message: int, wp: int, lp: int) -> int:
        try:
            if self._notifications.dispatch(message):
                self._revoked = True
                return 0
            if message in (0x0010, 0x0011, 0x0016):  # CLOSE, QUERYENDSESSION, ENDSESSION
                self._revoke()
                return 1 if message == 0x0011 else 0
            assert self._user is not None
            return int(self._user.DefWindowProcW(hwnd, message, wp, lp))
        except BaseException:
            # Never let ctypes print exception details from an OS callback.
            self._callback_failed = True
            self._revoked = True
            try:
                self._host.request_cancel()
            except BaseException:
                pass
            return 0

    def _check_thread(self) -> None:
        if self._kernel is None or self._kernel.GetCurrentThreadId() != self._thread:
            raise RuntimeError('session window thread required')

    def acquire(self) -> None:
        if self._attempted or self._closed or self._revoked or _QUARANTINE:
            self._revoke()
            raise RuntimeError('session window admission rejected')
        self._attempted = True
        _OWNERS.append(self)
        try:
            self._kernel, self._user = _libraries()
            self._thread = int(self._kernel.GetCurrentThreadId())
            self._instance = self._kernel.GetModuleHandleW(None)
            if not self._instance:
                raise RuntimeError('module unavailable')
            descriptor = _Class()
            descriptor.size = c.sizeof(_Class)
            descriptor.procedure = self._callback
            descriptor.instance = self._instance
            descriptor.name = self._name
            if not self._user.RegisterClassExW(c.byref(descriptor)):
                raise RuntimeError('class registration failed')
            self._registered = True
            self._hwnd = self._user.CreateWindowExW(
                0, self._name, '', 0, 0, 0, 0, 0, None, None, self._instance, None)
            if not self._hwnd or self._callback_failed:
                raise RuntimeError('window creation failed')
            self._notifications.acquire(self._hwnd)
            if self._revoked:
                raise RuntimeError('window admission revoked')
        except BaseException:
            self._revoke()
            self.close()
            raise RuntimeError('session window startup failed') from None

    def pump(self) -> bool:
        """Dispatch at most 64 queued messages; return False after revocation/reap.

        Caller must invoke promptly, including during asynchronous host startup.
        This is nonblocking except for host reap after revocation. WM_QUIT also
        revokes; it cannot silently stop the pump while leaving a child admitted.
        """
        self._check_thread()
        if self._closed or not self._hwnd:
            raise RuntimeError('session window not active')
        assert self._user is not None
        try:
            message = w.MSG()
            for _ in range(64):
                if not self._user.PeekMessageW(c.byref(message), self._hwnd, 0, 0, 1):
                    break
                if message.message == 0x0012:  # WM_QUIT is retrieved regardless of filter
                    self._revoke()
                else:
                    self._user.DispatchMessageW(c.byref(message))
                if self._revoked:
                    break
            if self._revoked:
                self.close()
                if self._callback_failed:
                    raise RuntimeError('callback failed')
                return False
            return True
        except BaseException:
            self._revoke()
            self.close()
            raise RuntimeError('session window pump failed') from None

    def close(self) -> None:
        """Reap host, unregister WTS, destroy HWND, release class, then owner."""
        self._revoke()
        if self._closed:
            return
        try:
            if self._thread is not None:
                self._check_thread()
            self._host.close()
            self._notifications.close()
            if self._hwnd:
                assert self._user is not None
                if not self._user.DestroyWindow(self._hwnd):
                    raise RuntimeError('window destruction failed')
                self._hwnd = None
            if self._registered:
                assert self._user is not None
                if not self._user.UnregisterClassW(self._name, self._instance):
                    raise RuntimeError('class release failed')
                self._registered = False
            self._closed = True
            if self in _OWNERS:
                _OWNERS.remove(self)
        except BaseException:
            if self not in _QUARANTINE:
                _QUARANTINE.append(self)
            raise RuntimeError('session window cleanup unconfirmed') from None
