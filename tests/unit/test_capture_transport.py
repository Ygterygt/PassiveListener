from threading import Event, Thread

import pytest

from passivelistener import capture_consent
from passivelistener import capture_transport as module
from passivelistener.capture_consent import CaptureConsent, ConsentRejected, capture_notice
from passivelistener.configuration import Configuration
from passivelistener.lease_cleanup import LeaseCleanupError


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(capture_consent, 'require_unlocked_session', lambda: None)
    monkeypatch.setattr(capture_consent, '_require_clean', lambda: None)
    config = Configuration()
    consent = CaptureConsent(config, displayed_notice=capture_notice(config), accepted=True)
    return module.CaptureTransport(config, consent, capacity_frames=2)


def test_fifo_sample_offsets_and_admission_clock(transport, monkeypatch):
    ticks = iter([100, 200, 200])
    monkeypatch.setattr(module, 'monotonic_ns', lambda: next(ticks))
    a, b = bytes(module.FRAME_BYTES), b'\x01\x00' * module.FRAME_SAMPLES
    assert transport.read() is None
    transport.offer(a)
    transport.offer(b)
    first = transport.read()
    assert (first.sequence, first.sample_offset, first.admitted_ns, first.pcm) == (0, 0, 100, a)
    assert 'pcm=' not in repr(first)
    transport.offer(a)
    second, third = transport.read(), transport.read()
    assert (second.sequence, second.sample_offset, second.admitted_ns, second.pcm) == (
        1, 320, 200, b)
    assert (third.sequence, third.sample_offset, third.admitted_ns) == (2, 640, 200)
    assert transport.read() is None


@pytest.mark.parametrize('pcm', [b'', bytes(639), bytes(641), bytearray(640), 'PRIVATE'])
def test_malformed_input_revokes_and_purges(transport, pcm):
    transport.offer(bytes(640))
    with pytest.raises(module.CaptureTransportError, match='^capture frame rejected$'):
        transport.offer(pcm)
    assert not transport._frames
    with pytest.raises(ConsentRejected):
        transport.read()


def test_overflow_is_terminal_not_silent_drop(transport):
    transport.offer(bytes(640))
    transport.offer(bytes(640))
    with pytest.raises(module.CaptureTransportError, match='^capture transport continuity lost$'):
        transport.offer(bytes(640))
    assert not transport._frames
    with pytest.raises(ConsentRejected):
        transport.offer(bytes(640))


def test_clock_regression_is_terminal(transport, monkeypatch):
    ticks = iter([200, 100])
    monkeypatch.setattr(module, 'monotonic_ns', lambda: next(ticks))
    transport.offer(bytes(640))
    with pytest.raises(module.CaptureTransportError):
        transport.offer(bytes(640))
    assert not transport._frames


def test_close_idempotent_and_no_buffer_delivery(transport):
    transport.offer(bytes(640))
    transport.close()
    transport.close()
    assert not transport._frames
    with pytest.raises(ConsentRejected):
        transport.read()


@pytest.mark.parametrize('operation', ['offer', 'read'])
def test_fatal_cleanup_is_preserved_and_audio_purged(transport, monkeypatch, operation):
    transport.offer(bytes(640))

    def fail():
        raise LeaseCleanupError('PRIVATE')

    monkeypatch.setattr(capture_consent, '_require_clean', fail)
    with pytest.raises(LeaseCleanupError, match='^capture consent cleanup failed$'):
        if operation == 'offer':
            transport.offer(bytes(640))
        else:
            transport.read()
    assert not transport._frames


@pytest.mark.parametrize('capacity', [0, -1, 251, True, 1.5])
def test_invalid_capacity(transport, capacity):
    with pytest.raises(module.CaptureTransportError):
        module.CaptureTransport(transport._config, transport._consent, capacity_frames=capacity)


@pytest.mark.parametrize('operation', ['offer', 'read'])
def test_close_during_snapshot_does_not_wait_or_deliver(transport, monkeypatch, operation):
    transport.offer(bytes(640))
    entered, release = Event(), Event()
    rejected = []

    def query():
        entered.set()
        assert release.wait(5)

    def run():
        try:
            if operation == 'offer':
                transport.offer(bytes(640))
            else:
                transport.read()
        except ConsentRejected:
            rejected.append(True)

    monkeypatch.setattr(capture_consent, 'require_unlocked_session', query)
    thread = Thread(target=run)
    thread.start()
    try:
        assert entered.wait(5)
        transport.close()
        assert not transport._frames
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert rejected == [True]


def test_revocation_after_dequeue_blocks_delivery(transport, monkeypatch):
    transport.offer(bytes(640))
    calls = 0

    def query():
        nonlocal calls
        calls += 1
        if calls == 2:
            transport._consent.revoke()

    monkeypatch.setattr(capture_consent, 'require_unlocked_session', query)
    with pytest.raises(ConsentRejected):
        transport.read()
    assert not transport._frames



def test_revocation_after_enqueue_purges_admission(transport, monkeypatch):
    calls = 0

    def query():
        nonlocal calls
        calls += 1
        if calls == 2:
            transport._consent.revoke()

    monkeypatch.setattr(capture_consent, 'require_unlocked_session', query)
    with pytest.raises(ConsentRejected):
        transport.offer(bytes(640))
    assert not transport._frames

@pytest.mark.parametrize('operation', ['offer', 'read'])
def test_fatal_post_operation_check_purges_and_preserves_type(transport, monkeypatch, operation):
    transport.offer(bytes(640))
    calls = 0

    def query():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise LeaseCleanupError('PRIVATE')

    monkeypatch.setattr(capture_consent, 'require_unlocked_session', query)
    with pytest.raises(LeaseCleanupError, match='^capture consent cleanup failed$'):
        if operation == 'offer':
            transport.offer(bytes(640))
        else:
            transport.read()
    assert transport._closed
    assert not transport._frames
    assert transport._consent._revoked.is_set()


def test_close_revokes_before_waiting_for_queue_lock(transport, monkeypatch):
    transport.offer(bytes(640))
    revoked = Event()
    original = transport._consent.revoke

    def revoke():
        original()
        revoked.set()

    monkeypatch.setattr(transport._consent, 'revoke', revoke)
    thread = Thread(target=transport.close)
    with transport._lock:
        thread.start()
        assert revoked.wait(5)
        assert transport._consent._revoked.is_set()
        assert thread.is_alive()
    thread.join(5)
    assert not thread.is_alive()
    assert transport._closed
    assert not transport._frames
