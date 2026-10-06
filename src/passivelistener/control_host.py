"""One-shot user host ownership for the fixed diagnostic control worker."""

import math
import sys
import tempfile
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from threading import RLock

from passivelistener.bootstrap import CONTROL_ARGUMENT
from passivelistener.contained_process import ContainedProcess
from passivelistener.control_event import PrivateEvent, event_name
from passivelistener.private_storage import _require_clean, private_directory
from passivelistener.session_guard import require_capture_session

# Failed owners must survive caller stack unwinding. Process exit is fatal policy.
_QUARANTINE: list['ControlHost'] = []


def _timeout(seconds: float) -> None:
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 30:
        raise ValueError('control host timeout rejected')


class ControlHost:
    """Trusted installed frozen EXE only; never a service or arbitrary-command API.

    Caller serializes lifecycle on its host thread. close() can retry retained
    cleanup but never restore admission. No destructor hides failed cleanup.
    start() readiness is diagnostic eligibility, not consent or engine health.
    A session notifier must call close on lock/disconnect; not wired here yet.
    """

    def __init__(self) -> None:
        self._ready = PrivateEvent()
        self._stop = PrivateEvent()
        self._child = ContainedProcess()
        self._leases = ExitStack()
        self._temporary: Path | None = None
        self._attempted = False
        self._closed = False
        self._closing = False
        self._running = False
        self._lock = RLock()

    def start(self, *, ready_seconds: float = 10) -> None:
        _timeout(ready_seconds)
        with self._lock:
            if self._attempted or self._closing or self._closed or _QUARANTINE:
                raise RuntimeError('control host admission rejected')
            self._attempted = True
            try:
                _require_clean()
                require_capture_session()
                executable = Path(sys.executable)
                if not getattr(sys, 'frozen', False) or not executable.is_absolute():
                    raise RuntimeError('installed frozen host required')
                names = event_name(), event_name()
                self._ready.acquire(names[0], create=True)
                self._stop.acquire(names[1], create=True)
                self._temporary = Path(tempfile.gettempdir()) / (
                    'PassiveListener-' + uuid.uuid4().hex)
                self._leases.enter_context(private_directory(self._temporary, create=True))
                self._child.start(executable, (CONTROL_ARGUMENT, *names),
                                  cwd=executable.parent, temporary=self._temporary)
                _require_clean()
                require_capture_session()
                self._child.resume()
                deadline = time.monotonic() + ready_seconds
                while True:
                    _require_clean()
                    require_capture_session()
                    if self._child.wait(0) is not None:
                        raise RuntimeError('control worker exited before admission')
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError('control worker readiness timeout')
                    if self._ready.wait():
                        self._running = True
                        return
                    self._ready.wait(min(100, max(1, math.ceil(remaining * 1000))))
            except BaseException:
                self.close()
                raise RuntimeError('control host startup failed') from None

    def stop(self, *, grace_seconds: float = 5) -> int:
        """Request graceful diagnostic stop, then force cleanup even on failure.

        Fixed result 0 requires worker exit 0; all other exits/timeouts return 70.
        Cleanup uncertainty raises and retains ownership, overriding that result.
        """
        _timeout(grace_seconds)
        with self._lock:
            if not self._running or self._closing or self._closed:
                raise RuntimeError('control host not running')
            result = 70
            try:
                _require_clean()
                self._stop.signal()
                if self._child.wait(grace_seconds) == 0:
                    result = 0
            except Exception:
                result = 70
            finally:
                self.close()
            return result

    def close(self) -> None:
        """Reap child before releasing events/extraction lease; retain on failure."""
        with self._lock:
            if self._closed:
                return
            self._closing = True
            self._running = False
            try:
                # A failed reap forbids any event or extraction lease release.
                self._child.close()
                failed = False
                for event in (self._stop, self._ready):
                    try:
                        event.close()
                    except BaseException:
                        failed = True
                if failed:
                    raise RuntimeError('control event cleanup unconfirmed')
                self._leases.close()
                if self._temporary is not None:
                    try:
                        self._temporary.rmdir()  # Empty only; preserve crash leftovers.
                    except OSError:
                        pass
                self._closed = True
            except BaseException:
                if self not in _QUARANTINE:
                    _QUARANTINE.append(self)
                raise RuntimeError('control host cleanup unconfirmed') from None
