# Creation-time child containment

Base: 77dc37bc25a09ece566a5dbf06e761f691c1e6c2. This adds an unwired successor
primitive; WorkerSupervisor still uses the existing multiprocessing path.

ContainedProcess creates an unnamed, non-inheritable kill-on-close job and passes
its handle in PROC_THREAD_ATTRIBUTE_JOB_LIST to CreateProcessW. The initial thread
is suspended. Job assignment is performed by Windows during process creation,
so Python/frozen loader startup cannot precede assignment. There is no named-job
lookup or child-held job handle. Unsupported attributes/nested-job constraints
fail closed; there is no fallback or breakaway. The application path is absolute,
passed separately, and arguments use Windows quoting without a shell. Only
SystemRoot is passed in the child environment; host credentials are not copied.

Ownership of PROCESS_INFORMATION exists before the native call, so injected
failure after native publication still leaves handles available for termination
and reap. resume is single-attempt. Any close attempt permanently revokes
admission, including failed termination. close terminates the job, waits a bounded
time for the primary process, then closes owned handles. Uncertain termination or
close retains ownership for explicit retry. This is forced cleanup, not graceful
transcript flush. Descendants receive job termination; all-descendant exit is not
separately enumerated/confirmed by this API.

Call only from trusted same-user, non-impersonating host code. No public CLI,
configuration command override or privileged service dispatch is added. Default
job security still derives from the owner token. This is not a hostile same-user
security boundary. Installation/image/DLL trust, token/session admission, session
events, fixed worker dispatch, private graceful-stop IPC and frozen packaging must
be wired and reviewed before production capture. No model engine or microphone
is opened by this change. No full candidate, Windows acceptance or release claim.

Microsoft source:
https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute
The documented job-list attribute assigns listed jobs to a new process and is
supported on Windows 10+. Attribute-value storage remains live through destruction
of the attribute list. See also:
https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects

## Executed verification

Windows, Python 3.12.10; pytest 8.3.5, ruff 0.11.13, mypy 1.16.0 installed only in
run scratch. Commands from source root with scratch tools on PYTHONPATH:

    py -3.12 -m pytest -q tests/unit/test_contained_process.py tests/unit/test_supervisor.py
    40 passed in 4.68s
    py -3.12 -m ruff check src/passivelistener/contained_process.py tests/unit/test_contained_process.py
    All checks passed!
    py -3.12 -m mypy src/passivelistener/contained_process.py
    Success: no issues found in 1 source file

18 new cases cover native membership while suspended, first-statement exclusion,
host death before bootstrap, environment isolation and Unicode/space quoting,
partial native publication, combined publication/cleanup failure, failed resume,
attribute/create rejection, timeout validation, retained ownership and cleanup
revocation, plus explicit invalidation of all three owned handles over 20 launches.
Existing supervisor tests remain green. Synthetic marker strings only; no audio,
private transcripts, microphone/reboot/engine/frozen-build/performance results.

An initial aggregate process-handle assertion failed (185 vs 184 in suite; 177 vs
174 isolated; one warmup still 178 vs 177). It was replaced by direct invalidation
checks for the actual owned handles, not a widened tolerance. Separate standalone
100-launch diagnostic: cold count 140; every post-close sample 143 (five batches
of 20, 0.2s between batches); settled 143. First-use initialization is a hypothesis,
not an identified cause. These observations do not prove global absence of leaks.
No CI, EXE rebuild or independent review executed in this implementation run.
