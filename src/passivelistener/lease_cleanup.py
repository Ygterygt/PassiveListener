"""Fatal lease release quarantine shared by storage and protected inputs."""

import ctypes
import os
from threading import Lock
from typing import BinaryIO

_lock = Lock()
_failed: list[tuple[str, object]] = []


class LeaseCleanupError(RuntimeError):
    """Unconfirmed release: stop admission and terminate the owning process."""


def require_clean() -> None:
    with _lock:
        if _failed:
            raise LeaseCleanupError("lease cleanup quarantine active")


def _retain(kind: str, owner: object) -> None:
    with _lock:
        _failed.append((kind, owner))
    raise LeaseCleanupError("lease resource cleanup failed") from None


def close_handle(kernel: ctypes.WinDLL, handle: int) -> None:
    try:
        released = kernel.CloseHandle(handle)
    except BaseException:
        _retain("handle", (kernel, handle))
    else:
        if not released:
            _retain("handle", (kernel, handle))


def close_fd(fd: int) -> None:
    try:
        os.close(fd)
    except BaseException:
        _retain("fd", fd)


def close_stream(stream: BinaryIO) -> None:
    try:
        stream.close()
    except BaseException:
        _retain("stream", stream)
