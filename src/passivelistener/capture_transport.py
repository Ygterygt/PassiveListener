"""Bounded in-process PCM transport. No device, disk, network or inference I/O."""

from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic_ns

from passivelistener.capture_consent import CaptureConsent
from passivelistener.configuration import Configuration

SAMPLE_RATE = 16000
FRAME_SAMPLES = 320
FRAME_BYTES = FRAME_SAMPLES * 2


class CaptureTransportError(RuntimeError):
    """Fixed operational diagnostic; never includes PCM or caller values."""


@dataclass(frozen=True)
class PcmFrame:
    """20 ms mono signed int16 little-endian PCM; timestamp is queue admission.

    This timestamp is NOT microphone onset or device-clock latency evidence.
    PCM is omitted from repr, but is still sensitive in-memory data.
    """

    sequence: int
    sample_offset: int
    admitted_ns: int
    pcm: bytes = field(repr=False)


class CaptureTransport:
    """Single-run FIFO; overflow or malformed PCM permanently closes admission.

    No waiting for buffer space. Consent checks perform native snapshot calls,
    so offer/read are NOT real-time audio callbacks. A device adapter must own
    callback scheduling, framing/resampling and stop/reap. Only fixed 16 kHz
    mono PCM is accepted. Stop clears references, not guaranteed memory erasure.
    Caller owns already delivered frames and must stop inference on revocation.
    """

    def __init__(self, config: Configuration, consent: CaptureConsent,
                 *, capacity_frames: int = 50) -> None:
        if type(capacity_frames) is not int or not 1 <= capacity_frames <= 250:
            raise CaptureTransportError('capture transport configuration rejected')
        consent.require(config)
        self._config = config
        self._consent = consent
        self._capacity = capacity_frames
        self._frames: deque[PcmFrame] = deque()
        self._lock = Lock()
        self._closed = False
        self._sequence = 0
        self._last_ns = -1

    def close(self) -> None:
        # Revoke before waiting for queue ownership; no native query under lock.
        self._consent.revoke()
        with self._lock:
            self._closed = True
            self._frames.clear()

    def _guard(self) -> None:
        try:
            self._consent.require(self._config)
        except BaseException:
            self.close()
            raise

    def offer(self, pcm: bytes) -> None:
        """Accept exactly one frame. Never silently drop audio on overrun."""
        self._guard()
        if type(pcm) is not bytes or len(pcm) != FRAME_BYTES:
            self.close()
            raise CaptureTransportError('capture frame rejected')
        with self._lock:
            if self._closed:
                raise CaptureTransportError('capture transport closed')
            now = monotonic_ns()
            if len(self._frames) >= self._capacity or now < self._last_ns:
                self._closed = True
                self._frames.clear()
                self._consent.revoke()
                raise CaptureTransportError('capture transport continuity lost')
            self._frames.append(PcmFrame(self._sequence, self._sequence * FRAME_SAMPLES,
                                         now, pcm))
            self._sequence += 1
            self._last_ns = now
        # A stop observed during admission also purges pending frames.
        self._guard()

    def read(self) -> PcmFrame | None:
        """Return oldest frame or None. Revocation rejects even an empty queue."""
        self._guard()
        with self._lock:
            if self._closed:
                raise CaptureTransportError('capture transport closed')
            frame = self._frames.popleft() if self._frames else None
        self._guard()
        return frame
