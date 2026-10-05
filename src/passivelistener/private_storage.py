"""User-bound private directories; no privilege elevation or existing ACL repair."""

import ctypes
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from threading import Lock

from passivelistener.storage_handles import _handle, _kernel, directory_lease


class _Attributes(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("descriptor", ctypes.c_void_p),
                ("inherit", wintypes.BOOL)]


def _security() -> ctypes.WinDLL:
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    api.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    api.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    api.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
                                   ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    api.GetSecurityInfo.restype = wintypes.DWORD
    return api


# A failed native release has ambiguous ownership. Never retry or reuse its
# address/handle in this process; retain the value until process termination.
_cleanup_lock = Lock()
_failed_resources: list[tuple[str, int]] = []


class SecurityCleanupError(OSError):
    """Fatal security-helper cleanup failure; terminate the owning worker."""


def _require_clean() -> None:
    with _cleanup_lock:
        if _failed_resources:
            raise SecurityCleanupError("security cleanup quarantine active")


def _quarantine(kind: str, value: int) -> None:
    with _cleanup_lock:
        _failed_resources.append((kind, value))
    raise SecurityCleanupError("security resource cleanup failed")


def _close_token(token: wintypes.HANDLE) -> None:
    if not _kernel().CloseHandle(token):
        _quarantine("token", int(token.value or 0))


def _free(pointer: ctypes.c_void_p) -> None:
    kernel = _kernel()
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if kernel.LocalFree(pointer):
        _quarantine("allocation", int(pointer.value or 0))


def current_user_sid() -> str:
    """Resolve the process user without accepting a caller-supplied target identity."""
    _require_clean()
    kernel = _kernel()
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    api = _security()
    api.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                    ctypes.POINTER(wintypes.HANDLE)]
    api.OpenProcessToken.restype = wintypes.BOOL
    api.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                       wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    api.GetTokenInformation.restype = wintypes.BOOL
    api.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    api.ConvertSidToStringSidW.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not api.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise OSError("user token unavailable")
    try:
        size = wintypes.DWORD()
        api.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not api.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise OSError("user identity unavailable")
        sid_pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        result = ctypes.c_void_p()
        if not api.ConvertSidToStringSidW(sid_pointer, ctypes.byref(result)):
            raise OSError("user identity conversion failed")
        try:
            sid = ctypes.wstring_at(result)
        finally:
            _free(result)
        if sid in ("S-1-5-18", "S-1-5-19", "S-1-5-20"):
            raise OSError("interactive user storage required")
        return sid
    finally:
        _close_token(token)


@contextmanager
def _descriptor(sddl: str) -> Iterator[ctypes.c_void_p]:
    _require_clean()
    value = ctypes.c_void_p()
    if not _security().ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(value), None):
        raise OSError("private security descriptor unavailable")
    try:
        yield value
    finally:
        _free(value)


def _sddl(value: ctypes.c_void_p) -> str:
    _require_clean()
    result = ctypes.c_void_p()
    if not _security().ConvertSecurityDescriptorToStringSecurityDescriptorW(
            value, 1, 5, ctypes.byref(result), None):
        raise OSError("private security descriptor unreadable")
    try:
        return ctypes.wstring_at(result)
    finally:
        _free(result)


@contextmanager
def private_directory(root: Path, *, create: bool = False) -> Iterator[None]:
    """Hold ancestors and validate exact owner/protected DACL before writing.

    Parent must already exist. New leaf is private from creation. Existing
    directories with broader/different access are rejected without changing ACLs.
    Administrators with takeover/backup privileges remain outside this boundary.
    """
    sid = current_user_sid()
    policy = f"O:{sid}D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;{sid})"
    with directory_lease(root.parent), _descriptor(policy) as descriptor:
        # Validate the leaf lexically before passing it to CreateDirectoryW.
        from passivelistener.archive import safe_basename

        if not safe_basename(root.name) or root.name.endswith((" ", ".")):
            raise ValueError("canonical private directory required")
        native = "\\\\?\\" + str(root)
        if create:
            kernel = _kernel()
            kernel.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(_Attributes)]
            kernel.CreateDirectoryW.restype = wintypes.BOOL
            attributes = _Attributes(ctypes.sizeof(_Attributes), descriptor, False)
            if not kernel.CreateDirectoryW(native, ctypes.byref(attributes)):
                if ctypes.get_last_error() != 183:
                    raise OSError("private directory creation failed")
        with _handle(root, directory=True, security=True) as handle:
            actual = ctypes.c_void_p()
            if _security().GetSecurityInfo(handle, 1, 5, None, None, None, None,
                                           ctypes.byref(actual)):
                raise OSError("private directory security unavailable")
            try:
                if _sddl(actual) != _sddl(descriptor):
                    raise OSError("private directory access policy rejected")
            finally:
                _free(actual)
            yield
