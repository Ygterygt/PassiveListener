"""Own model leases for a native backend, including partial initialization.

Backend implementations are trusted application code, never configuration plugins.
No engine, DLL discovery, audio capture or network operation is implemented here.
"""

from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Lock
from typing import Protocol

from passivelistener.models import MODEL_PINS, verified_model


class NativeBackend(Protocol):
    def open(self, whisper: Path, vad: Path) -> None:
        """Initialize synchronously; close must also handle partial initialization."""

    def transcribe(self, samples: Sequence[float]) -> str:
        """Consume 16 kHz mono samples synchronously; retain no caller buffer."""

    def close(self) -> None:
        """Join native work and release every model consumer before returning."""


class NativeLifetimeError(RuntimeError):
    """Content-free lifecycle failure safe for operational logging."""


# A failed native destructor cannot prove that model consumers have stopped.
# Retain leases AND backend until process exit; never retry a potentially partial
# destructor or let garbage collection silently release the protection.
_QUARANTINE: list[tuple[ExitStack, NativeBackend]] = []
_QUARANTINE_LOCK = Lock()


class NativeSession:
    """Serialize inference and shutdown. Never expose the backend to callers."""

    def __init__(self, backend: NativeBackend, leases: ExitStack) -> None:
        self._backend = backend
        self._leases = leases
        self._lock = Lock()
        self._closed = False

    def transcribe(self, samples: Sequence[float]) -> str:
        with self._lock:
            if self._closed:
                raise NativeLifetimeError("native session closed")
            return self._backend.transcribe(samples)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._backend.close()
            except BaseException:
                with _QUARANTINE_LOCK:
                    _QUARANTINE.append((self._leases, self._backend))
                raise NativeLifetimeError("native cleanup failed; restart worker") from None
            self._leases.close()


@contextmanager
def native_session(directory: Path, backend: NativeBackend) -> Iterator[NativeSession]:
    """Verify BOTH models before native initialization; retain through cleanup.

    The caller supplies a fresh, unopened backend. A consumer must use compatible
    read sharing. Cleanup failure requires worker termination; it deliberately
    retains protection until process exit instead of claiming safe recovery.
    """
    leases = ExitStack()
    try:
        for model_id in ("whisper-base", "silero-v6"):
            leases.enter_context(verified_model(model_id, directory))
    except BaseException:
        leases.close()
        raise
    session = NativeSession(backend, leases)
    try:
        backend.open(directory / MODEL_PINS["whisper-base"].filename,
                     directory / MODEL_PINS["silero-v6"].filename)
        yield session
    finally:
        session.close()
