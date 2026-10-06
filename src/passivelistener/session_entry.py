"""Foreground diagnostic entrypoint; acknowledgement is not capture consent."""

import os
import signal
import sys
from collections.abc import Callable
from threading import Event, current_thread, main_thread
from types import FrameType

from passivelistener.lease_cleanup import require_clean as require_clean_leases
from passivelistener.session_runtime import SessionRuntime

NOTICE = (
    'PassiveListener session diagnostic: no microphone or transcription. '
    'Starts a contained user-session worker; stops on Ctrl+C or session revocation. '
    'This acknowledgement does not authorize future audio capture.'
)


def run_session_diagnostic(acknowledged: bool) -> int:
    """Fixed foreground lifecycle. Cleanup uncertainty terminates this process.

    No persisted consent, unattended startup, restart, or configurable dispatch.
    Signal callbacks only set an Event; native cleanup stays on the window thread.
    """
    print(NOTICE, flush=True)
    if acknowledged is not True or current_thread() is not main_thread():
        return 64
    stop = Event()
    runtime: SessionRuntime | None = None
    previous: dict[signal.Signals, Callable[[int, FrameType | None], object] | int | None] = {}
    result = 70

    def request_stop(signum: int, frame: FrameType | None) -> None:
        stop.set()

    try:
        for kind in (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK):
            previous[kind] = signal.signal(kind, request_stop)
        runtime = SessionRuntime()
        result = runtime.run(stop)
    except BaseException:
        result = 70
    finally:
        if runtime is not None:
            try:
                runtime.close()
                # A prior failed release may have drained ExitStack callbacks.
                # Successful retry cannot clear retained lease quarantine.
                require_clean_leases()
            except BaseException:
                # Do not release a live child's resources or print native exceptions.
                # OS process teardown closes the non-inherited job handle.
                os._exit(70)
        for restored_kind, handler in previous.items():
            try:
                signal.signal(restored_kind, handler)
            except BaseException:
                result = 70
    print(f'session diagnostic result: {result}', file=sys.stderr)
    return result

