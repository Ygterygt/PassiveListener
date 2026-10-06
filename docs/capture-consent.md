# Capture consent boundary

Base: 98249351c0e7c4da01e78b8dec166443ae09d0a8, existing feature/yav-12-runnable-candidate.

Implemented: capture_notice(config) renders the complete validated settings and offline/local retention notice for local display only. CaptureConsent requires exact notice equality and literal True, records process identity, and rechecks settings, security quarantine and unlocked eligible session. Revocation is permanent for that object, including guard failure or configuration mismatch. An Event allows cancellation during a pending native check without waiting on that check.

Production integration obligations: display the notice and obtain a real explicit foreground decision; never manufacture accepted=True from diagnostic acknowledgement, a default, an unattended service or an unlock notification. Register/pump session notifications first; revoke on stop/session loss, check immediately before device open and during capture, and reap contained workers. This Python object is NOT an IPC credential or proof of human input. Same-user code is trusted; no hostile-code resistance. Do not serialize or persist the decision. Settings paths in the notice are local UI data, not operational log content.

Not wired: capture UI/CLI, worker transport, actual device, STT/VAD, service, installer or QA adapters. Checks are point-in-time and cancellation can overlap a final check; device ownership/closure still needs production wiring. No capture authorization crosses a process boundary through this object. Automatic startup requires a separately reviewed user-consent policy; this primitive covers explicit per-run decisions only.

Executed on Windows Python 3.12, using repository-pinned pytest 8.4.0, ruff 0.11.13, mypy 1.16.0 in run-owned venv:

    python -m pytest tests/unit/test_capture_consent.py tests/unit/test_session_entry.py -q
    28 passed in 0.23s
    python -m ruff check src/passivelistener/capture_consent.py tests/unit/test_capture_consent.py
    All checks passed!
    python -m mypy src/passivelistener/capture_consent.py
    Success: no issues found in 1 source file

New consent tests use simulated native guards, with a real threaded cancellation test. Existing diagnostic entrypoint tests include real subprocess fatal-exit coverage. No new microphone/native engine/session-transition/reboot/performance/frozen-build/CI result. Initial system Python lacked tools; installed in isolated venv, aligned versions to repository pins before checks. Initial test lint failures were formatting/default-argument issues, corrected before the final run.

Review action: Jason independently inspect notice/decision semantics, revocation and fatal caller policy; integrate three new files on existing branch/PR, run verify and return reviewed head plus durable source. Full YAV-12 candidate remains incomplete.

Independent review correction: fatal SecurityCleanupError / LeaseCleanupError must
remain distinguishable from ordinary ConsentRejected. require checks quarantine
before other rejection paths and again after the native snapshot, revokes the
object, and propagates the fatal type with a fixed diagnostic. The caller must
terminate the owning process on either fatal type, including construction failure;
retrying consent or merely closing the device is insufficient. Final-check races
remain possible; this primitive supplies no atomic capture exclusion.
