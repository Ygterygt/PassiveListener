import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest

from passivelistener import control_host as host
from passivelistener.contained_process import ContainedProcess
from passivelistener.session_guard import SessionRejected, require_capture_session


@pytest.fixture
def fake(monkeypatch, tmp_path):
    ready, stop, child = Mock(), Mock(), Mock()
    ready.wait.return_value = True
    child.wait.return_value = None
    leases = []

    @contextmanager
    def directory(*args, **kwargs):
        leases.append('held')
        yield
        leases.append('released')

    monkeypatch.setattr(host, '_QUARANTINE', [])
    monkeypatch.setattr(host, 'PrivateEvent', Mock(side_effect=[ready, stop]))
    monkeypatch.setattr(host, 'ContainedProcess', Mock(return_value=child))
    monkeypatch.setattr(host, 'private_directory', directory)
    monkeypatch.setattr(host, 'require_capture_session', lambda: None)
    monkeypatch.setattr(host, '_require_clean', lambda: None)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(host.tempfile, 'gettempdir', lambda: str(tmp_path))
    return ready, stop, child, leases


def test_fixed_launch_and_graceful_stop(fake):
    ready, stop, child, leases = fake
    owner = host.ControlHost()
    owner.start()
    args, kwargs = child.start.call_args
    assert args[0] == Path(sys.executable)
    assert args[1][0] == '--internal-worker-control'
    assert len(args[1]) == 3
    assert kwargs['cwd'] == Path(sys.executable).parent
    assert leases == ['held']
    child.wait.return_value = 0
    assert owner.stop() == 0
    stop.signal.assert_called_once_with()
    assert leases == ['held', 'released']
    ready.close.assert_called_once_with()
    owner.close()
    with pytest.raises(RuntimeError):
        owner.start()


def test_reap_failure_retains_events_and_lease_until_retry(fake):
    ready, stop, child, leases = fake
    owner = host.ControlHost()
    owner.start()
    child.close.side_effect = OSError('sensitive')
    with pytest.raises(RuntimeError, match='^control host cleanup unconfirmed$'):
        owner.close()
    ready.close.assert_not_called()
    stop.close.assert_not_called()
    assert leases == ['held']
    assert host._QUARANTINE == [owner]
    child.close.side_effect = None
    owner.close()
    assert leases == ['held', 'released']
    assert host._QUARANTINE == [owner]  # Fatal admission remains revoked.


def test_partial_acquire_is_owned_and_closed(fake):
    ready, stop, child, leases = fake
    stop.acquire.side_effect = OSError('sensitive')
    owner = host.ControlHost()
    with pytest.raises(RuntimeError, match='^control host startup failed$'):
        owner.start()
    child.close.assert_called_once_with()
    ready.close.assert_called_once_with()
    stop.close.assert_called_once_with()
    assert leases == []


def test_event_close_failure_attempts_both_and_retains_lease(fake):
    ready, stop, _, leases = fake
    owner = host.ControlHost()
    owner.start()
    stop.close.side_effect = OSError()
    with pytest.raises(RuntimeError):
        owner.close()
    ready.close.assert_called_once_with()
    assert leases == ['held']
    stop.close.side_effect = None
    owner.close()
    assert leases == ['held', 'released']


@pytest.mark.parametrize('exit_code', [None, 3, 70, 71, 999])
def test_timeout_and_nonzero_exit_force_cleanup(fake, exit_code):
    _, _, child, leases = fake
    owner = host.ControlHost()
    owner.start()
    child.wait.return_value = exit_code
    assert owner.stop(grace_seconds=0.01) == 70
    child.close.assert_called_once_with()
    assert leases[-1] == 'released'


def test_stale_ready_does_not_hide_exited_child(fake):
    _, _, child, _ = fake
    child.wait.return_value = 0
    owner = host.ControlHost()
    with pytest.raises(RuntimeError):
        owner.start()
    child.close.assert_called_once_with()


def test_ready_deadline_forces_cleanup(fake):
    ready, _, child, _ = fake
    ready.wait.return_value = False
    with pytest.raises(RuntimeError):
        host.ControlHost().start(ready_seconds=0.001)
    child.close.assert_called_once_with()


def test_session_loss_before_resume(fake, monkeypatch):
    _, _, child, _ = fake
    monkeypatch.setattr(host, 'require_capture_session',
                        Mock(side_effect=[None, SessionRejected('private')]))
    with pytest.raises(RuntimeError):
        host.ControlHost().start()
    child.resume.assert_not_called()
    child.close.assert_called_once_with()


def test_cleanup_overrides_success(fake):
    _, _, child, _ = fake
    owner = host.ControlHost()
    owner.start()
    child.wait.return_value = 0
    child.close.side_effect = OSError()
    with pytest.raises(RuntimeError, match='cleanup unconfirmed'):
        owner.stop()


@pytest.mark.parametrize('seconds', [True, 0, -1, 31, float('nan'), float('inf')])
def test_invalid_timeout_before_admission(fake, seconds):
    _, _, child, _ = fake
    with pytest.raises(ValueError):
        host.ControlHost().start(ready_seconds=seconds)
    child.start.assert_not_called()


def test_prior_cleanup_failure_rejects_admission(fake, monkeypatch):
    _, _, child, _ = fake
    monkeypatch.setattr(host, '_QUARANTINE', [Mock()])
    with pytest.raises(RuntimeError, match='admission rejected'):
        host.ControlHost().start()
    child.start.assert_not_called()


