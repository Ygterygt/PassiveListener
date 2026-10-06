# Foreground consent adapter

The foreground_consent context manager displays the exact validated settings notice,
flushes it, and requires the exact ALLOW line from nonredirected terminal streams.
It denies EOF, overlong answers, background threads, missing terminals and I/O errors.
No settings or input are emitted to an operational logger. Consent is revoked on
scope exit, including exceptional exit. Cleanup quarantine remains a fatal exception
that the production entrypoint must handle by process termination.

This is a local UI adapter, not capture wiring or a proof of human input. It does
not open a microphone, grant unattended startup consent, persist a decision or
transfer authority to another process. Same-user terminal automation is trusted.

The eventual foreground caller must register/pump session notifications, revoke
on stop/session loss, check consent before device open and each capture iteration,
and reap workers. The stop Event rejects admission before and after blocking input
and after the constructor; it does not interrupt readline or automatically watch
stop after yielding. The caller owns live stop/revocation after admission. There is
no atomic exclusion against a concurrent final-check race. Invoke only before
capture starts; do not block a live session message pump on this prompt.

No CLI command is exposed until real capture transport and fatal caller wiring are
ready. The existing diagnostic acknowledgement remains separate from capture consent.
