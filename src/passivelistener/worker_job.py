"""Host-owned Windows job: no worker code before containment and admission.

Named only for same-session spawn bootstrap; name is not an authorization API.
Trusted host and child must not mutate identity or retain/duplicate job handles.
"""

import ctypes as c
import os
from ctypes import wintypes as w


class ContainmentError(RuntimeError):
    pass


class _Basic(c.Structure):
    _fields_ = [("per_process", c.c_int64), ("per_job", c.c_int64),
                ("flags", w.DWORD), ("minimum", c.c_size_t), ("maximum", c.c_size_t),
                ("active", w.DWORD), ("affinity", c.c_size_t),
                ("priority", w.DWORD), ("scheduling", w.DWORD)]


class _IO(c.Structure):
    _fields_ = [(name, c.c_uint64) for name in
                ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class _Extended(c.Structure):
    _fields_ = [("basic", _Basic), ("io", _IO), ("process_memory", c.c_size_t),
                ("job_memory", c.c_size_t), ("peak_process", c.c_size_t),
                ("peak_job", c.c_size_t)]


def _kernel() -> c.WinDLL:
    if os.name != "nt":
        raise ContainmentError("worker containment unavailable")
    k = c.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
    k.CreateJobObjectW.restype = w.HANDLE
    k.OpenJobObjectW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
    k.OpenJobObjectW.restype = w.HANDLE
    k.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    k.SetInformationJobObject.restype = w.BOOL
    k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    k.AssignProcessToJobObject.restype = w.BOOL
    k.GetCurrentProcess.restype = w.HANDLE
    k.CloseHandle.argtypes = [w.HANDLE]
    k.CloseHandle.restype = w.BOOL
    return k


class WorkerJob:
    """Non-inheritable host handle; close only after reap or failed startup."""

    def __init__(self, name: str) -> None:
        self._kernel = _kernel()
        self._name = name
        self._handle: int | None = None
        self._attempted = False

    def open(self) -> None:
        # Publish Python ownership before acquiring a native resource, so even
        # initialization + cleanup failures retain a handle for explicit retry.
        if self._attempted:
            raise ContainmentError("worker containment already attempted")
        self._attempted = True
        self._handle = self._kernel.CreateJobObjectW(None, self._name)
        exists = c.get_last_error() == 183
        if not self._handle:
            raise ContainmentError("worker containment unavailable")
        try:
            if exists:
                raise ContainmentError("worker containment unavailable")
            limits = _Extended()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self._kernel.SetInformationJobObject(
                self._handle, 9, c.byref(limits), c.sizeof(limits),
            ):
                raise ContainmentError("worker containment unavailable")
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self._handle:
            if not self._kernel.CloseHandle(self._handle):
                raise ContainmentError("worker containment cleanup failed")
            self._handle = None


def enter_worker_job(name: str) -> None:
    """Release child's temporary handle BEFORE any backend/admission wait.

    If the host died while we held the last handle, this close kills this process.
    If the host died earlier, open fails. No Python watchdog/GIL dependency.
    """
    k = _kernel()
    handle = k.OpenJobObjectW(1, False, name)  # JOB_OBJECT_ASSIGN_PROCESS only
    if not handle:
        raise ContainmentError("worker containment unavailable")
    try:
        if not k.AssignProcessToJobObject(handle, k.GetCurrentProcess()):
            raise ContainmentError("worker containment unavailable")
    finally:
        if not k.CloseHandle(handle):
            raise ContainmentError("worker containment cleanup failed")
