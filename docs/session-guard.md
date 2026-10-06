# Capture session eligibility boundary

Unwired library slice based on reviewed source 863f8fd42617cdc1e9c6fad4c5e53a8334bdfa6d.

`require_capture_session()` rejects API uncertainty, thread impersonation, Session 0,
elevation, missing INTERACTIVE membership, SERVICE membership and non-active WTS
state. It examines the calling process/thread, accepts no requested user/session,
and neither changes privileges nor opens a device. Operational errors have a fixed
message. Do not serialize exception internals, which may contain chained exceptions.

This is a point-in-time eligibility check, NOT consent, authentication of an IPC
peer, a lock detector, or a permanent capture authorization. WTSActive may include a
locked desktop. The eventual trusted worker must check before device open, recheck
while running and stop on session lock/logoff/disconnect using host notifications.
A disconnect can race a check; this module does not eliminate that race. The host
must never mutate/impersonate identity around these checks. No SID is logged.

Partial multiprocessing spawn and parent-death protection remain unresolved in the
reviewed supervisor. Do not wire capture/STT to that supervisor until process
ownership is addressed. No service, frozen EXE, microphone, native STT or installer
readiness is implied. No privileged process launch, session switching or capture
was performed by these tests.

## Verification

Windows Python 3.12.10; pytest 8.4.0, ruff 0.11.13, mypy 1.16.0 in run-local tools.
Commands run with tools and source/src on PYTHONPATH from the source directory:

```
python -m pytest -q tests/unit/test_session_guard.py
11 passed in 0.09s
python -m ruff check src/passivelistener/session_guard.py tests/unit/test_session_guard.py
All checks passed!
python -m mypy src/passivelistener/session_guard.py
Success: no issues found in 1 source file
```

Native tests query this host only. Policy matrix and rejection/failure tests are
synthetic; no actual LocalService, SYSTEM, RDP disconnect, lock, impersonated-thread
or Session 0 execution was attempted. Native query test compares admin membership
as an independent rejection cross-check; it does not establish all token policy
cases. 100 repeated native snapshots preserved the process handle count.

## API references

- https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/nf-wtsapi32-wtsquerysessioninformationw
- https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/ne-wtsapi32-wts_info_class
- https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-gettokeninformation
- https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-checktokenmembership

## Review/continuation

Jason owns existing branch/PR integration and independent review. Apply the three
new files to the existing reviewed branch; no Git operations were performed here.
Review native API failure behavior and token/session policy; run current-head verify
and return exact head plus durable source on the existing review assignment.
Next implementation: resolve partial-spawn/parent-death ownership, then wire real
capture/VAD/STT and session events, lifecycle, installer and production QA adapters.
All parent acceptance requirements remain open as applicable.

Native token-handle and SID cleanup failures reject eligibility. The caller must stop on rejection; cleanup failure may leave a resource until process exit, so the host must not retry capture indefinitely.
