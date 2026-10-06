import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows leases required')


def isolated(body: str) -> None:
    result = subprocess.run([sys.executable, '-c', body],
                            cwd=Path(__file__).resolve().parents[2] / 'src',
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('mode', ['storage', 'input'])
@pytest.mark.parametrize('failure', ['false', 'exception'])
def test_native_release_quarantine_and_admission(mode: str, failure: str) -> None:
    isolated('''
import ctypes
import hashlib
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from ctypes import wintypes
from passivelistener import lease_cleanup as c, storage_handles as s, windows_input as w
root = Path(tempfile.mkdtemp())
file = root / 'synthetic.bin'
file.write_bytes(b'synthetic')
kernel = s._kernel()
close = Mock(return_value=0) if FAILURE == 'false' else Mock(side_effect=OSError('private'))
proxy = SimpleNamespace(CloseHandle=close)
def fail_close(api, handle):
    c.close_handle(proxy, handle)
manager = s.directory_lease(root) if MODE == 'storage' else w.verified_input(
    file, hashlib.sha256(b'synthetic').hexdigest(), 9)
module = s if MODE == 'storage' else w
with patch.object(module, 'close_handle', side_effect=fail_close):
    try:
        with manager:
            raise ValueError('private body')
    except c.LeaseCleanupError as error:
        assert str(error) == 'lease resource cleanup failed'
    else:
        raise AssertionError('cleanup hidden')
assert len(c._failed) == len(root.parents) + 1
assert close.call_count == len(c._failed)
flags = wintypes.DWORD()
kernel.GetHandleInformation.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
for kind, owner in c._failed:
    assert kind == 'handle'
    assert kernel.GetHandleInformation(owner[1], ctypes.byref(flags))
for manager in (s.source_lease(file), s.directory_lease(root),
                w.verified_input(file, hashlib.sha256(b'synthetic').hexdigest(), 9)):
    try:
        with manager:
            raise AssertionError('admission after quarantine')
    except c.LeaseCleanupError:
        pass
assert close.call_count == len(c._failed)  # no retries
# Process teardown reclaims quarantined native owners.
'''.replace('MODE', repr(mode)).replace('FAILURE', repr(failure)))


@pytest.mark.parametrize('mode', ['fd', 'stream'])
def test_crt_release_retains_owner(mode: str) -> None:
    isolated('''
from unittest.mock import Mock, patch
from passivelistener import lease_cleanup as c
owner = 123 if MODE == 'fd' else Mock()
if MODE == 'stream':
    owner.close.side_effect = OSError('private')
with patch.object(c.os, 'close', side_effect=OSError('private')):
    try:
        (c.close_fd if MODE == 'fd' else c.close_stream)(owner)
    except c.LeaseCleanupError:
        pass
    else:
        raise AssertionError('failure hidden')
assert c._failed == [(MODE, owner)]
try:
    c.require_clean()
except c.LeaseCleanupError:
    pass
else:
    raise AssertionError('quarantine bypassed')
'''.replace('MODE', repr(mode)))


def test_success_invalidates_exact_native_handle() -> None:
    isolated('''
import ctypes
import tempfile
from ctypes import wintypes
from pathlib import Path
from passivelistener import storage_handles as s, lease_cleanup as c
root = Path(tempfile.mkdtemp())
with s._handle(root, directory=True) as handle:
    pass
kernel = s._kernel()
kernel.GetHandleInformation.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
flags = wintypes.DWORD()
assert not kernel.GetHandleInformation(handle, ctypes.byref(flags))
assert not c._failed
root.rmdir()
''')


def test_duplicate_transfer_failure_checks_close() -> None:
    isolated('''
import tempfile
import msvcrt
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from passivelistener import storage_handles as s, lease_cleanup as c
root = Path(tempfile.mkdtemp())
file = root / 'synthetic.bin'
file.write_bytes(b'synthetic')
proxy = SimpleNamespace(CloseHandle=Mock(return_value=0))
def fail_close(api, handle):
    c.close_handle(proxy, handle)
with patch.object(msvcrt, 'open_osfhandle', side_effect=OSError('private')):
    with patch.object(s, 'close_handle', side_effect=fail_close):
        try:
            with s.source_lease(file):
                raise AssertionError('unexpected yield')
        except c.LeaseCleanupError:
            pass
assert len(c._failed) == 2
assert c._failed[0][1][1] != c._failed[1][1][1]
''')


def test_security_admission_observes_lease_quarantine() -> None:
    isolated("""
from unittest.mock import patch
from passivelistener import lease_cleanup as c, private_storage as p
c._failed.append(('fd', 123))
with patch.object(p, '_kernel', side_effect=AssertionError('native acquisition reached')):
    try:
        p.current_user_sid()
    except c.LeaseCleanupError:
        pass
    else:
        raise AssertionError('security admission bypassed quarantine')
""")


def test_lease_quarantine_is_fatal_after_cleanup_retry() -> None:
    isolated('''
import subprocess, sys
script = """
from passivelistener import session_entry as entry, lease_cleanup as c
class Runtime:
    def run(self, stop):
        c._failed.append(('fd', 123))
        raise c.LeaseCleanupError('lease resource cleanup failed')
    def close(self):
        pass
entry.SessionRuntime = Runtime
entry.run_session_diagnostic(True)
raise SystemExit(99)
"""
r = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
assert r.returncode == 70, (r.returncode, r.stdout, r.stderr)
assert 'session diagnostic result:' not in r.stderr
''')
