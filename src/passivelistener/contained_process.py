"""Creation-time containment for trusted same-user executable bootstrap.

Unwired successor primitive for multiprocessing bootstrap. Never accept commands
from configuration, QA requests or privileged IPC. Does not authorize capture.
"""

import ctypes as c
import math
import os
import subprocess
from ctypes import wintypes as w
from pathlib import Path
from threading import RLock

from passivelistener.worker_job import _Extended, _kernel


class LaunchError(RuntimeError):
    """Content-free operational diagnostic."""


class _Startup(c.Structure):
    _fields_ = [("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR),
                ("title", w.LPWSTR), ("x", w.DWORD), ("y", w.DWORD),
                ("x_size", w.DWORD), ("y_size", w.DWORD), ("x_chars", w.DWORD),
                ("y_chars", w.DWORD), ("fill", w.DWORD), ("flags", w.DWORD),
                ("show", w.WORD), ("reserved_size", w.WORD), ("reserved_ptr", c.c_void_p),
                ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE)]


class _StartupEx(c.Structure):
    _fields_ = [("startup", _Startup), ("attributes", c.c_void_p)]


class _ProcessInfo(c.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE),
                ("pid", w.DWORD), ("tid", w.DWORD)]


def _launch_kernel() -> c.WinDLL:
    k = _kernel()
    k.InitializeProcThreadAttributeList.argtypes = [c.c_void_p, w.DWORD, w.DWORD,
                                                   c.POINTER(c.c_size_t)]
    k.InitializeProcThreadAttributeList.restype = w.BOOL
    k.UpdateProcThreadAttribute.argtypes = [c.c_void_p, w.DWORD, c.c_size_t, c.c_void_p,
                                          c.c_size_t, c.c_void_p, c.c_void_p]
    k.UpdateProcThreadAttribute.restype = w.BOOL
    k.DeleteProcThreadAttributeList.argtypes = [c.c_void_p]
    k.DeleteProcThreadAttributeList.restype = None
    k.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p, w.BOOL,
                                w.DWORD, c.c_void_p, w.LPCWSTR, c.POINTER(_StartupEx),
                                c.POINTER(_ProcessInfo)]
    k.CreateProcessW.restype = w.BOOL
    k.ResumeThread.argtypes = [w.HANDLE]
    k.ResumeThread.restype = w.DWORD
    k.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
    k.TerminateJobObject.restype = w.BOOL
    k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    k.WaitForSingleObject.restype = w.DWORD
    k.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
    k.GetExitCodeProcess.restype = w.BOOL
    return k


def _checked(ok: int) -> None:
    if not ok:
        raise LaunchError("contained launch failed")


def _milliseconds(seconds: float) -> int:
    if (type(seconds) not in (float, int) or not math.isfinite(seconds)
            or not 0 <= seconds <= 300):
        raise ValueError("launch timeout rejected")
    return math.ceil(seconds * 1000)


