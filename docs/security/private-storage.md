# Private storage implementation evidence — YAV-12

Base: 15ce3e610d6267574d53a99c9ab5218564bc1da7, source handoff v2 SHA-256
63b4a887f45c23f830c568efb20be6ee30e0d70f013f75b9f367d17f1bd82d4b.

## Implemented, not yet published or independently reviewed

- private_storage.py creates the leaf with an explicit protected DACL at creation,
  grants only the process user and SYSTEM, verifies owner and exact DACL through
  a held handle, and rejects service identities and existing broader ACLs. The
  parent must exist. It does not silently repair or migrate existing directories.
- persistence.py writes bounded strict UTF-8 segments, flushes/fsyncs before
  non-replacing Windows rename, and uses UTC plus UUID names. Interrupted
  .partial files are retained and counted, never automatically published or
  deleted. This is a conservative orphan policy, not a recovery UI.
- storage_handles.py and windows_input.py now request LIST_DIRECTORY alongside
  READ_ATTRIBUTES and share read/write, never delete, on directory handles.
  Leaf input sharing remains read-only; archive leaf leases remain exclusive.

## Reproduced security finding

A native Windows test attempted Path.rename on the directory while its prior
READ_ATTRIBUTES-only storage lease was held. Rename succeeded: the new test
failed with `AssertionError: OSError not raised`. Adding LIST_DIRECTORY made
that test pass. Denying directory write sharing also blocked legitimate child
publication (WinError 32); share read/write without delete allows publication
while the rename-denial regression passes. The equivalent model ancestor
regression passes with the same change. This finding supersedes an assumption
in the previous independent archive inspection; review both lease primitives.

## Actual local verification

Windows host, Python 3.12.10; pytest 8.4.0, ruff 0.11.13, mypy 1.16.0.
Tools installed in run-local scratch, no global installation. Only synthetic
UTF-8 text/model bytes were used. Commands run from the staged source tree with
src and run-local test-deps on PYTHONPATH:

    python -m pytest -q
    ..................................................... [100%]
    53 passed in 1.46s

    python -m ruff check src tests
    All checks passed!

    python -m mypy src
    Success: no issues found in 10 source files

11 new tests plus the 42 prior tests executed. Tests cover private creation,
idempotence, broad/inherited ACL rejection, leaf type rejection, held-directory
rename denial, unique Turkish UTF-8 persistence, two injected interruption
points, invalid-input rejection, model ancestor rename denial, and archive
integration. These are local results, not new CI evidence.

## Review and continuation

Jessica owns applying this staged source delta to the existing isolated branch
feature/yav-12-runnable-candidate, small commits, PR #7, independent review and
CI. No Git operation was performed by Windows Engineer in this heartbeat.
The source archive was restored into isolated scratch for patch preparation;
it is not a Git clone and no branch identity is claimed for that directory.
Shared workspace and QA-owned files were untouched.

Jason retains architecture, security/privacy and release acceptance. This
library is not yet wired into a capture worker or installer. Existing archive
CLI still assumes a provisioned private root; it is not newly certified for
production transcript use. Missing parent provisioning, config persistence,
offline VAD/STT/native lifetime, capture, lifecycle, installer, benchmark/host
adapters and complete EXE/installer delivery remain on YAV-12. No microphone,
reboot, performance, power-loss durability or release result is claimed.

Windows API references:
https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-getsecurityinfo
https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights
