from dataclasses import replace
from threading import Event, Thread

import pytest

from passivelistener import capture_consent as module
from passivelistener.configuration import Configuration


@pytest.fixture(autouse=True)
def eligible(monkeypatch):
    monkeypatch.setattr(module, '_require_clean', lambda: None)
    monkeypatch.setattr(module, 'require_unlocked_session', lambda: None)


def grant(config=None):
    config = Configuration() if config is None else config
    return module.CaptureConsent(config, displayed_notice=module.capture_notice(config),
                                 accepted=True)


@pytest.mark.parametrize('accepted', [False, None, 1, 'yes'])
def test_only_explicit_true(accepted):
    with pytest.raises(module.ConsentRejected):
        module.CaptureConsent(Configuration(),
                              displayed_notice=module.capture_notice(Configuration()),
                              accepted=accepted)


def test_diagnostic_or_old_notice_rejected():
    for notice in ('', 'session diagnostic', module.capture_notice(Configuration()) + ' '):
        with pytest.raises(module.ConsentRejected):
            module.CaptureConsent(Configuration(), displayed_notice=notice, accepted=True)


@pytest.mark.parametrize('change', [dict(microphone=1), dict(vad_threshold=0.7),
                                   dict(output_directory=r'C:\Other'), dict(segment_seconds=5)])
def test_changed_settings_permanently_revoke(change):
    consent = grant()
    with pytest.raises(module.ConsentRejected):
        consent.require(replace(Configuration(), **change))
    with pytest.raises(module.ConsentRejected):
        consent.require(Configuration())


def test_normal_then_explicit_revoke():
    consent = grant()
    consent.require(Configuration())
    consent.revoke()
    consent.revoke()
    with pytest.raises(module.ConsentRejected):
        consent.require(Configuration())


def test_cannot_move_to_another_process(monkeypatch):
    consent = grant()
    monkeypatch.setattr(module.os, 'getpid', lambda: -1)
    with pytest.raises(module.ConsentRejected):
        consent.require(Configuration())


@pytest.mark.parametrize('guard', ['_require_clean', 'require_unlocked_session'])
def test_guard_failure_is_sanitized_and_permanent(monkeypatch, guard):
    consent = grant()

    def fail():
        raise RuntimeError('PRIVATE DETAILS')

    monkeypatch.setattr(module, guard, fail)
    with pytest.raises(module.ConsentRejected, match='^capture consent rejected$'):
        consent.require(Configuration())
    monkeypatch.setattr(module, guard, lambda: None)
    with pytest.raises(module.ConsentRejected):
        consent.require(Configuration())


def test_revoke_during_query(monkeypatch):
    consent = grant()
    entered, released = Event(), Event()
    rejected = []

    def query():
        entered.set()
        assert released.wait(5)

    def check():
        try:
            consent.require(Configuration())
        except module.ConsentRejected:
            rejected.append(True)

    monkeypatch.setattr(module, 'require_unlocked_session', query)
    thread = Thread(target=check)
    thread.start()
    try:
        assert entered.wait(5)
        consent.revoke()
    finally:
        released.set()
        thread.join(5)
    assert not thread.is_alive()
    assert rejected == [True]


def test_invalid_settings_never_issue():
    with pytest.raises(ValueError):
        grant(replace(Configuration(), tts=True))


@pytest.mark.parametrize('error_type', [module.SecurityCleanupError, module.LeaseCleanupError])
@pytest.mark.parametrize('revoked', [False, True])
def test_fatal_cleanup_is_not_an_ordinary_denial(monkeypatch, error_type, revoked):
    consent = grant()
    if revoked:
        consent.revoke()

    def fail():
        raise error_type('PRIVATE DETAILS')

    monkeypatch.setattr(module, '_require_clean', fail)
    with pytest.raises(error_type, match='^capture consent cleanup failed$'):
        consent.require(Configuration())
    monkeypatch.setattr(module, '_require_clean', lambda: None)
    with pytest.raises(module.ConsentRejected):
        consent.require(Configuration())


def test_quarantine_during_native_query_rejects_admission(monkeypatch):
    consent = grant()

    def fail():
        raise module.LeaseCleanupError('PRIVATE DETAILS')

    def query():
        monkeypatch.setattr(module, '_require_clean', fail)

    monkeypatch.setattr(module, 'require_unlocked_session', query)
    with pytest.raises(module.LeaseCleanupError):
        consent.require(Configuration())
