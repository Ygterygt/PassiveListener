"""Diagnostic host orchestration; no capture, consent, or engine admission."""

from threading import Event, Thread, get_ident

from passivelistener.control_host import ControlHost
from passivelistener.session_window import SessionWindow

_QUARANTINE: list['SessionRuntime'] = []


class SessionRuntime:
    """One-shot caller-thread window pump with separate host startup.

    External stop is a threading.Event, not an arbitrary command transport.
    Native calls and host close remain cooperative and may block. Cleanup
    uncertainty is fatal: retain this owner and exit the hosting process.
    Retry close only on the original window thread; quarantine never readmits.
    """

    def __init__(self) -> None:
        self._host = ControlHost()
        self._window = SessionWindow(self._host)
        self._startup = Thread(target=self._start, name='PassiveListener-start', daemon=True)
        self._done = Event()
        self._failed = False
        self._attempted = False
        self._closed = False
        self._thread: int | None = None

    def _start(self) -> None:
        try:
            self._host.start()
        except BaseException:
            # Do not retain traceback or print potentially private backend diagnostics.
            self._failed = True
            self._host.request_cancel()
        finally:
            self._done.set()

    def run(self, stop: Event) -> int:
        """Return fixed 0 for requested stop/revocation, 70 for operational failure.

        Registration precedes startup. Pumping continues during startup and after
        readiness; diagnostic readiness is never exposed as capture authorization.
        Cleanup errors override results. No automatic restart after revocation.
        """
        if self._attempted or self._closed or _QUARANTINE:
            raise RuntimeError('session runtime admission rejected')
        self._attempted = True
        self._thread = get_ident()
        result = 70
        try:
            if stop.is_set():
                return 0
            self._window.acquire()
            if stop.is_set():
                return 0
            self._startup.start()
            while True:
                if stop.is_set():
                    result = 0
                    break
                if not self._window.pump():
                    result = 0
                    break
                if self._done.is_set() and self._failed:
                    break
                stop.wait(0.01)
        except BaseException:
            result = 70
        finally:
            self.close()
        return result

    def close(self) -> None:
        """Cancel, reap through window owner, then confirm startup thread exit."""
        self._host.request_cancel()
        if self._closed:
            return
        try:
            if self._thread is not None and self._thread != get_ident():
                raise RuntimeError('session runtime thread required')
            self._window.close()
            # Thread.start may raise after publishing a thread. Never lose it.
            if self._startup.ident is not None:
                self._startup.join(5)
                if self._startup.is_alive():
                    raise RuntimeError('startup thread exit unconfirmed')
            self._closed = True
        except BaseException:
            if self not in _QUARANTINE:
                _QUARANTINE.append(self)
            raise RuntimeError('session runtime cleanup unconfirmed') from None
