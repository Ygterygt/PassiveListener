import ctypes as c
from ctypes import wintypes as w
from unittest.mock import Mock

import pytest

from passivelistener import session_unlock as su


@pytest.fixture
def native(monkeypatch):
    kernel, terminal = Mock(), Mock()
    info = su._Info()
    info.level = 1
    info.data.session, info.data.state, info.data.flags = 7, 0, 1

    def process_session(pid, output):
        c.cast(output, c.POINTER(w.DWORD))[0] = 7
        return True

    def query(server, session, kind, output, size):
        assert server is None and session == 7 and kind == 25
        c.cast(output, c.POINTER(c.c_void_p))[0] = c.addressof(info)
        c.cast(size, c.POINTER(w.DWORD))[0] = c.sizeof(info)
        return True

    kernel.ProcessIdToSessionId.side_effect = process_session
    terminal.WTSQuerySessionInformationW.side_effect = query
    monkeypatch.setattr(su.c, 'WinDLL', lambda name, **kw:
                        kernel if name == 'kernel32' else terminal)
    return kernel, terminal, info


def test_windows_abi_layout():
    assert c.sizeof(su._Level1) == 224
    assert c.sizeof(su._Info) == 232
    assert su._Info.data.offset == 8
    assert su._Level1.times.offset == 160


def test_valid_snapshot_and_owned_buffer_release(native):
    assert su._query_unlocked()
    native[1].WTSFreeMemory.assert_called_once()
    assert native[1].WTSFreeMemory.call_args.args[0].value == c.addressof(native[2])


@pytest.mark.parametrize(('field', 'value'), [
    ('level', 0), ('level', 2), ('session', 8), ('session', 0),
    ('state', 1), ('state', 4), ('flags', 0), ('flags', -1), ('flags', 2),
])
def test_rejection_matrix_frees_buffer(native, field, value):
    setattr(native[2] if field == 'level' else native[2].data, field, value)
    assert not su._query_unlocked()
    native[1].WTSFreeMemory.assert_called_once()


@pytest.mark.parametrize('failure', ['false', 'null', 'short', 'large', 'raise'])
def test_malformed_or_failed_query_frees_published_buffer(native, failure):
    terminal = native[1]
    original = terminal.WTSQuerySessionInformationW.side_effect

    def query(server, session, kind, output, size):
        original(server, session, kind, output, size)
        if failure == 'null':
            c.cast(output, c.POINTER(c.c_void_p))[0] = None
        elif failure in ('short', 'large'):
            c.cast(size, c.POINTER(w.DWORD))[0] = 20 if failure == 'short' else 4096
        elif failure == 'raise':
            raise OSError('private detail')
        return failure != 'false'

    terminal.WTSQuerySessionInformationW.side_effect = query
    with pytest.raises((su.SessionRejected, OSError)):
        su._query_unlocked()
    assert terminal.WTSFreeMemory.call_count == (0 if failure == 'null' else 1)


def test_process_query_failure_never_allocates(native):
    native[0].ProcessIdToSessionId.side_effect = None
    native[0].ProcessIdToSessionId.return_value = False
    with pytest.raises(su.SessionRejected):
        su._query_unlocked()
    native[1].WTSQuerySessionInformationW.assert_not_called()


def test_session_zero_rejected_without_query(native):
    native[0].ProcessIdToSessionId.side_effect = None
    native[0].ProcessIdToSessionId.return_value = True
    assert not su._query_unlocked()
    native[1].WTSQuerySessionInformationW.assert_not_called()


def test_old_windows_flags_never_used(monkeypatch):
    monkeypatch.setattr(su.sys, 'getwindowsversion', lambda: Mock(major=6))
    loader = Mock()
    monkeypatch.setattr(su.c, 'WinDLL', loader)
    with pytest.raises(su.SessionRejected):
        su._query_unlocked()
    loader.assert_not_called()


@pytest.mark.parametrize('eligible', [False, True])
def test_token_eligibility_precedes_unlock_query(monkeypatch, eligible):
    guard, query = Mock(), Mock(return_value=True)
    if not eligible:
        guard.side_effect = RuntimeError('private identity detail')
    monkeypatch.setattr(su, 'require_capture_session', guard)
    monkeypatch.setattr(su, '_query_unlocked', query)
    if eligible:
        su.require_unlocked_session()
        query.assert_called_once()
    else:
        with pytest.raises(su.SessionRejected, match='^unlocked session required$'):
            su.require_unlocked_session()
        query.assert_not_called()


@pytest.mark.parametrize('error', [None, OSError('private identity detail')])
def test_public_rejection_sanitized(monkeypatch, error, capsys):
    monkeypatch.setattr(su, 'require_capture_session', lambda: None)
    monkeypatch.setattr(su, '_query_unlocked', Mock(return_value=False, side_effect=error))
    with pytest.raises(su.SessionRejected, match='^unlocked session required$'):
        su.require_unlocked_session()
    assert capsys.readouterr() == ('', '')


def test_actual_native_query_without_identity_output():
    # Real OS snapshot only. Neither outcome proves a lock/unlock transition.
    assert type(su._query_unlocked()) is bool


def test_buffer_release_exception_rejects_otherwise_valid_snapshot(native, monkeypatch):
    monkeypatch.setattr(su, 'require_capture_session', lambda: None)
    native[1].WTSFreeMemory.side_effect = OSError('private release detail')
    with pytest.raises(su.SessionRejected, match='^unlocked session required$'):
        su.require_unlocked_session()
    native[1].WTSFreeMemory.assert_called_once()


def test_library_load_failure_is_sanitized_before_allocation(monkeypatch):
    monkeypatch.setattr(su, 'require_capture_session', lambda: None)
    monkeypatch.setattr(su.c, 'WinDLL', Mock(side_effect=OSError('private library detail')))
    with pytest.raises(su.SessionRejected, match='^unlocked session required$'):
        su.require_unlocked_session()
