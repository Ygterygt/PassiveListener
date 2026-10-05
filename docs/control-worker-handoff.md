# Diagnostic ready/stop IPC handoff

Base: 04664e253959d6041b70fb8ac174c7757bc93633, existing feature/yav-12-runnable-candidate / PR #7. Source restored from reviewed 61-file ZIP after SHA-256 matched 6646b6565936f20b237ccfc0215b828404ba04215e94deee21d98866f54ff6b3. Work occurred only in isolated run scratch; no Git operations or shared-root/QA-file edits.

## Implemented

Fixed internal dispatch `--internal-worker-control READY_EVENT STOP_EVENT` accepts exactly two event names; existing event parser rejects other names. No caller-selected module, payload, transcript, device or executable. Worker opens ready with signal rights and stop with wait rights. Both objects exist before acquisition and remain available for cleanup when acquisition raises after handle publication. Cleanup attempts both independently; failed owners remain in process-local quarantine. Cleanup failure overrides an otherwise successful return.

Worker checks the existing process/session eligibility and shared security quarantine before publishing readiness and during bounded waits. Preexisting stop suppresses readiness. Uses a monotonic 30-second deadline (shorter timeout available only to direct Python callers/tests), waits at most 100 ms each iteration. Fixed result codes: 0 stopped; 3 session rejected; 64 argument/timeout rejection; 70 fatal/cleanup error; 71 deadline. Existing event operations are not retroactively cancelled by quarantine. Same-user peers are trusted; names do not authenticate peers. Readiness is diagnostic eligibility, not consent, desktop unlock, model readiness, or production health.

## Executed verification

Windows, Python 3.12.10. Fresh scratch venv; pytest 8.4.0, ruff 0.11.13, mypy 1.16.0.

```
python -m pytest -q tests/unit/test_control_worker.py tests/unit/test_bootstrap.py
36 passed in 0.57s
python -m ruff check src/passivelistener/control_worker.py src/passivelistener/bootstrap.py tests/unit/test_control_worker.py
All checks passed!
python -m mypy src/passivelistener/control_worker.py src/passivelistener/bootstrap.py
Success: no issues found in 2 source files
```

Real Windows contained source-mode child used fixed dispatch, native events, actual session eligibility, ready signaling and stop/reap. Real event handles were invalidated after injected acquisition failure following publication. Session loss, quarantine, combined failed closes, failed-close override and arity rejection tested with controlled injection. Initial lint import-order failure corrected by ruff; initial 34 tests passed before two additional ownership regressions. No tests weakened.

Resolved environment: colorama 0.4.6, iniconfig 2.3.0, mypy_extensions 1.1.0, packaging 26.3, pathspec 1.1.1, pluggy 1.6.0, Pygments 2.21.0, typing_extensions 4.16.0.

## Limits and continuation

This is diagnostic IPC dispatch only; no production host, capture, models or user-session notification hookup. No frozen IPC execution/build, microphone, reboot, performance, new CI or release claim. Existing frozen diagnostic command remains unchanged. Host integration must own the two owner events through confirmed child reap, contain/terminate on deadline/fatal return, and retain resources if reap fails. Production admission/shutdown must account for shared quarantine, in-flight operations and storage lease cleanup-result handling. Session polling does not establish desktop-unlock or consent and does not replace WTS event wiring. Aggregate handle variability remains unresolved.

Jason independently reviews and integrates the delta on the existing branch through YAV-13, runs verify and supplies reviewed source. Next implementation: host ready/stop ownership and session notifications, then real capture/VAD/STT and lifecycle/installer/QA adapters. All YAV-5 parent acceptance criteria remain open as applicable. No release approval.

Independent review: failed event-close owners now reject subsequent control_worker
admission before opening new events. Process exit is required after fatal cleanup;
there is no recovery/reset API. Concurrent invocations are unsupported. Readiness
remains diagnostic only. Host ownership through reap, session event wiring and
frozen IPC acceptance remain pending.
