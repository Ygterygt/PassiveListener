from contextlib import nullcontext
from pathlib import Path
from unittest.mock import ANY, Mock

import pytest

from passivelistener import bootstrap
from passivelistener.session_guard import SessionRejected


@pytest.fixture(autouse=True)
def fake_private_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bootstrap, "private_directory", lambda *a, **k: nullcontext())


def test_dispatch_rejects_extra_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    check = Mock()
    monkeypatch.setattr(bootstrap, "worker_check", check)
    monkeypatch.setattr(bootstrap.sys, "argv", ["app", bootstrap.CHECK_ARGUMENT, "private"])
    assert bootstrap.dispatch() == 64
    check.assert_not_called()


@pytest.mark.parametrize("reject,expected", [(False, 0), (True, 3)])
def test_child_guard(monkeypatch: pytest.MonkeyPatch, reject: bool, expected: int) -> None:
    guard = Mock(side_effect=SessionRejected("private") if reject else None)
    monkeypatch.setattr(bootstrap, "require_capture_session", guard)
    assert bootstrap.worker_check() == expected
    guard.assert_called_once_with()


@pytest.mark.parametrize("exit_code,expected", [(0, 0), (3, 3), (19, 70), (None, 70)])
def test_frozen_fixed_launch(
    monkeypatch: pytest.MonkeyPatch, exit_code: int | None, expected: int,
) -> None:
    child = Mock()
    child.wait.return_value = exit_code
    monkeypatch.setattr(bootstrap, "ContainedProcess", Mock(return_value=child))
    monkeypatch.setattr(bootstrap.sys, "frozen", True, raising=False)
    monkeypatch.setattr(bootstrap.sys, "executable", "C:/trusted/PassiveListener.exe")
    assert bootstrap.check_frozen_worker() == expected
    child.start.assert_called_once_with(
        Path("C:/trusted/PassiveListener.exe"), (bootstrap.CHECK_ARGUMENT,),
        cwd=Path("C:/trusted"), temporary=ANY,
    )
    child.resume.assert_called_once_with()
    child.close.assert_called_once_with()


def test_unfrozen_no_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = Mock()
    monkeypatch.setattr(bootstrap, "ContainedProcess", factory)
    monkeypatch.setattr(bootstrap.sys, "frozen", False, raising=False)
    assert bootstrap.check_frozen_worker() == 70
    factory.assert_not_called()


def test_failed_start_reaps(monkeypatch: pytest.MonkeyPatch) -> None:
    child = Mock()
    child.start.side_effect = RuntimeError("private")
    monkeypatch.setattr(bootstrap, "ContainedProcess", Mock(return_value=child))
    monkeypatch.setattr(bootstrap.sys, "frozen", True, raising=False)
    assert bootstrap.check_frozen_worker() == 70
    child.resume.assert_not_called()
    child.close.assert_called_once_with()


def test_cleanup_retains_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    child = Mock()
    child.wait.return_value = 0
    child.close.side_effect = RuntimeError("private")
    quarantine = []
    monkeypatch.setattr(bootstrap, "_QUARANTINE", quarantine)
    monkeypatch.setattr(bootstrap, "ContainedProcess", Mock(return_value=child))
    monkeypatch.setattr(bootstrap.sys, "frozen", True, raising=False)
    with pytest.raises(RuntimeError, match="^worker bootstrap cleanup unconfirmed$"):
        bootstrap.check_frozen_worker()
    assert len(quarantine) == 1
    assert quarantine[0][0] is child



def test_native_private_temporary_environment(tmp_path: Path) -> None:
    import json
    import sys

    from passivelistener.contained_process import ContainedProcess
    from passivelistener.private_storage import private_directory

    private = tmp_path / "private"
    output = tmp_path / "environment.json"
    with private_directory(private, create=True):
        child = ContainedProcess()
        try:
            program = (
                "import json,os,sys; "
                "open(sys.argv[1],'w').write(json.dumps(dict(os.environ)))"
            )
            child.start(Path(sys.executable), ("-I", "-c", program, str(output)),
                        cwd=tmp_path, temporary=private)
            child.resume()
            assert child.wait(10) == 0
        finally:
            child.close()
    environment = json.loads(output.read_text())
    assert environment["TEMP"] == str(private)
    assert environment["TMP"] == str(private)
    assert set(environment) == {"SYSTEMROOT", "TEMP", "TMP"}


def test_native_public_temporary_rejected(tmp_path: Path) -> None:
    import sys

    from passivelistener.contained_process import ContainedProcess, LaunchError

    child = ContainedProcess()
    try:
        with pytest.raises(LaunchError, match="^contained launch failed$"):
            child.start(Path(sys.executable), ("-I", "-c", "pass"),
                        cwd=tmp_path, temporary=tmp_path)
        with pytest.raises(LaunchError):
            child.resume()
    finally:
        child.close()


def test_lease_held_until_child_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import contextmanager

    events = []

    @contextmanager
    def lease(*args, **kwargs):
        events.append("lease")
        try:
            yield
        finally:
            events.append("release")

    child = Mock()
    child.wait.return_value = 0
    child.close.side_effect = lambda: events.append("reaped")
    monkeypatch.setattr(bootstrap, "private_directory", lease)
    monkeypatch.setattr(bootstrap, "ContainedProcess", Mock(return_value=child))
    monkeypatch.setattr(bootstrap.sys, "frozen", True, raising=False)
    assert bootstrap.check_frozen_worker() == 0
    assert events == ["lease", "reaped", "release"]


def test_failed_cleanup_keeps_live_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import contextmanager

    events = []

    @contextmanager
    def lease(*args, **kwargs):
        events.append("lease")
        try:
            yield
        finally:
            events.append("release")

    child = Mock()
    child.wait.return_value = 0
    child.close.side_effect = RuntimeError("sensitive")
    quarantine = []
    monkeypatch.setattr(bootstrap, "private_directory", lease)
    monkeypatch.setattr(bootstrap, "ContainedProcess", Mock(return_value=child))
    monkeypatch.setattr(bootstrap, "_QUARANTINE", quarantine)
    monkeypatch.setattr(bootstrap.sys, "frozen", True, raising=False)
    with pytest.raises(RuntimeError, match="^worker bootstrap cleanup unconfirmed$"):
        bootstrap.check_frozen_worker()
    assert events == ["lease"]
    assert len(quarantine) == 1
    child.close.side_effect = None
    quarantine[0][0].close()
    quarantine[0][1].close()
    assert events == ["lease", "release"]


def test_failed_private_lease_never_starts_child(monkeypatch: pytest.MonkeyPatch) -> None:
    child = Mock()
    monkeypatch.setattr(bootstrap, "private_directory", Mock(side_effect=OSError("sensitive")))
    monkeypatch.setattr(bootstrap, "ContainedProcess", Mock(return_value=child))
    monkeypatch.setattr(bootstrap.sys, "frozen", True, raising=False)
    assert bootstrap.check_frozen_worker() == 70
    child.start.assert_not_called()
    child.resume.assert_not_called()
    child.close.assert_called_once_with()
