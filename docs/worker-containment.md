# Worker containment boundary

Base: fcaaf00a6729be68916c8e413a132417bcb1709d. Library integration only; no capture.

The supervisor creates a random Local namespace job with a non-inheritable host
handle and JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE. The spawned entry opens only
JOB_OBJECT_ASSIGN_PROCESS access, assigns itself, and closes its temporary job
handle before waiting for admission or calling trusted worker code. Host death
then terminates the contained process without a Python watchdog or GIL dependency.
Missing jobs, assignment failure, duplicate names and cleanup errors fail closed.
Nested-job incompatibility is rejection, never a breakaway fallback.

Successful start means spawn/admission, not worker readiness. No handle or name is
an external IPC/configuration interface. This assumes a trusted same-user host and
worker with immutable identity, default token DACL, and no third party retaining
job handles. It is not isolation against hostile same-user code or administrators.
No explicit job ACL policy has been added; independent review must assess this
assumption before privileged lifecycle integration. The broker must not use this
primitive with arbitrary user callables.

If start raises after publishing the child PID, ownership remains available to
stop(), which must be called to reap the child. Closing the job on start failure
prevents an unadmitted child from entering worker code. CPython can also fail
inside Popen construction before publishing its handle. This change does NOT
prove synchronous reaping of every such bootstrap process. Module import and
unpickling occur before _entry: only compile-time trusted inputs/modules are
allowed, with no model/device side effects during import. A frozen executable's
bootstrap and earliest launch containment remain acceptance work.

Tests executed on Windows with Python 3.12, pytest 8.4.0, ruff 0.11.13, mypy 1.16.0:

```
python -m pytest -q tests/unit/test_supervisor.py tests/unit/test_native_lifetime.py
28 passed in 4.02s
python -m ruff check src/passivelistener/worker_job.py src/passivelistener/supervisor.py tests/unit/test_supervisor.py
All checks passed!
python -m mypy src/passivelistener/worker_job.py src/passivelistener/supervisor.py
Success: no issues found in 2 source files
```

New tests use real spawned processes for host termination, missing-job rejection,
and injected post-start failure; collision uses real native job creation. The
first partial-start test incorrectly patched an instance with a local function,
causing serialization failure; it failed 1/20 and was corrected to patch the
process class. Final suite above passed. No audio, native STT, microphone, reboot,
installer, performance, frozen build, CI or release measurement was performed.

References:
- https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
- https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject
- https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs

Next: independently review job lifetime/access/races and integrate through the
existing PR; resolve earliest bootstrap ownership, frozen bootstrap, token/session
wiring and lock/disconnect handling before real capture/backend/lifecycle wiring.

## Independent integration review

The review added two cleanup regressions. A pre-spawn failure followed by a
failed job close previously left stop() rejecting cleanup as "not started";
this was reproduced on Windows. stop() now retries that retained job handle.
Native acquisition is split into WorkerJob.open() so the supervisor owns the
Python object before acquisition/configuration; configuration plus close failure
also retains the handle for retry. FAILED in this path reports startup failure,
not proof that an unpublished CPython bootstrap process was synchronously reaped.

Independent Windows Python 3.12.10: 30 focused supervisor/native-lifetime tests
passed, including real parent termination and both new failure regressions.
Ruff 0.11.13 and strict mypy 1.16.0 (configured Python 3.12 target) passed.

Security disposition: accepted only as this unwired, trusted same-user library
slice. NULL security attributes use the creator token's default DACL and a
non-inheritable handle; this is not an explicit least-privilege DACL guarantee.
No service/impersonation or cross-user launch is approved. A process retaining
another job handle can defeat last-close termination. Child closure before
admission handles host-death races once assignment succeeds; assignment/close
failure prevents worker invocation and the child exits. These tests do not
exhaustively schedule each native race or prove policy for arbitrary host DACLs.

Before capture wiring: contain earliest launch/import/unpickling, validate frozen
bootstrap, enforce token/session identity and lock/disconnect events, and define
an explicit job ACL/handle-transfer policy if the trust boundary expands.
No microphone, STT engine, reboot, performance or release acceptance.

Additional API reference:
https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-createjobobjectw