def test_native_source_transport_under_host_ownership(monkeypatch, tmp_path):
    # Exercise actual host events, private lease, contained child, stop and reap.
    # Only fixed frozen launch is adapted to source-mode Python for this test.
    original = ContainedProcess.start
    def source_start(self, executable, arguments, **kwargs):
        program = 'from passivelistener.bootstrap import dispatch; raise SystemExit(dispatch())'
        original(self, Path(sys.executable), ('-c', program, *arguments),
                 cwd=Path(__file__).resolve().parents[2] / 'src',
                 temporary=kwargs['temporary'])
    monkeypatch.setattr(ContainedProcess, 'start', source_start)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(host.tempfile, 'gettempdir', lambda: str(tmp_path))
    owner = host.ControlHost()
    try:
        try:
            require_capture_session()
        except SessionRejected:
            with pytest.raises(RuntimeError, match='startup failed'):
                owner.start()
            return
        owner.start()
        assert owner.stop() == 0
        assert owner._closed
        assert not owner._temporary.exists()
    finally:
        owner.close()


def test_ready_after_deadline_is_not_admitted(fake, monkeypatch):
    ready, _, child, _ = fake
    ready.wait.side_effect = [False, True, True]
    monkeypatch.setattr(host.time, 'monotonic', Mock(side_effect=[0, 0, 11]))
    with pytest.raises(RuntimeError, match='startup failed'):
        host.ControlHost().start(ready_seconds=10)
    child.close.assert_called_once_with()


def test_stop_signal_failure_still_reaps(fake):
    _, stop, child, leases = fake
    owner = host.ControlHost()
    owner.start()
    stop.signal.side_effect = OSError('private')
    assert owner.stop() == 70
    child.close.assert_called_once_with()
    assert leases == ['held', 'released']


def test_session_loss_while_waiting_reaps(fake, monkeypatch):
    ready, _, child, leases = fake
    ready.wait.return_value = False
    monkeypatch.setattr(host, 'require_capture_session',
                        Mock(side_effect=[None, None, None, SessionRejected('private')]))
    with pytest.raises(RuntimeError, match='startup failed'):
        host.ControlHost().start()
    child.close.assert_called_once_with()
    assert leases == ['held', 'released']


def test_cancellation_before_start_prevents_creation(fake):
    _, _, child, _ = fake
    owner = host.ControlHost()
    owner.request_cancel()
    with pytest.raises(RuntimeError, match='admission rejected'):
        owner.start()
    child.start.assert_not_called()
    owner.close()


def test_cancellation_during_creation_prevents_resume(fake):
    _, _, child, leases = fake
    owner = host.ControlHost()
    child.start.side_effect = lambda *args, **kwargs: owner.request_cancel()
    with pytest.raises(RuntimeError, match='startup failed'):
        owner.start()
    child.resume.assert_not_called()
    child.close.assert_called_once_with()
    assert leases == ['held', 'released']


def test_cancellation_during_ready_probe_prevents_admission(fake):
    ready, _, child, _ = fake
    owner = host.ControlHost()
    def cancel_and_ready(*args):
        owner.request_cancel()
        return True
    ready.wait.side_effect = cancel_and_ready
    with pytest.raises(RuntimeError, match='startup failed'):
        owner.start()
    assert not owner._running
    child.close.assert_called_once_with()


def test_close_revokes_waiting_startup_before_lifecycle_lock(fake):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    ready, _, child, leases = fake
    owner = host.ControlHost()
    waiting, release = Event(), Event()
    def wait(milliseconds=0):
        if milliseconds:
            waiting.set()
            assert release.wait(2)
        return False
    ready.wait.side_effect = wait
    with ThreadPoolExecutor(max_workers=2) as pool:
        startup = pool.submit(owner.start)
        try:
            assert waiting.wait(2)
            closing = pool.submit(owner.close)
            assert owner._cancelled.wait(2)
        finally:
            release.set()
        with pytest.raises(RuntimeError, match='startup failed'):
            startup.result(timeout=2)
        closing.result(timeout=2)
    child.close.assert_called_once_with()
    assert leases == ['held', 'released']


def test_cancelled_startup_preserves_failed_cleanup_owner(fake):
    _, _, child, leases = fake
    owner = host.ControlHost()
    child.start.side_effect = lambda *args, **kwargs: owner.request_cancel()
    child.close.side_effect = OSError('private')
    with pytest.raises(RuntimeError, match='cleanup unconfirmed'):
        owner.start()
    assert host._QUARANTINE == [owner]
    assert leases == ['held']
    child.close.side_effect = None
    owner.close()
    assert leases == ['held', 'released']


def test_cancel_after_admission_requires_explicit_close(fake):
    ready, stop, child, leases = fake
    owner = host.ControlHost()
    owner.start()
    owner.request_cancel()
    assert owner._running
    child.close.assert_not_called()
    ready.close.assert_not_called()
    stop.close.assert_not_called()
    assert leases == ['held']
    owner.close()
    assert not owner._running
    child.close.assert_called_once_with()
    assert leases == ['held', 'released']
    with pytest.raises(RuntimeError, match='admission rejected'):
        owner.start()


def test_cancel_during_resume_reaps_before_readiness(fake):
    ready, _, child, leases = fake
    owner = host.ControlHost()
    child.resume.side_effect = owner.request_cancel
    with pytest.raises(RuntimeError, match='startup failed'):
        owner.start()
    ready.wait.assert_not_called()
    child.close.assert_called_once_with()
    assert leases == ['held', 'released']
