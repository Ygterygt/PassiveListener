import ctypes
import subprocess
import sys

import pytest

from passivelistener.control_event import PrivateEvent, _api, event_name


def test_native_peer_signal_and_latched_wait():
    owner, peer = PrivateEvent(), PrivateEvent()
    name = event_name()
    try:
        owner.acquire(name, create=True)
        assert not owner.wait()
        peer.acquire(name, create=False, signal=True)
        peer.signal()
        assert owner.wait(100)
        assert owner.wait()
        with pytest.raises(OSError):
            peer.wait()
    finally:
        peer.close()
        owner.close()


def test_native_wait_peer_cannot_signal():
    owner, peer = PrivateEvent(), PrivateEvent()
    try:
        name = event_name()
        owner.acquire(name, create=True)
        peer.acquire(name, create=False)
        with pytest.raises(OSError):
            peer.signal()
        owner.signal()
        assert peer.wait()
    finally:
        peer.close()
        owner.close()


def test_collision_rejected_without_damaging_original():
    owner, duplicate = PrivateEvent(), PrivateEvent()
    try:
        name = event_name()
        owner.acquire(name, create=True)
        with pytest.raises(OSError, match="collision"):
            duplicate.acquire(name, create=True)
        assert duplicate._handle is None
        owner.signal()
        assert owner.wait()
    finally:
        duplicate.close()
        owner.close()


def test_native_public_policy_rejected():
    name = event_name()
    handle = _api().CreateEventW(None, True, False, name)
    assert handle
    peer = PrivateEvent()
    try:
        with pytest.raises(OSError, match="policy rejected"):
            peer.acquire(name, create=False)
        assert peer._handle is None
    finally:
        peer.close()
        assert _api().CloseHandle(handle)


def test_native_subprocess_signal():
    event = PrivateEvent()
    name = event_name()
    try:
        event.acquire(name, create=True)
        code = ('from passivelistener.control_event import PrivateEvent; '
                'e=PrivateEvent(); e.acquire(__import__("sys").argv[1], '
                'create=False, signal=True); e.signal(); e.close()')
        result = subprocess.run([sys.executable, '-c', code, name], timeout=10,
                                capture_output=True)
        assert result.returncode == 0, result.stderr
        assert event.wait()
    finally:
        event.close()


def test_close_invalidates_exact_handle():
    event = PrivateEvent()
    event.acquire(event_name(), create=True)
    handle = event._handle
    event.close()
    event.close()
    assert _api().WaitForSingleObject(handle, 0) == 0xFFFFFFFF
    assert ctypes.get_last_error() == 6
    with pytest.raises(RuntimeError):
        event.signal()


@pytest.mark.parametrize('name', ['', 'Global\\PassiveListener-' + 'a'*32,
                                  'Local\\PassiveListener-../x'])
def test_invalid_names(name):
    with pytest.raises(ValueError):
        PrivateEvent().acquire(name, create=True)


@pytest.mark.parametrize('timeout', [-1, 1001, True, 1.5])
def test_invalid_waits(timeout):
    with pytest.raises(ValueError):
        PrivateEvent().wait(timeout)


def test_missing_event():
    with pytest.raises(OSError, match='unavailable'):
        PrivateEvent().acquire(event_name(), create=False)


def test_failed_close_retains_ownership(monkeypatch):
    import passivelistener.control_event as module
    event = PrivateEvent()
    event.acquire(event_name(), create=True)
    original = event._handle
    class FailedClose:
        def CloseHandle(self, handle):
            return False
    with monkeypatch.context() as patch:
        patch.setattr(module, '_api', FailedClose)
        with pytest.raises(OSError, match='cleanup unconfirmed'):
            event.close()
        assert event._handle == original
    event.close()


def test_collision_cleanup_failure_retains_handle_for_retry(monkeypatch):
    import passivelistener.control_event as module

    owner, duplicate = PrivateEvent(), PrivateEvent()
    native = _api()

    class FailedClose:
        def __getattr__(self, name):
            return getattr(native, name)

        def CloseHandle(self, handle):
            return False

    try:
        name = event_name()
        owner.acquire(name, create=True)
        with monkeypatch.context() as patch:
            patch.setattr(module, '_api', FailedClose)
            with pytest.raises(OSError, match='cleanup unconfirmed'):
                duplicate.acquire(name, create=True)
            assert duplicate._handle is not None
            with pytest.raises(RuntimeError, match='already owned'):
                duplicate.acquire(event_name(), create=True)
        duplicate.close()
        owner.signal()
        assert owner.wait()
    finally:
        duplicate.close()
        owner.close()


def test_owner_and_peer_handles_are_noninheritable():
    from ctypes import wintypes

    api = _api()
    api.GetHandleInformation.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    api.GetHandleInformation.restype = wintypes.BOOL
    owner, peer = PrivateEvent(), PrivateEvent()
    try:
        name = event_name()
        owner.acquire(name, create=True)
        peer.acquire(name, create=False)
        for event in (owner, peer):
            flags = wintypes.DWORD()
            assert api.GetHandleInformation(event._handle, ctypes.byref(flags))
            assert flags.value & 1 == 0
    finally:
        peer.close()
        owner.close()


def test_explicit_world_access_policy_rejected():
    from passivelistener.private_storage import _Attributes, _descriptor, current_user_sid

    name = event_name()
    sid = current_user_sid()
    policy = f'O:{sid}D:P(A;;0x1f0003;;;SY)(A;;0x1f0003;;;{sid})(A;;0x100000;;;WD)'
    with _descriptor(policy) as descriptor:
        attributes = _Attributes(ctypes.sizeof(_Attributes), descriptor, False)
        handle = _api().CreateEventW(ctypes.byref(attributes), True, False, name)
    assert handle
    peer = PrivateEvent()
    try:
        with pytest.raises(OSError, match='policy rejected'):
            peer.acquire(name, create=False)
        assert peer._handle is None
    finally:
        peer.close()
        assert _api().CloseHandle(handle)
