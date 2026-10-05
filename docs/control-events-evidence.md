# Private control events — bounded implementation

Base: 2eb5f1f82e4c0e071c1635e3b3a80d53449b1eae (existing feature/yav-12-runnable-candidate / draft PR #7).

Implemented: session-local random manual-reset events, exact user/SYSTEM protected DACL checked on create/open, fail-closed collisions, non-inheritable handles, wait-only or signal-only peer handles plus READ_CONTROL, bounded waits (0–1000 ms), serialized close, retained ownership on failed CloseHandle. No reset API, data payload, external routing or TTS.

Trust: same-user peers are trusted. Names/ACLs do not authenticate the originating process or isolate hostile code running as that user. SYSTEM and takeover-capable administrators remain outside this boundary. This is not a privileged service RPC API. Reusing an instance after close is permitted; reacquiring while owned is rejected. Callers must retain the object after failed acquire/cleanup and retry close; no finalizer releases uncertain ownership. Host must retain distinct ready/stop events until the worker is reaped. Session eligibility/consent and lock/disconnect handling are separate requirements.

Not wired into bootstrap or capture. Proposed next integration: host creates distinct events before contained launch; fixed worker opens scoped peers, checks session eligibility, signals ready, observes stop, flushes before exit; host uses bounded timeout and job termination on failure. Frozen name transport and session events still require implementation/review. No microphone, STT, reboot, performance, CI, EXE or release result claimed.

Actual Windows / Python 3.12.10 checks in isolated scratch with packaging/requirements-build.txt pinned tools:

    python -m pytest -q tests/unit/test_control_event.py
    15 passed in 0.17s
    python -m ruff check src/passivelistener/control_event.py tests/unit/test_control_event.py
    All checks passed!
    python -m mypy src/passivelistener/control_event.py
    Success: no issues found in 1 source file

Tests execute native events and a real Python subprocess with synthetic control signals only. Public default-policy rejection uses this host's default DACL; no cross-account access test or global leak claim. Initial system Python lacked pytest/ruff/mypy; installed pinned tools in run-owned venv. Initial test run: 6 failed, 9 passed because generic GA rights were mapped in the actual kernel-object ACL; explicit 0x1f0003 policy fixed comparison. Initial strict type error was corrected using bool conversion. Tests were not weakened.

API references:
- https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createeventw
- https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-openeventw

Parent acceptance in YAV-5 remains open. Jason owns independent security/architecture review and GitHub integration. This three-file delta contains only source, synthetic tests and this record.
