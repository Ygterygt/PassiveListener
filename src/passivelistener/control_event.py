"""Same-user, session-local one-shot events. No transcript or command payloads."""

import ctypes
import re
import threading
import uuid
from ctypes import wintypes

from passivelistener.private_storage import (
    _Attributes,
    _descriptor,
    _free,
    _sddl,
    _security,
    current_user_sid,
)


def _api() -> ctypes.WinDLL:
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateEventW.argtypes = [ctypes.POINTER(_Attributes), wintypes.BOOL,
                                wintypes.BOOL, wintypes.LPCWSTR]
    api.CreateEventW.restype = wintypes.HANDLE
    api.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    api.OpenEventW.restype = wintypes.HANDLE
    api.SetEvent.argtypes = [wintypes.HANDLE]
    api.SetEvent.restype = wintypes.BOOL
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


def event_name() -> str:
    return "Local\\PassiveListener-" + uuid.uuid4().hex


class PrivateEvent:
    """Explicit, non-inheritable handle ownership; no finalizer or reset.

    Trusted same-user peers only. Names and ACLs do not authenticate a process.
    Serialize close with finite waits; failed close retains the handle for retry.
    Use distinct objects for ready and stop. Host retains both until child reap.
    """

    def __init__(self) -> None:
        self._handle: int | None = None
        self._lock = threading.Lock()

    def acquire(self, name: str, *, create: bool, signal: bool = False) -> None:
        if not re.fullmatch(r"Local\\PassiveListener-[0-9a-f]{32}", name):
            raise ValueError("invalid control event name")
        with self._lock:
            if self._handle is not None:
                raise RuntimeError("event already owned")
            sid = current_user_sid()
            policy = f"O:{sid}D:P(A;;0x1f0003;;;SY)(A;;0x1f0003;;;{sid})"
            with _descriptor(policy) as descriptor:
                api = _api()
                if create:
                    attributes = _Attributes(ctypes.sizeof(_Attributes), descriptor, False)
                    ctypes.set_last_error(0)
                    handle = api.CreateEventW(ctypes.byref(attributes), True, False, name)
                    collision = ctypes.get_last_error() == 183
                else:
                    # READ_CONTROL plus only the operation needed by this peer.
                    access = 0x20000 | (2 if signal else 0x100000)
                    handle = api.OpenEventW(access, False, name)
                    collision = False
                if not handle:
                    raise OSError("control event unavailable")
                self._handle = handle
                try:
                    if collision:
                        raise OSError("control event collision")
                    actual = ctypes.c_void_p()
                    if _security().GetSecurityInfo(handle, 6, 5, None, None, None, None,
                                                   ctypes.byref(actual)):
                        raise OSError("control event policy unavailable")
                    try:
                        if _sddl(actual) != _sddl(descriptor):
                            raise OSError("control event policy rejected")
                    finally:
                        _free(actual)
                except BaseException:
                    self._close()
                    raise

    def _owned(self) -> int:
        if self._handle is None:
            raise RuntimeError("control event closed")
        return self._handle

    def signal(self) -> None:
        with self._lock:
            if not _api().SetEvent(self._owned()):
                raise OSError("control event signal failed")

    def wait(self, milliseconds: int = 0) -> bool:
        if type(milliseconds) is not int or not 0 <= milliseconds <= 1000:
            raise ValueError("bounded event wait required")
        with self._lock:
            result = _api().WaitForSingleObject(self._owned(), milliseconds)
            if result not in (0, 258):
                raise OSError("control event wait failed")
            return bool(result == 0)

    def _close(self) -> None:
        if self._handle is not None:
            if not _api().CloseHandle(self._handle):
                raise OSError("control event cleanup unconfirmed")
            self._handle = None

    def close(self) -> None:
        with self._lock:
            self._close()

