import ctypes as c
import sys
import time
from ctypes import wintypes as w
from pathlib import Path
from threading import Thread
from unittest.mock import Mock

import pytest

from passivelistener import session_notifications as sn
from passivelistener import session_window as sw
from passivelistener.control_host import ControlHost


@pytest.fixture(autouse=True)
def clean():
    sw._QUARANTINE.clear()
    sn._QUARANTINE.clear()
    yield
    assert not sw._OWNERS
    sw._QUARANTINE.clear()
    sn._QUARANTINE.clear()


@pytest.fixture
def api(monkeypatch):
    kernel, user, notifications = Mock(), Mock(), Mock()
    kernel.GetCurrentThreadId.return_value = 42
    kernel.GetModuleHandleW.return_value = 43
    user.RegisterClassExW.return_value = 1
    user.CreateWindowExW.return_value = 44
    user.DestroyWindow.return_value = 1
    user.UnregisterClassW.return_value = 1
    user.PeekMessageW.return_value = 0
    monkeypatch.setattr(sw, '_libraries', lambda: (kernel, user))
    monkeypatch.setattr(sw, 'SessionNotifications', lambda host: notifications)
    notifications.dispatch.return_value = False
    return kernel, user, notifications


def test_teardown_order_and_idempotency(api):
    host = Mock()
    order = Mock()
    order.attach_mock(host.close, 'reap')
    order.attach_mock(api[2].close, 'unregister')
    order.attach_mock(api[1].DestroyWindow, 'destroy')
    order.attach_mock(api[1].UnregisterClassW, 'release')
    owner = sw.SessionWindow(host)
    owner.acquire()
    assert owner in sw._OWNERS
    assert owner.pump()
    owner.close()
    owner.close()
    assert [call[0] for call in order.mock_calls] == ['reap', 'unregister', 'destroy', 'release']
    with pytest.raises(RuntimeError, match='admission rejected'):
        owner.acquire()


def test_close_before_acquire_permanently_rejects(api):
    owner = sw.SessionWindow(Mock())
    owner.close()
    with pytest.raises(RuntimeError, match='admission rejected'):
        owner.acquire()
    api[1].RegisterClassExW.assert_not_called()


@pytest.mark.parametrize('failure', ['reap', 'unregister', 'destroy', 'release'])
def test_cleanup_failure_retains_native_owners_and_retries(api, failure):
    host = Mock()
    owner = sw.SessionWindow(host)
    owner.acquire()
    failing = {'reap': host.close, 'unregister': api[2].close,
               'destroy': api[1].DestroyWindow, 'release': api[1].UnregisterClassW}[failure]
    failing.side_effect = OSError('private detail')
    with pytest.raises(RuntimeError, match='^session window cleanup unconfirmed$'):
        owner.close()
    assert owner in sw._OWNERS and owner in sw._QUARANTINE
    if failure in ('reap', 'unregister'):
        api[1].DestroyWindow.assert_not_called()
    if failure == 'reap':
        api[2].close.assert_not_called()
    if failure != 'release':
        assert owner._hwnd == 44
        api[1].UnregisterClassW.assert_not_called()
    else:
        assert owner._hwnd is None
    with pytest.raises(RuntimeError, match='admission rejected'):
        sw.SessionWindow(Mock()).acquire()
    failing.side_effect = None
    owner.close()
    assert owner in sw._QUARANTINE


@pytest.mark.parametrize('operation', ['RegisterClassExW', 'CreateWindowExW'])
def test_partial_startup_unwinds(api, operation):
    getattr(api[1], operation).return_value = 0
    owner = sw.SessionWindow(Mock())
    with pytest.raises(RuntimeError, match='startup failed'):
        owner.acquire()
    assert owner._closed
    api[1].DestroyWindow.assert_not_called()
    if operation == 'CreateWindowExW':
        api[1].UnregisterClassW.assert_called_once()


def test_registration_failure_reaps_before_destroy(api):
    api[2].acquire.side_effect = RuntimeError('private detail')
    host = Mock()
    owner = sw.SessionWindow(host)
    with pytest.raises(RuntimeError, match='startup failed'):
        owner.acquire()
    host.close.assert_called_once()
    api[1].DestroyWindow.assert_called_once_with(44)


def test_wrong_thread_retains_for_creator_retry(api):
    owner = sw.SessionWindow(Mock())
    owner.acquire()
    api[0].GetCurrentThreadId.return_value = 45
    with pytest.raises(RuntimeError, match='thread required'):
        owner.pump()
    with pytest.raises(RuntimeError, match='cleanup unconfirmed'):
        owner.close()
    api[1].DestroyWindow.assert_not_called()
    api[0].GetCurrentThreadId.return_value = 42
    owner.close()


def test_callback_failure_is_sanitized_and_reaped(api, capsys):
    owner = sw.SessionWindow(Mock())
    owner.acquire()
    api[2].dispatch.side_effect = RuntimeError('private detail')
    assert owner._callback(44, 123, 0, 0) == 0
    api[1].DestroyWindow.assert_not_called()
    with pytest.raises(RuntimeError, match='^session window pump failed$'):
        owner.pump()
    assert owner._closed
    assert capsys.readouterr().err == ''


