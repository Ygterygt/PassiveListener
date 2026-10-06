"""Local foreground consent interaction; never use redirected streams or logs."""

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Event, current_thread, main_thread

from passivelistener.capture_consent import CaptureConsent, ConsentRejected, capture_notice
from passivelistener.configuration import Configuration
from passivelistener.lease_cleanup import LeaseCleanupError
from passivelistener.private_storage import SecurityCleanupError, _require_clean


@contextmanager
def foreground_consent(config: Configuration, stop: Event) -> Iterator[CaptureConsent]:
    """Display exact settings and grant only an explicit, bounded terminal answer.

    Caller must pump session events and check consent before open/each iteration.
    Terminal input is blocking: stop is checked after input, not a prompt deadline.
    TTY checks prevent accidental redirection, not malicious same-user automation.
    Fatal cleanup errors propagate: the production caller must exit its process.
    The decision is always revoked when the caller leaves this scope.
    """
    consent: CaptureConsent | None = None
    try:
        _require_clean()
        if (current_thread() is not main_thread() or stop.is_set()
                or sys.stdin is None or sys.stdout is None
                or not sys.stdin.isatty() or not sys.stdout.isatty()):
            raise ConsentRejected('capture consent rejected')
        notice = capture_notice(config)
        prompt = notice + '\nType ALLOW to enable capture for this run: '
        if sys.stdout.write(prompt) != len(prompt):
            raise ConsentRejected('capture consent rejected')
        sys.stdout.flush()
        answer = sys.stdin.readline(16)
        _require_clean()
        if stop.is_set() or answer not in ('ALLOW\n', 'ALLOW\r\n'):
            raise ConsentRejected('capture consent rejected')
        consent = CaptureConsent(config, displayed_notice=notice, accepted=True)
        if stop.is_set():
            raise ConsentRejected('capture consent rejected')
    except (SecurityCleanupError, LeaseCleanupError):
        if consent is not None:
            consent.revoke()
        raise
    except BaseException:
        if consent is not None:
            consent.revoke()
        raise ConsentRejected('capture consent rejected') from None
    try:
        yield consent
    finally:
        consent.revoke()
