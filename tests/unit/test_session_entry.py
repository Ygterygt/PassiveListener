import signal
from threading import Thread

import pytest

from passivelistener import session_entry as entry


@pytest.fixture
def fake(monkeypatch):
    state = {'runs': 0, 'closes': 0, 'failure': False, 'cleanup': False}

    class Runtime:
        def run(self, stop):
            state['runs'] += 1
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
            assert stop.is_set()
            if state['failure']:
                raise RuntimeError('PRIVATE diagnostic')
            return 0

        def close(self):
            state['closes'] += 1
            if state['cleanup']:
                raise RuntimeError('PRIVATE cleanup')

    monkeypatch.setattr(entry, 'SessionRuntime', Runtime)
    return state


@pytest.mark.parametrize('ack', [False, None, 1, 'yes'])
def test_no_implicit_acknowledgement(fake, ack):
    assert entry.run_session_diagnostic(ack) == 64
    assert fake['runs'] == 0
    assert fake['closes'] == 0


def test_stop_and_restore(fake):
    before = {k: signal.getsignal(k) for k in
              (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK)}
    assert entry.run_session_diagnostic(True) == 0
    assert fake['closes'] == 1
    assert before == {k: signal.getsignal(k) for k in before}


def test_failure_sanitized(fake, capsys):
    fake['failure'] = True
    assert entry.run_session_diagnostic(True) == 70
    assert fake['closes'] == 1
    out = capsys.readouterr()
    assert 'PRIVATE' not in out.out + out.err


def test_cleanup_failure_is_process_fatal(fake, monkeypatch):
    fake['cleanup'] = True
    before = {k: signal.getsignal(k) for k in
              (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK)}

    def fatal(code):
        raise SystemExit(code)

    monkeypatch.setattr(entry.os, '_exit', fatal)
    try:
        with pytest.raises(SystemExit, match='70'):
            entry.run_session_diagnostic(True)
    finally:
        for kind, handler in before.items():
            signal.signal(kind, handler)


def test_wrong_thread_rejects(fake):
    results = []
    thread = Thread(target=lambda: results.append(entry.run_session_diagnostic(True)))
    thread.start()
    thread.join(2)
    assert results == [64]
    assert fake['runs'] == 0


def test_partial_signal_install_restores(fake, monkeypatch):
    original = signal.signal
    previous = signal.getsignal(signal.SIGINT)

    def install(kind, handler):
        if kind == signal.SIGTERM:
            raise OSError('PRIVATE signal')
        return original(kind, handler)

    monkeypatch.setattr(signal, 'signal', install)
    assert entry.run_session_diagnostic(True) == 70
    assert signal.getsignal(signal.SIGINT) == previous
    assert fake['runs'] == 0


def test_cli_rejects_unacknowledged(fake, monkeypatch):
    from passivelistener.cli import main
    monkeypatch.setattr('sys.argv', ['PassiveListener', 'session-diagnostic'])
    assert main() == 64
    assert fake['runs'] == 0


def test_actual_fatal_process_exit():
    import os
    import subprocess
    import sys
    from pathlib import Path

    script = '''
from passivelistener import session_entry as entry
class Runtime:
    def run(self, stop):
        return 0
    def close(self):
        raise RuntimeError('PRIVATE native cleanup')
entry.SessionRuntime = Runtime
entry.run_session_diagnostic(True)
raise SystemExit(99)
'''
    env = dict(os.environ, PYTHONPATH=str(Path(entry.__file__).parents[1]))
    result = subprocess.run([sys.executable, '-c', script], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 70
    assert 'PRIVATE' not in result.stdout + result.stderr
    assert 'session diagnostic result:' not in result.stdout + result.stderr


def test_restoration_failure_does_not_skip_other_handlers(fake, monkeypatch):
    original = signal.signal
    kinds = (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK)
    before = {kind: signal.getsignal(kind) for kind in kinds}
    restored = []

    def install(kind, handler):
        if handler is before[kind]:
            restored.append(kind)
            if kind == signal.SIGINT:
                raise OSError('PRIVATE restoration')
        return original(kind, handler)

    monkeypatch.setattr(signal, 'signal', install)
    try:
        assert entry.run_session_diagnostic(True) == 70
        assert restored == list(kinds)
        assert fake['closes'] == 1
    finally:
        for kind, handler in before.items():
            original(kind, handler)


def test_constructor_failure_restores_handlers(fake, monkeypatch, capsys):
    before = {kind: signal.getsignal(kind) for kind in
              (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK)}

    def fail():
        raise RuntimeError('PRIVATE constructor')

    monkeypatch.setattr(entry, 'SessionRuntime', fail)
    assert entry.run_session_diagnostic(True) == 70
    assert before == {kind: signal.getsignal(kind) for kind in before}
    assert fake['runs'] == fake['closes'] == 0
    output = capsys.readouterr()
    assert 'PRIVATE' not in output.out + output.err
