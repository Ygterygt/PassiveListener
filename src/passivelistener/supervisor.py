"""One-shot process ownership for trusted user-session native worker code.

No configuration plugins, shell commands, automatic restarts or privileged IPC.
This primitive is not yet wired to capture, SCM or a frozen executable.
"""

import math
import multiprocessing as mp
import os
import sys
from collections.abc import Callable
from enum import StrEnum
from multiprocessing.synchronize import Event
from threading import Lock
from uuid import uuid4

from passivelistener.worker_job import WorkerJob, enter_worker_job

Worker = Callable[[Event], None]
_FAILED = 70


class WorkerResult(StrEnum):
    STOPPED = "worker_stopped"
    FAILED = "worker_failed"
    TERMINATED = "worker_terminated"


class SupervisorError(RuntimeError):
    """Only fixed content-free messages cross the operational boundary."""


def _entry(worker: Worker, stop: Event, admitted: Event, job_name: str) -> None:
    # Discard Python and C-runtime stdout/stderr before running trusted backend
    # code. No exception text, traceback, transcript or model path goes to host logs.
    # Win32 direct-handle logging must separately be disabled in a real backend.
    code = _FAILED
    try:
        with open(os.devnull, "w") as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            sys.stdout = sink
            sys.stderr = sink
            enter_worker_job(job_name)
            if not admitted.wait(10):
                raise SupervisorError("worker admission expired")
            worker(stop)
            code = 0
    except BaseException:
        pass
    # Skip Python finalizers / atexit / non-daemon joins: failed native cleanup
    # may retain live consumers. The OS reclaims all handles on process exit.
    # A successful worker MUST explicitly flush/close persistence before return.
    os._exit(code)


def _timeout(value: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 300:
        raise ValueError("worker timeout rejected")
    return value


class WorkerSupervisor:
    """Own exactly one spawned child; never reuse an uncertain native process.

    Call from the interactive user's host, never from a privileged service with
    user-supplied callables. The callable is compile-time trusted application code.
    The host must call stop in a finally block. Each join is bounded; failure to
    confirm death raises and retains ownership instead of declaring cleanup safe.
    """

    def __init__(self, worker: Worker, *, grace_seconds: float = 5,
                 kill_seconds: float = 5) -> None:
        self._grace = _timeout(grace_seconds)
        self._kill = _timeout(kill_seconds)
        context = mp.get_context("spawn")
        self._stop = context.Event()
        self._admitted = context.Event()
        self._job_name = "Local\\PassiveListener-" + uuid4().hex
        self._job: WorkerJob | None = None
        self._process = context.Process(
            target=_entry, args=(worker, self._stop, self._admitted, self._job_name),
        )
        self._lock = Lock()
        self._attempted = False
        self._started = False
        self._forced = False
        self._job_cleanup_pending = False
        self._result: WorkerResult | None = None

    def start(self) -> None:
        with self._lock:
            if self._attempted:
                raise SupervisorError("worker start already attempted")
            self._attempted = True
            try:
                self._job = WorkerJob(self._job_name)
                self._job.open()
                self._process.start()
                self._started = True
                self._admitted.set()
            except BaseException:
                # A start wrapper may raise after publishing a process handle.
                # Retain it for explicit stop/reap; no worker was admitted.
                self._started = self._process.pid is not None
                if self._job is not None:
                    try:
                        self._job.close()
                    except BaseException:
                        self._job_cleanup_pending = True
                        raise SupervisorError("worker containment cleanup failed") from None
                raise SupervisorError("worker start failed") from None

    def stop(self) -> WorkerResult:
        with self._lock:
            if self._result is not None:
                return self._result
            if not self._started:
                if self._job_cleanup_pending and self._job is not None:
                    try:
                        self._job.close()
                    except BaseException:
                        raise SupervisorError("worker containment cleanup failed") from None
                    self._job_cleanup_pending = False
                    self._result = WorkerResult.FAILED
                    return self._result
                raise SupervisorError("worker not started")
            try:
                self._stop.set()
                self._process.join(self._grace)
                if self._process.is_alive():
                    self._forced = True
                    self._process.terminate()
                    self._process.join(self._kill)
                if self._process.is_alive():
                    raise SupervisorError("worker termination unconfirmed")
                exitcode = self._process.exitcode
                if exitcode is None:
                    raise SupervisorError("worker termination unconfirmed")
                result = (WorkerResult.TERMINATED if self._forced else
                          WorkerResult.STOPPED if exitcode == 0 else WorkerResult.FAILED)
                if self._job is not None:
                    self._job.close()
                self._process.close()
            except BaseException:
                # Never expose native/process diagnostics. A failed stop retains
                # the handle for a later explicit stop; start remains prohibited.
                raise SupervisorError("worker termination unconfirmed") from None
            self._result = result
            return result
