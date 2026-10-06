# Diagnostic host ownership

Base: be4d30c2a2be1b2c2cc7312bcd12d7559b29b909 (existing feature/yav-12-runnable-candidate / draft PR 7).

ControlHost owns the ready and stop events before acquisition, private extraction lease, and creation-time-contained process. Admission accepts only the current frozen executable and fixed internal control arguments. Caller is the trusted non-impersonating user host, never the service. Startup checks native session eligibility and shared security quarantine before resume and during a monotonic readiness deadline. stop requests the fixed event, uses a bounded grace interval, then always forces containment cleanup. Only exit 0 yields stop result 0; failures use fixed diagnostics.

Child close must succeed before events or the extraction lease are released. Failed reap retains all owners; combined event cleanup failures attempt both events and retain the lease. close retries cleanup, but quarantine permanently revokes new admission in this process. Caller must exit on fatal cleanup uncertainty. Empty temporary folders are removed; crash leftovers are retained.

Verification on Windows, CPython 3.12.10, repository pinned packaging/requirements-build.txt:

    python -m pytest -q tests/unit/test_control_host.py
    21 passed in 0.58s
    python -m ruff check src/passivelistener/control_host.py tests/unit/test_control_host.py
    All checks passed!
    python -m mypy src/passivelistener/control_host.py
    Success: no issues found in 1 source file
    python -m pytest -q tests/unit/test_control_host.py tests/unit/test_control_worker.py tests/unit/test_control_event.py tests/unit/test_contained_process.py
    84 passed in 1.70s

Native host session query returned eligible. The native host test therefore executed real source-mode ready/stop/reap, private event ACLs, private directory creation/removal and native contained child cleanup, not the ineligible rejection branch. Source launch adaptation is test-only. Fault tests are mocks; no actual OS cleanup failure is claimed. Initial system Python lacked pytest/ruff/mypy; isolated pinned Python 3.12.10 environment resolved it. An unused uv default 3.14 environment was created first; no results are attributed to it.

Limits: diagnostic library only, no CLI host integration, frozen execution, microphone, STT engine, session-notification subscription, consent/unlock proof, reboot, performance or release result. Lifecycle calls serialize on the host thread; close from another thread can wait behind the bounded startup/stop operation. No immediate asynchronous session cancellation is implemented. A child can exit immediately after readiness observation; readiness is not ongoing health. Same-user peers and trusted immutable installation remain preconditions. Existing generic storage cleanup-result handling and aggregate handle variability remain open. ExitStack lease cleanup inherits that known limitation; this change does not assert verified release if the underlying storage primitive silently fails. Failed native child handle cleanup conservatively retains resources even if process death was observed.

Review next: Jason independently reviews host admission/ownership/cleanup ordering and tests, integrates this three-file delta on the existing branch/PR, runs exact-head verify and returns durable source. Subsequent implementation: session events and host wiring, capture/VAD/STT, service/task/installer and production QA adapters. All parent acceptance criteria remain open as applicable.
