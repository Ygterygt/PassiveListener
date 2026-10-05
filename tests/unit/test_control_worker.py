import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from passivelistener import bootstrap
from passivelistener import control_worker as worker
from passivelistener.contained_process import ContainedProcess
from passivelistener.control_event import PrivateEvent, event_name
from passivelistener.session_guard import SessionRejected, require_capture_session


@pytest.fixture
def pair():
    ready, stop = PrivateEvent(), PrivateEvent()
    names = event_name(), event_name()
    try:
        ready.acquire(names[0], create=True)
        stop.acquire(names[1], create=True)
        yield ready, stop, names
    finally:
        stop.close()
        ready.close()


def test_real_contained_child_ready_stop(pair):
    ready, stop, names = pair
    eligible = True
    try:
        require_capture_session()
    except SessionRejected:
        eligible = False
    child = ContainedProcess()
    try:
        # Source-mode equivalent of fixed dispatch; no microphone is opened.
        program = ('from passivelistener.bootstrap import dispatch; '
                   'raise SystemExit(dispatch())')
        child.start(Path(sys.executable),
                    ('-c', program, bootstrap.CONTROL_ARGUMENT, *names),
                    cwd=Path(__file__).resolve().parents[2] / 'src')
        child.resume()
        if not eligible:
            assert child.wait(10) == 3
            assert not ready.wait()
            return
        deadline = time.monotonic() + 10
        while not ready.wait(100):
            code = child.wait(0)
            assert code is None, f'child exited before ready: {code}'
            assert time.monotonic() < deadline
        stop.signal()
        assert child.wait(5) == 0
    finally:
        child.close()


def test_stop_before_ready(pair, monkeypatch):
    ready, stop, names = pair
    monkeypatch.setattr(worker, 'require_capture_session', lambda: None)
    stop.signal()
    assert worker.control_worker(*names) == 0
    assert not ready.wait()


def test_timeout_is_bounded(pair, monkeypatch):
    ready, _, names = pair
    monkeypatch.setattr(worker, 'require_capture_session', lambda: None)
    assert worker.control_worker(*names, timeout=0.02) == 71
    assert ready.wait()


def test_session_loss_after_ready(pair, monkeypatch):
    ready, _, names = pair
    monkeypatch.setattr(worker, 'require_capture_session',
                        Mock(side_effect=[None, SessionRejected('sensitive')]))
    assert worker.control_worker(*names) == 3
    assert ready.wait()


def test_quarantine_after_ready_is_fatal(pair, monkeypatch):
    ready, _, names = pair
    monkeypatch.setattr(worker, 'require_capture_session', lambda: None)
    monkeypatch.setattr(worker, '_require_clean',
                        Mock(side_effect=[None, OSError('sensitive')]))
    assert worker.control_worker(*names) == 70
    assert ready.wait()


def test_rejected_session_never_ready(pair, monkeypatch):
    ready, _, names = pair
    monkeypatch.setattr(worker, 'require_capture_session',
                        Mock(side_effect=SessionRejected('sensitive')))
    assert worker.control_worker(*names) == 3
    assert not ready.wait()


def test_acquire_failure_closes_both_published_owners(monkeypatch):
    ready, stop = Mock(), Mock()
    stop.acquire.side_effect = OSError('private')
    monkeypatch.setattr(worker, 'PrivateEvent', Mock(side_effect=[ready, stop]))
    assert worker.control_worker(event_name(), event_name()) == 70
    ready.close.assert_called_once_with()
    stop.close.assert_called_once_with()
    ready.signal.assert_not_called()


def test_combined_close_failure_retains_both(monkeypatch):
    ready, stop = Mock(), Mock()
    ready.acquire.side_effect = OSError('private')
    ready.close.side_effect = OSError('private')
    stop.close.side_effect = OSError('private')
    quarantine = []
    monkeypatch.setattr(worker, '_QUARANTINE', quarantine)
    monkeypatch.setattr(worker, 'PrivateEvent', Mock(side_effect=[ready, stop]))
    assert worker.control_worker(event_name(), event_name()) == 70
    assert quarantine == [stop, ready]


@pytest.mark.parametrize('timeout', [0, -1, 31, float('nan'), float('inf'), True])
def test_invalid_timeout(timeout):
    assert worker.control_worker(event_name(), event_name(), timeout=timeout) == 64


def test_same_event_rejected():
    name = event_name()
    assert worker.control_worker(name, name) == 64


@pytest.mark.parametrize('args', [[], ['one'], ['one', 'two', 'extra']])
def test_dispatch_exact_arity(monkeypatch, args):
    monkeypatch.setattr(sys, 'argv', ['app', bootstrap.CONTROL_ARGUMENT, *args])
    assert bootstrap.dispatch() == 64


def test_dispatch_only_names(monkeypatch):
    names = [event_name(), event_name()]
    target = Mock(return_value=71)
    monkeypatch.setattr(worker, 'control_worker', target)
    monkeypatch.setattr(sys, 'argv', ['app', bootstrap.CONTROL_ARGUMENT, *names])
    assert bootstrap.dispatch() == 71
    target.assert_called_once_with(*names)


def test_native_post_publication_failure_releases_both_handles(pair, monkeypatch):
    from passivelistener.control_event import _api

    _, _, names = pair
    acquire = PrivateEvent.acquire
    handles = []

    def fail_after_publish(self, name, **kwargs):
        acquire(self, name, **kwargs)
        handles.append(self._handle)
        if len(handles) == 2:
            raise OSError('descriptor cleanup failed')

    monkeypatch.setattr(PrivateEvent, 'acquire', fail_after_publish)
    assert worker.control_worker(*names) == 70
    assert len(handles) == 2
    for handle in handles:
        assert _api().WaitForSingleObject(handle, 0) == 0xFFFFFFFF


def test_cleanup_failure_overrides_success(monkeypatch):
    ready, stop = Mock(), Mock()
    stop.wait.return_value = True
    stop.close.side_effect = OSError('private')
    quarantine = []
    monkeypatch.setattr(worker, '_QUARANTINE', quarantine)
    monkeypatch.setattr(worker, 'require_capture_session', lambda: None)
    monkeypatch.setattr(worker, 'PrivateEvent', Mock(side_effect=[ready, stop]))
    assert worker.control_worker(event_name(), event_name()) == 70
    ready.close.assert_called_once_with()
    assert quarantine == [stop]


def test_prior_event_cleanup_failure_rejects_new_admission(monkeypatch):
    monkeypatch.setattr(worker, '_QUARANTINE', [Mock()])
    factory = Mock()
    monkeypatch.setattr(worker, 'PrivateEvent', factory)
    assert worker.control_worker(event_name(), event_name()) == 70
    factory.assert_not_called()


def test_quarantine_before_ready_never_signals(pair, monkeypatch):
    ready, _, names = pair
    monkeypatch.setattr(worker, '_require_clean', Mock(side_effect=OSError('private')))
    assert worker.control_worker(*names) == 70
    assert not ready.wait()
