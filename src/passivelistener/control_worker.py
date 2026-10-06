"""Bounded diagnostic ready/stop protocol; no capture or caller-selected code."""

import math
import time

from passivelistener.control_event import PrivateEvent
from passivelistener.private_storage import _require_clean
from passivelistener.session_guard import SessionRejected, require_capture_session

# Retain failed event owners until process exit; never silently lose handles.
_QUARANTINE: list[PrivateEvent] = []


def control_worker(ready_name: str, stop_name: str, *, timeout: float = 30) -> int:
    """Signal eligibility, then wait for stop with periodic session/quarantine checks.

    Same-user peers are trusted. Ready means this diagnostic passed the session
    guard, not microphone consent, desktop unlock, or engine readiness. The host
    must retain owner events until reap and forcibly contain/terminate on timeout.
    Returns fixed codes only: 0 stop, 3 ineligible, 64 invalid, 70 fatal, 71 timeout.
    """
    if (ready_name == stop_name or isinstance(timeout, bool)
            or not math.isfinite(timeout) or not 0 < timeout <= 30):
        return 64
    if _QUARANTINE:
        return 70
    ready, stop = PrivateEvent(), PrivateEvent()
    result = 70
    try:
        # Keep both objects even if acquire publishes a handle and then raises.
        ready.acquire(ready_name, create=False, signal=True)
        stop.acquire(stop_name, create=False)
        _require_clean()
        require_capture_session()
        # A stop already requested must never publish readiness.
        if stop.wait():
            result = 0
        else:
            ready.signal()
            deadline = time.monotonic() + timeout
            while True:
                _require_clean()
                require_capture_session()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    result = 71
                    break
                if stop.wait(min(100, max(1, math.ceil(remaining * 1000)))):
                    _require_clean()
                    result = 0
                    break
    except SessionRejected:
        result = 3
    except Exception:
        result = 70
    finally:
        for event in (stop, ready):
            try:
                event.close()
            except BaseException:
                _QUARANTINE.append(event)
                result = 70
    return result
