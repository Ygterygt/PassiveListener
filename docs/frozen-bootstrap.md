# Frozen worker bootstrap

The fixed `worker-bootstrap-check` command starts the current frozen executable
with exactly `--internal-worker-check` under creation-time job containment.
The child checks its own user/session eligibility and exits without touching a
microphone, configuration, model, transcript or network. Exit 0 means the guard
accepted; 3 means it rejected; 70 means bootstrap/timeout/cleanup failure.
Additional internal arguments are rejected with 64. There is no caller-chosen
executable, module, callback, pickle, path or environment in this dispatch.

The entrypoint selects internal dispatch before importing maintenance commands.
PyInstaller onefile children receive SystemRoot and explicit TEMP/TMP pointing to a
private user/SYSTEM directory held under lease until cleanup. They
do not reuse a parent's extraction-directory environment. Job inheritance for bootloader descendants follows the existing no-breakaway policy;
individual descendant termination is not measured here. Installation-directory/executable trust is
a required precondition, not established by this diagnostic. Session admission
is a point-in-time check, not consent, desktop-unlock evidence or authorization.
Direct internal invocation does not open a device and grants no capability.

Close runs on every launch/exit/timeout failure; uncertain cleanup retains the
owner in process-lifetime quarantine and reports a fixed diagnostic. The CLI
then exits, closing remaining process-owned job handles. This is a bounded
bootstrap diagnostic, not the capture supervisor or graceful transcript stop.

Remaining: install ACL/trust validation; private stop/ready IPC; session event
handling; capture/VAD/STT; broker/service and scheduled task; installer and QA
adapters. The old multiprocessing supervisor is still present and unwired.
No full candidate or release acceptance is implied.
