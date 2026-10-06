import ctypes as c
from ctypes import wintypes as w
from threading import Event, Thread, get_ident
from unittest.mock import Mock

import pytest

from passivelistener import session_runtime as sr


@pytest.fixture(autouse=True)
def clean():
    sr._QUARANTINE.clear()
    yield
    sr._QUARANTINE.clear()


@pytest.fixture
def fake(monkeypatch):
    host, window = Mock(), Mock()
    window.close.side_effect = host.close
    monkeypatch.setattr(sr, 'ControlHost', lambda: host)
    monkeypatch.setattr(sr, 'SessionWindow', lambda value: window)
    return host, window


def test_stop_before_start_never_registers_or_launches(fake):
    stop = Event()
    stop.set()
    owner = sr.SessionRuntime()
    assert owner.run(stop) == 0
    fake[1].acquire.assert_not_called()
    fake[0].start.assert_not_called()
    fake[0].close.assert_called_once()
    with pytest.raises(RuntimeError, match='admission rejected'):
        owner.run(stop)


def test_registration_precedes_start_and_pump_runs_during_start(fake):
    host, window = fake
    started, pumped, cancelled = Event(), Event(), Event()
    caller = get_ident()
    ids = []

    def start():
        window.acquire.assert_called_once()
        ids.append(get_ident())
        started.set()
        assert pumped.wait(2)
        assert cancelled.wait(2)

    def pump():
        assert get_ident() == caller
        assert started.wait(2)
        pumped.set()
        return False

    host.start.side_effect = start
    host.request_cancel.side_effect = cancelled.set
    window.pump.side_effect = pump
    owner = sr.SessionRuntime()
    assert owner.run(Event()) == 0
    assert ids != [caller]
    assert owner._done.is_set() and not owner._startup.is_alive()
    assert not owner._failed


def test_successful_start_keeps_pumping_until_stop(fake):
    stop = Event()
    owner = sr.SessionRuntime()

    def pump():
        assert owner._done.wait(2)
        stop.set()
        return True

    fake[1].pump.side_effect = pump
    assert owner.run(stop) == 0
    fake[0].close.assert_called_once()


@pytest.mark.parametrize('failure', ['register', 'start', 'pump', 'thread_start'])
def test_failures_sanitized_and_closed(fake, failure, capsys):
    owner = sr.SessionRuntime()
    target = {'register': fake[1].acquire, 'start': fake[0].start,
              'pump': fake[1].pump}.get(failure)
    if target is not None:
        target.side_effect = RuntimeError('private diagnostic')
    else:
        owner._startup.start = Mock(side_effect=RuntimeError('private diagnostic'))
    assert owner.run(Event()) == 70
    fake[0].close.assert_called_once()
    assert not capsys.readouterr().err


def test_published_thread_survives_start_exception_until_reaped(fake):
    owner = sr.SessionRuntime()
    original = owner._startup.start

    def partial():
        original()
        raise RuntimeError('after publication')

    owner._startup.start = partial
    assert owner.run(Event()) == 70
    assert owner._done.is_set() and not owner._startup.is_alive()


def test_cleanup_failure_retained_overrides_success_and_retry(fake):
    stop = Event()
    stop.set()
    owner = sr.SessionRuntime()
    fake[1].close.side_effect = RuntimeError('private diagnostic')
    with pytest.raises(RuntimeError, match='^session runtime cleanup unconfirmed$'):
        owner.run(stop)
    assert owner in sr._QUARANTINE
    with pytest.raises(RuntimeError, match='admission rejected'):
        sr.SessionRuntime().run(stop)
    fake[1].close.side_effect = None
    owner.close()
    assert owner._closed and owner in sr._QUARANTINE


def test_unconfirmed_thread_exit_retained(fake):
    owner = sr.SessionRuntime()
    owner._startup = Mock(ident=5)
    owner._startup.is_alive.return_value = True
    with pytest.raises(RuntimeError, match='cleanup unconfirmed'):
        owner.close()
    assert owner in sr._QUARANTINE
    owner._startup.is_alive.return_value = False
    owner.close()
    assert owner._closed


def test_wrong_thread_close_cancels_but_retains_for_creator(fake):
    owner = sr.SessionRuntime()
    owner._thread = get_ident()
    errors = []

    def close():
        try:
            owner.close()
        except RuntimeError as error:
            errors.append(str(error))

    thread = Thread(target=close)
    thread.start()
    thread.join(2)
    assert errors == ['session runtime cleanup unconfirmed']
    fake[0].request_cancel.assert_called_once()
    fake[1].close.assert_not_called()
    owner.close()
    assert owner._closed


def test_close_before_run_denies_admission(fake):
    owner = sr.SessionRuntime()
    owner.close()
    with pytest.raises(RuntimeError, match='admission rejected'):
        owner.run(Event())


def test_native_window_pumps_revocation_during_blocked_start(monkeypatch):
    host = Mock()
    cancelled = Event()
    host.request_cancel.side_effect = cancelled.set
    monkeypatch.setattr(sr, 'ControlHost', lambda: host)
    owner = sr.SessionRuntime()
    caller = get_ident()
    completed = Event()

    def start():
        assert get_ident() != caller
        hwnd = owner._window._hwnd
        assert hwnd
        user = c.WinDLL('user32', use_last_error=True)
        user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        user.PostMessageW.restype = w.BOOL
        assert user.PostMessageW(hwnd, 0x0010, 0, 0)
        assert cancelled.wait(2)
        completed.set()

    host.start.side_effect = start
    assert owner.run(Event()) == 0
    assert completed.is_set() and not owner._failed
    assert owner._window._closed and owner._closed
    assert not owner._startup.is_alive()


def test_stop_after_registration_prevents_thread_launch(fake):
    stop = Event()
    fake[1].acquire.side_effect = stop.set
    owner = sr.SessionRuntime()
    assert owner.run(stop) == 0
    fake[0].start.assert_not_called()
    assert owner._startup.ident is None
    assert owner._closed


def test_failed_window_cleanup_prevents_join_until_retry(fake):
    owner = sr.SessionRuntime()
    owner._startup = Mock(ident=5)
    owner._startup.is_alive.return_value = False
    fake[1].close.side_effect = RuntimeError('private cleanup detail')
    with pytest.raises(RuntimeError, match='^session runtime cleanup unconfirmed$'):
        owner.close()
    owner._startup.join.assert_not_called()
    assert owner in sr._QUARANTINE and not owner._closed
    fake[1].close.side_effect = None
    owner.close()
    owner._startup.join.assert_called_once_with(5)
    assert owner._closed and owner in sr._QUARANTINE


def test_revocation_wins_start_failure_but_cleanup_still_required(fake):
    owner = sr.SessionRuntime()
    fake[0].start.side_effect = RuntimeError('private startup detail')

    def pump():
        assert owner._done.wait(2)
        assert owner._failed
        return False

    fake[1].pump.side_effect = pump
    assert owner.run(Event()) == 0
    fake[0].close.assert_called_once()
    assert owner._closed and not owner._startup.is_alive()
