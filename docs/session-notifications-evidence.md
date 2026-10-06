# Session notification registration evidence

Base: 3bb45de20acb313debc0a41feff4d7eac2cd06bc on existing feature/yav-12-runnable-candidate / draft PR #7.
Base source attachment 90a2400f-079f-4bc1-9d4e-a622151eeda6 downloaded; SHA-256 matched 29205d87cca451f58cda3cf8c60acb5fba630d3e73bbc577b132f2b089945bac. Restored into fresh isolated run scratch. No Git operations, shared-root or QA-owned file edits.

Implemented: one-shot WTS notification ownership for a dedicated same-process/current-thread host window, NOTIFY_FOR_THIS_SESSION only. Every forwarded session transition latches ControlHost.request_cancel; unlock/connect cannot restart. Registration/admission failures cancel. Unregister failures retain HWND/object in permanent quarantine, reject new registration, allow explicit cleanup retry. Fixed diagnostics.

Caller obligations: dedicated previously unregistered HWND, same-thread lifecycle/message pump, forward WM_WTSSESSION_CHANGE, register before starting worker, reap host outside WndProc, then unregister before destroying window. Cancellation is cooperative and does not itself reap. The wrapper does not own/destroy HWND. Callers must retain the window on failed unregister. Target is trusted nonthrowing ControlHost.request_cancel, not an arbitrary plugin. Forged notifications only revoke admission.

Actual Windows verification: CPython 3.12.10, pytest 8.4.0, ruff 0.11.13, mypy 1.16.0 in scratch venv312.
- python -m pytest tests/unit/test_session_notifications.py tests/unit/test_control_host.py -q
  38 passed in 0.42s (7 new registration cases, 31 existing host cases).
- python -m ruff check src/passivelistener/session_notifications.py tests/unit/test_session_notifications.py
  All checks passed!
- python -m mypy src/passivelistener/session_notifications.py
  Success: no issues found in 1 source file
Native hidden STATIC window acquired and released a real WTS registration. Fault tests use synthetic APIs to exercise process/thread mismatch, registration failure, failed unregister retention/retry and quarantine. Dispatch tested against real ControlHost permanent admission rejection.

Initial default Python lacked pytest/ruff/mypy; no tests ran in that attempt. uv initially selected 3.14.7; no verification used it. Created explicit Python 3.12.10 venv and installed fixed tool versions for the reported executions.

Limits: production message loop and host wiring still absent. No real lock/disconnect/logoff event triggered, no initial unlock/consent evidence, no frozen build, native engine, microphone, reboot, performance, CI or release claim. All remaining parent YAV-5 criteria stay open. Generic storage cleanup handling and aggregate handle uncertainty remain unchanged.

API source: https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/nf-wtsapi32-wtsregistersessionnotification (current-session notifications and unregister-before-window-destruction requirements).

Next: Jason independently reviews ownership/thread/failure boundaries, integrates the three new files on the existing branch, publishes small commit, runs verify and returns exact reviewed head/durable source through existing YAV-13. Engineer then implements host message pump/wiring and initial session admission policy before real capture/VAD/STT and remaining lifecycle/adapters. This is a bounded delta, not full candidate acceptance.
