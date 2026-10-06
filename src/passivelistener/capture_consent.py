"""Ephemeral capture consent for trusted same-user foreground callers.

Not a security token, persistence format, IPC capability, or proof of human input.
The production UI must display capture_notice and obtain an explicit decision.
"""

import json
import os
from dataclasses import asdict
from threading import Event

from passivelistener.configuration import Configuration, parse_configuration
from passivelistener.lease_cleanup import LeaseCleanupError
from passivelistener.private_storage import SecurityCleanupError, _require_clean
from passivelistener.session_unlock import require_unlocked_session


class ConsentRejected(RuntimeError):
    """Fixed diagnostic safe for operational logs."""


def _settings(config: Configuration) -> bytes:
    data = json.dumps(asdict(config), sort_keys=True, ensure_ascii=True).encode('utf-8')
    parse_configuration(data)
    return data


def capture_notice(config: Configuration) -> str:
    """Display locally; contains selected local paths and must not be logged."""
    return (
        'PassiveListener microphone capture consent v1\n'
        'Allow microphone capture and offline Turkish transcription for this run. '
        'UTF-8 transcripts remain on this computer in the selected output directory; '
        'archives may retain them after this run ends. No audio/transcript egress, '
        'external prompt routing, wake-word activation or TTS is authorized. '
        'Stop, session loss or settings changes require a new decision.\n'
        'Selected settings: ' + _settings(config).decode('utf-8')
    )


class CaptureConsent:
    """One-process, one-run revocable decision bound to exact validated settings.

    Construct only after displaying the notice and obtaining explicit consent.
    Check before device open and each capture iteration. Rejection permanently
    revokes this decision; a later unlock never revives it. Native checks are
    snapshots, so the host still owns session notifications, stop and reap.
    No device is opened here; passing a check does not atomically admit capture.
    """

    def __init__(self, config: Configuration, *, displayed_notice: str,
                 accepted: bool) -> None:
        self._revoked = Event()
        self._pid = os.getpid()
        self._settings = _settings(config)
        if accepted is not True or displayed_notice != capture_notice(config):
            self._revoked.set()
            raise ConsentRejected('capture consent rejected')
        self.require(config)

    def revoke(self) -> None:
        """Nonblocking signal, safe while a native eligibility query is pending."""
        self._revoked.set()

    def require(self, config: Configuration) -> None:
        try:
            _require_clean()
            if (self._revoked.is_set() or os.getpid() != self._pid
                    or _settings(config) != self._settings):
                raise ConsentRejected('capture consent rejected')
            require_unlocked_session()
            _require_clean()
            # A concurrent stop during the native query must not pass admission.
            if self._revoked.is_set():
                raise ConsentRejected('capture consent rejected')
        except (SecurityCleanupError, LeaseCleanupError) as error:
            self.revoke()
            raise type(error)('capture consent cleanup failed') from None
        except BaseException:
            self.revoke()
            raise ConsentRejected('capture consent rejected') from None
