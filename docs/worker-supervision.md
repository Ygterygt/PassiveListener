# User worker process ownership

`WorkerSupervisor` owns one Windows spawn child running a trusted application
callable. This is a library primitive, not yet a capture command or SCM service.
It must be hosted by the interactive user. Never expose callable selection or
process launch parameters through privileged QA IPC or configuration.

The parent signals a stop Event and waits up to `grace_seconds` (default 5).
If the worker remains alive it terminates that process and waits up to
`kill_seconds` (default 5). Results distinguish graceful stop, worker failure and
forced termination. Death must be observed before closing the process handle.
Failure to confirm termination retains ownership, raises a fixed error and
allows a later explicit stop attempt. A supervisor never starts twice, including
after failed initialization or uncertain termination. Calls are serialized.
Timeouts bound join waits, not OS scheduling or the start/terminate system calls.

The child catches backend failures without forwarding diagnostics and exits via
`os._exit`, including on success. That skips interpreter finalizers and hung
non-daemon thread joins; the OS reclaims native mappings and quarantined model
leases at exit. Worker code must explicitly stop capture, finalize transcripts,
flush persistence and close the native session before a successful return.
Forced termination can leave retained partial files; existing persistence orphan
policy applies. No in-process backend replacement or restart loop is provided.

Python streams and C-runtime stdout/stderr are redirected to the null device
before worker invocation. No transcript crosses this control boundary. Backend
direct Win32 handle logging and third-party telemetry must independently be
disabled when integrating real engines; this is not a sandbox against malicious
code. Spawn bootstrap failures precede this diagnostic wrapper. A frozen host
must use `multiprocessing.freeze_support()` and test its bootstrap separately.

Not yet implemented: session/token guard, service and host wiring, parent-death
job object, native DLL provenance, actual microphone/VAD/STT, readiness/health
IPC, installer and benchmark adapters. Consequently a parent crash is not yet
guaranteed to terminate the child. Future lifecycle work must address that gap.

Tests run real spawned processes with synthetic work, a hung call, a stuck
non-daemon thread, explicit fixture flush and simulated termination denial.
A Windows integration test loads the reviewed model lease primitive with tiny
test-only pins, proves write denial while held, then proves OS reclamation after
failed cleanup and child exit. No real models, microphones or reboot are used.

Review limitation: on Windows, multiprocessing may create a child before start()
raises (for example during serialization). A failed start is sanitized and never
retried, but this wrapper cannot confirm or reap a partially created child whose
process handle was not returned by multiprocessing. The death-before-close and
retry guarantees above apply after start() succeeds. Production bootstrap/job
ownership must close this gap as well as parent-death handling; do not interpret
a fixed start error as proof that no child was created.
