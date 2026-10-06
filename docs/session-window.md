# Owned session window and bounded pump

Implemented against reviewed base bd46521f79f052dacd4433acc610db08f5179d33.
This adds an internal owner, not a production capture entry point.

`SessionWindow(ControlHost())` owns a dedicated hidden top-level Unicode HWND,
unique registered class, Python callback and existing WTS registration. Acquire,
pump and close must stay on the creating thread. Acquire before starting a host;
keep pumping on that thread while startup runs elsewhere. The application-level
startup thread/join loop, initial desktop unlock and explicit consent policy are
still required before production wiring. Do not start the host synchronously on
the window thread, because its readiness wait prevents message pumping.

WTS transitions, close, end-session query/notification and quit revoke the one-shot
host. Unlock/connect cannot restart it. WndProc only requests cancellation; native
cleanup runs outside WndProc. Each pump drains at most 64 queued messages. It
returns false after revocation and teardown; callers must stop pumping then.
This bound does not bound sent-message dispatch or native cleanup duration.

Teardown order: host close/reap, WTS unregister, DestroyWindow, UnregisterClassW,
then removal of the strong owner reference. Failure retains the resources that
remain owned and permanently rejects new owners. Close can retry on the creating
thread. Cleanup uncertainty overrides a successful pump result. Callback failures
are contained with fixed diagnostics rather than allowing ctypes to print private
exception text. The target must be the trusted nonthrowing cancellation method.

The window is hidden, not a user notice or consent UI. This is a same-user trusted
boundary, not protection from same-user malicious code or HWND destruction. It
does not solve existing generic storage cleanup handling, aggregate handle
variability, frozen launch/session-transition acceptance or service lifecycle.

## Executed Windows verification

Python 3.12.10, pytest 8.4.0, ruff 0.11.13, mypy 1.16.0; dependencies from unchanged
packaging/requirements-build.txt in a fresh isolated venv.

```
python -m pytest tests/unit/test_session_window.py tests/unit/test_session_notifications.py tests/unit/test_control_host.py -q
63 passed in 0.59s
python -m ruff check src/passivelistener/session_window.py tests/unit/test_session_window.py
All checks passed!
python -m mypy src/passivelistener/session_window.py
Success: no issues found in 1 source file
```

Native checks executed: dedicated HWND/WTS acquire and release, posted session,
close/end-session/quit message revocation, sent-message cancellation before reap,
wrong-thread rejection and creator cleanup. A real contained synthetic Python
child wrote a fixed ready marker and slept; a posted WTS message caused host reap
with exit 70 and closure before window release. This is synthetic message injection,
not actual lock/disconnect, microphone, engine, performance or reboot acceptance.
The test directly owns the synthetic child through ControlHost; it does not exercise
production frozen host startup or authorize bypassing the token/session guard.

Fault checks cover cleanup exceptions/false returns and retained retry, callback
diagnostic containment, partial creation, registration failure and a flooded queue.
No new EXE/CI or release result claimed. Independent review and exact-head CI go
through Jason on the existing YAV-13 review task before further wiring.

## Native contracts consulted

- https://learn.microsoft.com/en-us/windows/win32/winmsg/about-window-classes
- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-peekmessagew
- https://learn.microsoft.com/en-us/windows/win32/winmsg/using-window-procedures

Next: reviewed caller orchestration with continued pumping during startup, initial
unlock/consent policy, then real capture/VAD/STT, lifecycle/installer and production
QA adapters. All parent acceptance criteria remain open as applicable.
