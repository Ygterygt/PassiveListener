"""Fixed frozen bootstrap diagnostic. No capture or arbitrary worker dispatch."""

import sys
import tempfile
import uuid
from contextlib import ExitStack
from pathlib import Path

from passivelistener.contained_process import ContainedProcess
from passivelistener.private_storage import private_directory
from passivelistener.session_guard import SessionRejected, require_capture_session

CHECK_ARGUMENT = "--internal-worker-check"
CONTROL_ARGUMENT = "--internal-worker-control"
# Keep ownership if native cleanup cannot be confirmed; caller must retry or exit.
_QUARANTINE: list[tuple[ContainedProcess, ExitStack]] = []


def worker_check() -> int:
    """Exercise the real session guard in the child without opening any device."""
    try:
        require_capture_session()
    except SessionRejected:
        return 3
    return 0


def check_frozen_worker() -> int:
    """Launch only this installed EXE, with a fixed argument and no input paths.

    Trusted installation and immutable host identity are caller preconditions.
    This command is diagnostic, not an authorization or IPC boundary. A rejected
    user session returns 3; bootstrap/timeout/cleanup failure returns 70.
    """
    if not getattr(sys, "frozen", False):
        return 70
    executable = Path(sys.executable)
    if not executable.is_absolute():
        return 70
    child = ContainedProcess()
    leases = ExitStack()
    temporary = Path(tempfile.gettempdir()) / ('PassiveListener-' + uuid.uuid4().hex)
    try:
        leases.enter_context(private_directory(temporary, create=True))
        child.start(executable, (CHECK_ARGUMENT,), cwd=executable.parent, temporary=temporary)
        child.resume()
        code = child.wait(30)
        return code if code in (0, 3) else 70
    except Exception:
        return 70
    finally:
        try:
            child.close()
        except BaseException:
            _QUARANTINE.append((child, leases))
            # Never report successful bootstrap if cleanup is uncertain.
            raise RuntimeError("worker bootstrap cleanup unconfirmed") from None
        leases.close()
        # Only remove an empty directory; preserve leftovers on any failure.
        try:
            temporary.rmdir()
        except OSError:
            pass


def dispatch() -> int:
    """Dispatch before loading the maintenance CLI; no imports from caller input."""
    if sys.argv[1:2] == [CONTROL_ARGUMENT]:
        if len(sys.argv) != 4:
            return 64
        from passivelistener.control_worker import control_worker

        return control_worker(sys.argv[2], sys.argv[3])
    if sys.argv[1:2] == [CHECK_ARGUMENT]:
        if sys.argv[1:] != [CHECK_ARGUMENT]:
            return 64
        try:
            return worker_check()
        except Exception:
            return 70
    from passivelistener.cli import main

    return main()