class ContainedProcess:
    """Own an unnamed kill-on-close job and child from CreateProcess onward.

    start() creates suspended; resume() admits the initial thread exactly once.
    close() forcibly stops the job and confirms primary-process death, retaining
    handles on failure for explicit retry. It is not graceful transcript shutdown.
    Caller must close in finally. No handle inheritance, breakaway, shell, token
    manipulation, named-job lookup or multiprocessing import/unpickle bootstrap.
    The owner is the trusted, non-impersonating user host, never the SCM broker.
    Optional temporary must be private; caller holds its private_directory lease
    through child cleanup. Only TEMP/TMP are added to the minimal environment.
    """

    def __init__(self, *, kill_seconds: float = 5) -> None:
        self._kill_ms = _milliseconds(kill_seconds)
        self._kernel = _launch_kernel()
        self._info = _ProcessInfo()  # Native API writes directly into retained ownership.
        self._job: int | None = None
        self._attempted = False
        self._resumed = False
        self._closed = False
        self._closing = False
        self._exit: int | None = None
        self._temporary: Path | None = None
        self._lock = RLock()

    def start(self, executable: Path, arguments: tuple[str, ...], *, cwd: Path,
              temporary: Path | None = None) -> None:
        with self._lock:
            if self._attempted or self._closed:
                raise LaunchError("contained launch already attempted")
            self._attempted = True
            try:
                if not executable.is_absolute() or not cwd.is_absolute():
                    raise LaunchError("contained launch failed")
                command = subprocess.list2cmdline([str(executable), *arguments])
                if "\0" in command or len(command) >= 32767:
                    raise LaunchError("contained launch failed")
                self._job = self._kernel.CreateJobObjectW(None, None)
                _checked(bool(self._job))
                limits = _Extended()
                limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE; no breakaway.
                _checked(self._kernel.SetInformationJobObject(
                    self._job, 9, c.byref(limits), c.sizeof(limits),
                ))
                if temporary is None:
                    self._create(executable, command, cwd)
                else:
                    from passivelistener.private_storage import private_directory

                    with private_directory(temporary):
                        self._temporary = temporary
                        self._create(executable, command, cwd)
            except BaseException:
                try:
                    self.close()
                except BaseException:
                    raise LaunchError("contained launch cleanup unconfirmed") from None
                raise LaunchError("contained launch failed") from None

    def _create(self, executable: Path, command: str, cwd: Path) -> None:
        size = c.c_size_t()
        # Sizing call must fail with ERROR_INSUFFICIENT_BUFFER and return a size.
        result = self._kernel.InitializeProcThreadAttributeList(None, 1, 0, c.byref(size))
        _checked(not result and c.get_last_error() == 122 and 0 < size.value <= 65536)
        attributes = c.create_string_buffer(size.value)
        _checked(self._kernel.InitializeProcThreadAttributeList(
            attributes, 1, 0, c.byref(size),
        ))
        jobs = (w.HANDLE * 1)(self._job)
        try:
            _checked(self._kernel.UpdateProcThreadAttribute(
                attributes, 0, 0x2000D, jobs, c.sizeof(jobs), None, None,
            ))  # PROC_THREAD_ATTRIBUTE_JOB_LIST (Windows 10+).
            startup = _StartupEx()
            startup.startup.cb = c.sizeof(startup)
            startup.attributes = c.cast(attributes, c.c_void_p)
            # Do not inherit host credentials, PYTHONPATH, plugin settings or tokens.
            root = os.environ.get("SystemRoot", "")
            if not root or "\0" in root:
                raise LaunchError("contained launch failed")
            entries = ["SystemRoot=" + root]
            if self._temporary is not None:
                entries.extend(["TEMP=" + str(self._temporary), "TMP=" + str(self._temporary)])
            environment = c.create_unicode_buffer("\0".join(entries) + "\0\0")
            mutable_command = c.create_unicode_buffer(command)
            _checked(self._kernel.CreateProcessW(
                str(executable), mutable_command, None, None, False,
                0x80000 | 0x400 | 0x4 | 0x08000000,  # extended, Unicode, suspended, no console
                environment, str(cwd), c.byref(startup), c.byref(self._info),
            ))
        finally:
            self._kernel.DeleteProcThreadAttributeList(attributes)

    def resume(self) -> None:
        with self._lock:
            if self._closing or self._closed or self._resumed or not self._info.thread:
                raise LaunchError("contained admission rejected")
            # Mark before native call: uncertain success may never be retried.
            self._resumed = True
            try:
                _checked(self._kernel.ResumeThread(self._info.thread) == 1)
            except BaseException:
                try:
                    self.close()
                except BaseException:
                    raise LaunchError("contained launch cleanup unconfirmed") from None
                raise LaunchError("contained admission rejected") from None

    def wait(self, seconds: float) -> int | None:
        milliseconds = _milliseconds(seconds)
        with self._lock:
            if self._exit is not None:
                return self._exit
            if not self._info.process:
                raise LaunchError("contained process unavailable")
            result = self._kernel.WaitForSingleObject(self._info.process, milliseconds)
            if result == 258:  # WAIT_TIMEOUT
                return None
            _checked(result == 0)
            code = w.DWORD()
            _checked(self._kernel.GetExitCodeProcess(self._info.process, c.byref(code)))
            self._exit = int(code.value)
            return self._exit

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closing = True  # Cleanup attempts irrevocably revoke admission.
            try:
                if self._job:
                    _checked(self._kernel.TerminateJobObject(self._job, 70))
                if self._info.process and self.wait(self._kill_ms / 1000) is None:
                    raise LaunchError("contained cleanup unconfirmed")
                for field in ("thread", "process"):
                    handle = getattr(self._info, field)
                    if handle:
                        _checked(self._kernel.CloseHandle(handle))
                        setattr(self._info, field, None)
                if self._job:
                    _checked(self._kernel.CloseHandle(self._job))
                    self._job = None
                self._closed = True
            except BaseException:
                raise LaunchError("contained cleanup unconfirmed") from None