def test_bounded_pump(api):
    owner = sw.SessionWindow(Mock())
    owner.acquire()
    api[1].PeekMessageW.return_value = 1
    assert owner.pump()
    assert api[1].DispatchMessageW.call_count == 64
    owner.close()


@pytest.mark.parametrize('message', [sn.WM_WTSSESSION_CHANGE, 0x0010, 0x0011, 0x0016, 0x0012])
def test_native_posted_revocation_closes_real_host(message):
    host = ControlHost()
    owner = sw.SessionWindow(host)
    owner.acquire()
    user = c.WinDLL('user32', use_last_error=True)
    user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.PostMessageW.restype = w.BOOL
    user.IsWindow.argtypes = [w.HWND]
    user.IsWindow.restype = w.BOOL
    hwnd = owner._hwnd
    try:
        assert user.IsWindow(hwnd)
        assert user.PostMessageW(hwnd, message, 0, 0)
        assert not owner.pump()
        assert host._closed
        assert not user.IsWindow(hwnd)
        with pytest.raises(RuntimeError, match='admission rejected'):
            host.start()
    finally:
        owner.close()


def test_native_sent_transition_cancels_without_reaping_in_callback():
    host = ControlHost()
    owner = sw.SessionWindow(host)
    owner.acquire()
    user = c.WinDLL('user32', use_last_error=True)
    user.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.SendMessageW.restype = c.c_ssize_t
    try:
        user.SendMessageW(owner._hwnd, sn.WM_WTSSESSION_CHANGE, 0, 0)
        assert host._cancelled.is_set()
        assert not host._closed
        assert owner._hwnd
        assert not owner.pump()
        assert host._closed
    finally:
        owner.close()


def test_native_wrong_thread_cannot_destroy():
    owner = sw.SessionWindow(ControlHost())
    owner.acquire()
    errors = []
    def wrong_thread():
        try:
            owner.close()
        except RuntimeError as exc:
            errors.append(str(exc))
    thread = Thread(target=wrong_thread)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    assert errors == ['session window cleanup unconfirmed']
    assert owner._hwnd
    owner.close()


def test_native_session_message_reaps_running_contained_child(tmp_path):
    # Synthetic process only: no token bypass, capture, native engine or frozen claim.
    host = ControlHost()
    owner = sw.SessionWindow(host)
    owner.acquire()
    marker = tmp_path / 'synthetic-ready.txt'
    program = ("import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text('ready'); "
               'time.sleep(30)')
    user = c.WinDLL('user32', use_last_error=True)
    user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.PostMessageW.restype = w.BOOL
    try:
        host._child.start(Path(sys.executable), ('-I', '-c', program, str(marker)),
                          cwd=tmp_path)
        host._child.resume()
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.read_text() == 'ready'
        assert host._child.wait(0) is None
        assert user.PostMessageW(owner._hwnd, sn.WM_WTSSESSION_CHANGE, 0, 0)
        assert not owner.pump()
        assert host._child.wait(0) == 70
        assert host._child._closed
        assert host._closed and owner._closed
    finally:
        owner.close()


@pytest.mark.parametrize('operation', ['DestroyWindow', 'UnregisterClassW'])
def test_false_cleanup_result_retains_then_retries(api, operation):
    owner = sw.SessionWindow(Mock())
    owner.acquire()
    getattr(api[1], operation).return_value = 0
    with pytest.raises(RuntimeError, match='cleanup unconfirmed'):
        owner.close()
    assert owner in sw._OWNERS
    getattr(api[1], operation).return_value = 1
    owner.close()


def test_sent_revocation_during_empty_peek_still_reaps(api):
    host = Mock()
    owner = sw.SessionWindow(host)
    owner.acquire()
    def empty_peek(*args):
        owner._callback(44, 0x0010, 0, 0)
        return 0
    api[1].PeekMessageW.side_effect = empty_peek
    assert not owner.pump()
    host.close.assert_called_once()
    api[1].DispatchMessageW.assert_not_called()
    assert owner._closed


def test_library_load_failure_reaps_without_native_release(api, monkeypatch):
    def unavailable():
        raise OSError('private detail')
    monkeypatch.setattr(sw, '_libraries', unavailable)
    host = Mock()
    owner = sw.SessionWindow(host)
    with pytest.raises(RuntimeError, match='^session window startup failed$'):
        owner.acquire()
    host.close.assert_called_once()
    assert owner._closed and owner not in sw._OWNERS
    api[1].DestroyWindow.assert_not_called()


def test_native_callback_owner_survives_caller_reference_loss():
    import gc
    import weakref

    owner = sw.SessionWindow(ControlHost())
    owner.acquire()
    retained = weakref.ref(owner)
    del owner
    gc.collect()
    active = retained()
    assert active is not None and active in sw._OWNERS
    user = c.WinDLL('user32', use_last_error=True)
    user.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.SendMessageW.restype = c.c_ssize_t
    try:
        user.SendMessageW(active._hwnd, sn.WM_WTSSESSION_CHANGE, 0, 0)
        assert not active.pump()
        assert active not in sw._OWNERS
    finally:
        active.close()
