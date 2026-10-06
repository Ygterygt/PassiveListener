import ctypes as c
from ctypes import wintypes as w
from unittest.mock import Mock

import pytest

from passivelistener import session_notifications as sn
from passivelistener.control_host import ControlHost


@pytest.fixture(autouse=True)
def quarantine():
    sn._QUARANTINE.clear()
    yield
    sn._QUARANTINE.clear()


@pytest.fixture
def api(monkeypatch):
    kernel, user, terminal = Mock(), Mock(), Mock()
    kernel.GetCurrentProcessId.return_value = 41
    kernel.GetCurrentThreadId.return_value = 42

    def window(hwnd, process):
        c.cast(process, c.POINTER(w.DWORD)).contents.value = 41
        return 42

    user.GetWindowThreadProcessId.side_effect = window
    terminal.WTSRegisterSessionNotification.return_value = 1
    terminal.WTSUnRegisterSessionNotification.return_value = 1
    monkeypatch.setattr(sn, '_libraries', lambda: (kernel, user, terminal))
    return kernel, user, terminal


def test_transition_permanently_cancels_real_host(api):
    host = ControlHost()
    notifications = sn.SessionNotifications(host)
    notifications.acquire(123)
    assert not notifications.dispatch(0)
    assert notifications.dispatch(sn.WM_WTSSESSION_CHANGE)
    with pytest.raises(RuntimeError, match='admission rejected'):
        host.start()
    host.close()
    notifications.close()
    notifications.close()
    api[2].WTSRegisterSessionNotification.assert_called_once_with(123, 0)
    api[2].WTSUnRegisterSessionNotification.assert_called_once_with(123)


@pytest.mark.parametrize('failure', ['thread', 'process', 'register'])
def test_failed_registration_cancels_and_is_one_shot(api, failure):
    target = Mock()
    owner = sn.SessionNotifications(target)
    if failure == 'thread':
        api[0].GetCurrentThreadId.return_value = 99
    elif failure == 'process':
        api[0].GetCurrentProcessId.return_value = 99
    else:
        api[2].WTSRegisterSessionNotification.return_value = 0
    with pytest.raises(RuntimeError, match='registration failed'):
        owner.acquire(123)
    target.request_cancel.assert_called_once()
    with pytest.raises(RuntimeError, match='admission rejected'):
        owner.acquire(123)
    owner.close()
    api[2].WTSUnRegisterSessionNotification.assert_not_called()


@pytest.mark.parametrize('failure', ['thread', 'unregister'])
def test_cleanup_retains_owner_and_can_retry(api, failure):
    owner = sn.SessionNotifications(Mock())
    owner.acquire(123)
    if failure == 'thread':
        api[0].GetCurrentThreadId.return_value = 99
    else:
        api[2].WTSUnRegisterSessionNotification.return_value = 0
    with pytest.raises(RuntimeError, match='cleanup unconfirmed'):
        owner.close()
    assert owner._hwnd == 123
    assert owner in sn._QUARANTINE
    with pytest.raises(RuntimeError, match='admission rejected'):
        sn.SessionNotifications(Mock()).acquire(456)
    api[0].GetCurrentThreadId.return_value = 42
    api[2].WTSUnRegisterSessionNotification.return_value = 1
    owner.close()
    assert owner._hwnd is None
    assert owner in sn._QUARANTINE


def test_native_hidden_window_registration():
    user = c.WinDLL('user32', use_last_error=True)
    user.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                                    c.c_int, c.c_int, c.c_int, c.c_int,
                                    w.HWND, w.HMENU, w.HINSTANCE, c.c_void_p]
    user.CreateWindowExW.restype = w.HWND
    user.DestroyWindow.argtypes = [w.HWND]
    user.DestroyWindow.restype = w.BOOL
    hwnd = user.CreateWindowExW(0, 'STATIC', '', 0, 0, 0, 0, 0, None, None, None, None)
    assert hwnd
    target = Mock()
    owner = sn.SessionNotifications(target)
    try:
        owner.acquire(hwnd)
        assert owner._hwnd == hwnd
        owner.close()
        assert owner._hwnd is None
        target.request_cancel.assert_called_once()
    finally:
        owner.close()
        assert user.DestroyWindow(hwnd)


def test_invalid_window_never_registers(api):
    api[1].GetWindowThreadProcessId.side_effect = None
    api[1].GetWindowThreadProcessId.return_value = 0
    target = Mock()
    owner = sn.SessionNotifications(target)
    with pytest.raises(RuntimeError, match='registration failed'):
        owner.acquire(0)
    api[2].WTSRegisterSessionNotification.assert_not_called()
    target.request_cancel.assert_called_once()


def test_library_failure_retains_registration_and_retry(monkeypatch, api):
    owner = sn.SessionNotifications(Mock())
    owner.acquire(123)
    def unavailable():
        raise OSError('sensitive diagnostic')
    monkeypatch.setattr(sn, '_libraries', unavailable)
    with pytest.raises(RuntimeError, match='^session notification cleanup unconfirmed$'):
        owner.close()
    assert owner._hwnd == 123
    assert owner in sn._QUARANTINE
    monkeypatch.setattr(sn, '_libraries', lambda: api)
    owner.close()
    assert owner._hwnd is None
    assert owner in sn._QUARANTINE
    api[2].WTSUnRegisterSessionNotification.assert_called_once_with(123)


def test_repeated_dispatch_only_cancels_without_native_cleanup(api):
    target = Mock()
    owner = sn.SessionNotifications(target)
    owner.acquire(123)
    for _ in range(3):
        assert owner.dispatch(sn.WM_WTSSESSION_CHANGE)
    assert target.request_cancel.call_count == 3
    assert owner._hwnd == 123
    api[2].WTSUnRegisterSessionNotification.assert_not_called()
    owner.close()
