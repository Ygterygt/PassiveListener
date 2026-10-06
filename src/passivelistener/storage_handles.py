"""Windows storage leases. Callers must provision a private directory first."""

import ctypes
import os
import re
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import BinaryIO

from passivelistener.lease_cleanup import close_fd, close_handle, close_stream, require_clean
from passivelistener.windows_input import _FileInfo


def _kernel() -> ctypes.WinDLL:
    if os.name != "nt":
        raise OSError("Windows storage required")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInfo)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                ctypes.c_void_p, wintypes.DWORD]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel.GetDriveTypeW.restype = wintypes.UINT
    return kernel


@contextmanager
def _handle(path: Path, *, directory: bool = False, delete: bool = False,
            create: bool = False, security: bool = False) -> Iterator[int]:
    require_clean()
    kernel = _kernel()
    access = 0x81 if directory else 0x80000000 | (0x10000 if delete else 0)
    access |= 0x20000 if security else 0
    native = str(path) if str(path).startswith("\\\\?\\") else "\\\\?\\" + str(path)
    handle = kernel.CreateFileW(native, access, 3 if directory else 0, None,
                                4 if create else 3, 0x00200000 | 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError("storage lease unavailable")
    try:
        info = _FileInfo()
        if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise OSError("storage metadata unavailable")
        if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
            raise OSError("storage reparse point or type rejected")
        if not directory and info.links != 1:
            raise OSError("storage hard links rejected")
        yield handle
    finally:
        close_handle(kernel, handle)


@contextmanager
def directory_lease(root: Path) -> Iterator[None]:
    """Hold all ancestors so a concurrent rename/junction cannot redirect I/O."""
    if (not re.match(r"^[a-zA-Z]:\\", str(root))
            or any(part in ("..", ".") or part.endswith((" ", ".")) or ":" in part
                   for part in root.parts[1:])
            or _kernel().GetDriveTypeW(root.anchor) != 3):
        raise ValueError("canonical fixed local path required")
    with ExitStack() as stack:
        for directory in [*reversed(root.parents), root]:
            stack.enter_context(_handle(directory, directory=True))
        yield


@contextmanager
def archive_lock(root: Path) -> Iterator[None]:
    # OPEN_ALWAYS, no sharing: kernel releases this lock on process termination.
    with _handle(root / ".archive.lock", create=True):
        yield


@contextmanager
def source_lease(path: Path, *, delete: bool = False) -> Iterator[BinaryIO]:
    import msvcrt

    with _handle(path, delete=delete) as handle:
        # CRT owns a duplicate; the original handle stays alive until after stream close.
        # Duplicate via os.dup would require transferring original ownership first.
        kernel = _kernel()
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                          ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                          wintypes.BOOL, wintypes.DWORD]
        kernel.DuplicateHandle.restype = wintypes.BOOL
        duplicate = wintypes.HANDLE()
        process = kernel.GetCurrentProcess()
        if not kernel.DuplicateHandle(process, handle, process,
                                      ctypes.byref(duplicate), 0, False, 2):
            raise OSError("storage handle duplication failed")
        if duplicate.value is None:
            raise OSError("storage duplicate handle missing")
        try:
            fd = msvcrt.open_osfhandle(duplicate.value, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            close_handle(kernel, duplicate.value)
            raise
        try:
            stream = os.fdopen(fd, "rb")
        except BaseException:
            close_fd(fd)
            raise
        try:
            yield stream
        finally:
            close_stream(stream)


def delete_held_source(stream: BinaryIO) -> None:
    """Delete the leased object, never a freshly resolved pathname."""
    import msvcrt

    disposition = wintypes.BOOL(True)
    if not _kernel().SetFileInformationByHandle(msvcrt.get_osfhandle(stream.fileno()), 4,
                                                ctypes.byref(disposition),
                                                ctypes.sizeof(disposition)):
        raise OSError("source deletion deferred")



