# Native model lifetime boundary

`native_lifetime.native_session` is a production ownership primitive, not a
working STT engine. Supply a fresh trusted backend implementation. Configuration
cannot name backend modules or DLLs. Both compiled model identities are verified
before `open` runs, with the existing fixed-drive/reparse/hardlink checks.

The context retains both model streams and ancestor leases through initialization,
synchronous inference and backend cleanup. Native consumers must open compatible
read handles. Backend `close` must join work and destroy all contexts/mappings
before returning, including when `open` only partially succeeded. A lock prevents
cleanup from racing inference; calls after closing fail. Close is idempotent.

If backend cleanup raises, the session becomes unusable and retains the backend
and model leases in a process-lifetime quarantine. Its public error contains no
backend diagnostic. The future worker supervisor MUST treat this error as fatal
and terminate the worker; it must not open a replacement session in that process.
There is deliberately no recovery API for uncertain native ownership. A stuck
native call can also block cleanup; the future host requires a bounded process
shutdown policy. This module does not promise bounded shutdown.

The primitive does not verify native DLL provenance, constrain DLL search, install
models, implement the backend, capture audio, or wire a supervisor. Those remain
candidate work. Trusted backend code must not keep using the model after successful
close. Same-user/admin compromise remains outside the reviewed lease assurance.

Tests use tiny synthetic model pins replaced only by pytest, and a synthetic
backend. They execute Windows sharing violations against real filesystem handles;
they do not load whisper.cpp or Silero and do not measure STT or microphones.
