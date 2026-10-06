import io
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from passivelistener import capture_consent
from passivelistener import foreground_consent as module
from passivelistener.configuration import Configuration
from passivelistener.lease_cleanup import LeaseCleanupError
from passivelistener.private_storage import SecurityCleanupError


class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def terminal(monkeypatch):
    incoming, outgoing = Terminal('ALLOW\n'), Terminal()
    monkeypatch.setattr(module, 'sys', SimpleNamespace(stdin=incoming, stdout=outgoing))
    monkeypatch.setattr(capture_consent, 'require_unlocked_session', lambda: None)
    return incoming, outgoing


def test_exact_notice_and_scope_revocation(terminal):
    config = Configuration()
    with module.foreground_consent(config, Event()) as decision:
        decision.require(config)
        assert terminal[1].getvalue().startswith(capture_consent.capture_notice(config) + '\n')
    with pytest.raises(capture_consent.ConsentRejected):
        decision.require(config)


@pytest.mark.parametrize('answer', ['', 'ALLOW', 'allow\n', 'yes\n', ' ALLOW\n',
                                    'ALLOW \n', 'ALLOW' * 100])
def test_denial_eof_and_oversized_answer(terminal, answer):
    terminal[0].seek(0)
    terminal[0].truncate()
    terminal[0].write(answer)
    terminal[0].seek(0)
    with pytest.raises(capture_consent.ConsentRejected):
        with module.foreground_consent(Configuration(), Event()):
            pytest.fail('admitted')
    assert terminal[0].tell() <= 16


@pytest.mark.parametrize('stream', ['stdin', 'stdout'])
def test_redirected_stream_never_displays_settings(terminal, monkeypatch, stream):
    monkeypatch.setattr(module.sys, stream, io.StringIO('ALLOW\n'))
    with pytest.raises(capture_consent.ConsentRejected):
        with module.foreground_consent(Configuration(), Event()):
            pytest.fail('admitted')
    assert terminal[1].getvalue() == ''


def test_stop_during_input_rejects(terminal, monkeypatch):
    stop = Event()

    def read(size):
        stop.set()
        return 'ALLOW\n'

    monkeypatch.setattr(terminal[0], 'readline', read)
    with pytest.raises(capture_consent.ConsentRejected):
        with module.foreground_consent(Configuration(), stop):
            pytest.fail('admitted')


@pytest.mark.parametrize('error', [SecurityCleanupError, LeaseCleanupError])
def test_fatal_after_input_not_demoted(terminal, monkeypatch, error):
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise error('fixed fatal')

    monkeypatch.setattr(module, '_require_clean', check)
    with pytest.raises(error):
        with module.foreground_consent(Configuration(), Event()):
            pytest.fail('admitted')


def test_body_failure_preserved_and_revoked(terminal):
    with pytest.raises(RuntimeError, match='body failure'):
        with module.foreground_consent(Configuration(), Event()) as decision:
            raise RuntimeError('body failure')
    with pytest.raises(capture_consent.ConsentRejected):
        decision.require(Configuration())


def test_background_thread_rejected_before_display(terminal):
    results = []

    def background():
        try:
            with module.foreground_consent(Configuration(), Event()):
                results.append('admitted')
        except capture_consent.ConsentRejected:
            results.append('rejected')

    thread = Thread(target=background)
    thread.start()
    thread.join(5)
    assert results == ['rejected']
    assert terminal[1].getvalue() == ''


@pytest.mark.parametrize('operation', ['write', 'flush', 'readline'])
def test_terminal_io_failure_sanitized(terminal, monkeypatch, operation):
    def fail(*args):
        raise OSError('private local details')

    target = terminal[0] if operation == 'readline' else terminal[1]
    monkeypatch.setattr(target, operation, fail)
    with pytest.raises(capture_consent.ConsentRejected, match='^capture consent rejected$'):
        with module.foreground_consent(Configuration(), Event()):
            pytest.fail('admitted')


def test_stop_during_final_snapshot_revokes_published_decision(terminal, monkeypatch):
    stop = Event()
    decisions = []
    original = module.CaptureConsent

    def create(*args, **kwargs):
        decision = original(*args, **kwargs)
        decisions.append(decision)
        stop.set()
        return decision

    monkeypatch.setattr(module, 'CaptureConsent', create)
    with pytest.raises(capture_consent.ConsentRejected):
        with module.foreground_consent(Configuration(), stop):
            pytest.fail('admitted')
    with pytest.raises(capture_consent.ConsentRejected):
        decisions[0].require(Configuration())


def test_partial_notice_write_denies_before_input(terminal, monkeypatch):
    reads = []
    monkeypatch.setattr(terminal[1], 'write', lambda text: len(text) - 1)
    monkeypatch.setattr(terminal[0], 'readline', lambda size: reads.append(size) or 'ALLOW\n')
    with pytest.raises(capture_consent.ConsentRejected):
        with module.foreground_consent(Configuration(), Event()):
            pytest.fail('admitted with incomplete notice')
    assert reads == []


@pytest.mark.parametrize('error', [SecurityCleanupError, LeaseCleanupError])
def test_body_fatal_is_preserved_and_decision_revoked(terminal, error):
    failure = error('fatal cleanup')
    with pytest.raises(error) as caught:
        with module.foreground_consent(Configuration(), Event()) as decision:
            raise failure
    assert caught.value is failure
    with pytest.raises(capture_consent.ConsentRejected):
        decision.require(Configuration())


def test_flush_finishes_before_answer_is_read(terminal, monkeypatch):
    operations = []
    monkeypatch.setattr(terminal[1], 'flush', lambda: operations.append('flush'))
    monkeypatch.setattr(
        terminal[0], 'readline', lambda size: operations.append('read') or 'ALLOW\n')
    with module.foreground_consent(Configuration(), Event()):
        assert operations == ['flush', 'read']
