# Session runtime orchestration

Base: f13af2d0606dabaaaec9b61d26158964ff82443e, existing feature/yav-12-runnable-candidate and draft PR #7. This source-only delta adds session_runtime.py, unit tests and this document. No Git operations or QA-owned file changes.

SessionRuntime registers its window before starting ControlHost on a separate daemon thread. The creator thread pumps during startup and diagnostic readiness. An external threading.Event requests stop; session revocation is one-shot and cannot readmit. Teardown cancels and closes the existing window owner (host reap precedes WTS/window release), then confirms startup-thread exit. Fixed 0 denotes requested stop/revocation only; 70 denotes operational failure. Cleanup uncertainty raises a fixed exception and retains the owner in process-lifetime quarantine, overriding success. Original exceptions/tracebacks are not retained or printed by the startup thread.

Caller obligations: run/close on the window thread, supply trusted stop event, treat quarantine as fatal process shutdown, and never interpret diagnostic readiness as consent/unlock/engine health. Native calls and host close are cooperative, potentially blocking. The five-second thread join is not a total shutdown deadline. Daemon-thread selection allows fatal process exit; it is not evidence of native cleanup. Same-user trusted scope remains. This module is not yet wired into CLI/service or capture. No restart after revocation.

Executed on Windows Python 3.12.10 using repository-pinned packaging/requirements-build.txt in isolated venv:

    python -m pytest tests/unit/test_session_runtime.py tests/unit/test_session_window.py tests/unit/test_control_host.py -q
    69 passed in 0.78s
    python -m ruff check src/passivelistener/session_runtime.py tests/unit/test_session_runtime.py
    All checks passed!
    python -m mypy src/passivelistener/session_runtime.py
    Success: no issues found in 1 source file

New tests use deterministic threading events and failure injection. Native hidden-window registration/message dispatch/cleanup runs with a synthetic host that posts WM_CLOSE while startup waits. Other real contained-child checks are in the existing host/window suite. No actual lock/disconnect, frozen runtime, microphone, engine, reboot, performance or new CI/build executed. No full candidate or release acceptance.

Review required: startup/window teardown ordering, thread publication failures, diagnostic failure/result precedence, retained quarantine and fatal caller obligations. After review: initial unlock/consent and production entrypoint wiring, capture/VAD/STT, service/task/installer and production QA adapters. Generic storage cleanup-result handling and aggregate handle uncertainty remain open.

Independent review: 72 focused Windows tests passed (runtime/window/host), with
scoped lint and strict typing clean. Added stop-after-registration, failed-window
cleanup/join retry ordering, and concurrent startup-failure/revocation regressions.
A requested stop or revocation takes result precedence over a concurrent startup
failure; result 0 denotes teardown requested, not successful startup or worker
health. Cleanup uncertainty still overrides either result and retains ownership.
No blocking defect found within this diagnostic-only slice. The startup thread
may remain alive after failed cleanup; quarantine requires fatal process exit.
