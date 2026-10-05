"""Hold a Windows input and its ancestors against write/delete during use.

This primitive is not an ACL installer or a model loader. Callers must keep the
context alive until their consumer has finished reading the verified stream.
"""

import ctypes
import os
import re
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import BinaryIO

from passivelistener.integrity import IntegrityError, verify_stream


class _FileInfo(ctypes.Structure):
    _fields_ = [
        ("attributes", wintypes.DWORD),
        ("created", wintypes.FILETIME),
        ("accessed", wintypes.FILETIME),
        ("modified", wintypes.FILETIME),
        ("volume", wintypes.DWORD),
        ("size_high", wintypes.DWORD),
        ("size_low", wintypes.DWORD),
        ("links", wintypes.DWORD),
        ("index_high", wintypes.DWORD),
        ("index_low", wintypes.DWORD),
    ]


@contextmanager
def verified_input(path: Path, sha256: str, size: int) -> Iterator[BinaryIO]:
    """Yield the verified, rewound stream; fail closed outside local Windows.

    Reject reparse points at every level and multiple hard links. Ancestors and
    the leaf are opened from root to leaf without delete sharing. The leaf also
    denies write sharing; directory write sharing allows child operations. Never
    resolve symlinks before opening. Native consumers must use this stream or
    retain this context for the complete lifetime of any compatible read handle.
    """
    if os.name != "nt":
        raise IntegrityError("Windows input lease required")
    import msvcrt

    raw = str(path)
    if (not re.match(r"^[a-zA-Z]:\\", raw)
            or any(part in ("..", ".") or part.endswith((" ", "."))
                   or ":" in part for part in path.parts[1:])):
        raise IntegrityError("input must use a canonical local drive path")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInfo)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel.GetDriveTypeW.restype = wintypes.UINT
    if kernel.GetDriveTypeW(path.anchor) != 3:  # DRIVE_FIXED, no network/removable input
        raise IntegrityError("input must use a fixed local drive")
    try:
        with ExitStack() as stack:
            for entry in [*reversed(path.parents), path]:
                directory = entry != path
                handle = kernel.CreateFileW(
                    str(entry), 0x81 if directory else 0x80000000,
                    3 if directory else 1, None, 3, 0x00200000 | 0x02000000, None,
                )  # Directories: list/read attributes, share read/write but never delete.
                if handle == ctypes.c_void_p(-1).value:
                    raise IntegrityError("input could not be locked")
                # Transfer leaf ownership to the CRT only after metadata checks.
                with ExitStack() as pending:
                    pending.callback(kernel.CloseHandle, handle)
                    info = _FileInfo()
                    if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
                        raise IntegrityError("input metadata unavailable")
                    if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
                        raise IntegrityError("input reparse point or file type rejected")
                    if directory:
                        stack.callback(kernel.CloseHandle, handle)
                        pending.pop_all()
                    else:
                        if info.links != 1:
                            raise IntegrityError("input hard links rejected")
                        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
                        pending.pop_all()
                        try:
                            stream = os.fdopen(fd, "rb")
                        except BaseException:
                            os.close(fd)
                            raise
                        stack.enter_context(stream)
            verify_stream(stream, sha256, size)
            stream.seek(0)
            yield stream
    except OSError:
        raise IntegrityError("input lease failed") from None



