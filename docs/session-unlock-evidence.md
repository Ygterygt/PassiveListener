# Initial unlock admission delta

Base: 89f8c176d9aa780eba190babcf6763ed803a9c35 on existing feature/yav-12-runnable-candidate / draft PR #7. Git operations remain Jason's. Apply the five path-preserving files over that exact source; no deletions. Base source ZIP SHA-256 8b875372fefd67d47ef37155ad127e8f54f487e9942e45929fcf0e5b12fb0217 verified before extraction. No shared-root or QA-owned source changed.

Implemented:
- Query the current process session only, using WTS SessionInfoEx, with Windows 10+ flag semantics. Reject session zero, inactive, locked, unknown, mismatched session, wrong level, null/truncated/oversized result and API failures.
- Require existing token eligibility before the unlock query. Release the OS-owned buffer on every path after publication, including failed queries. Copy no identity strings or times; expose only fixed rejection text. WTSFreeMemory returns void and offers no release status.
- SessionRuntime's startup thread performs this check after session registration and before host launch, while the creator thread continues pumping. Rejection cancels startup and uses existing reap/teardown. No automatic restart on unlock.

Verification, Windows Python 3.12.10; dependencies from packaging/requirements-build.txt installed into isolated venv using uv:

```text
python -m pytest tests/unit/test_session_unlock.py tests/unit/test_session_runtime.py tests/unit/test_session_window.py -q
67 passed in 0.37s
python -m ruff check src/passivelistener/session_unlock.py src/passivelistener/session_runtime.py tests/unit/test_session_unlock.py tests/unit/test_session_runtime.py
All checks passed!
python -m mypy src/passivelistener/session_unlock.py src/passivelistener/session_runtime.py
Success: no issues found in 2 source files
```

Initial run: 67 passed in 0.66s, lint passed, mypy reported Returning Any for the ctypes expression. Explicit bool conversion fixed it; outputs above are the final rerun. Native read-only WTS query executed without logging identity; ABI assertions (232-byte wrapper, 8-byte data offset, 224-byte level-one data) passed. Fault tests verify pointer release; runtime tests verify registration/check/launch ordering and denied admission. Existing runtime tests isolate the new check so they do not depend on current desktop lock state.

Limits / next actions:
- This is a point-in-time snapshot, not atomic lock/capture exclusion, consent, or secure-desktop detection. Notifications and cancellation remain necessary. The diagnostic child still does no capture. Actual lock/disconnect transitions, frozen host, microphone, engine, reboot, latency/resource measurements, CI and EXE build were not executed here.
- Initial unlock checking is implemented; consent UX/persistence and production capture admission remain open. No claim of full candidate or release readiness. Existing generic storage cleanup and fatal caller policy obligations remain.
- Jason: independently review API layout/version/cleanup and startup race boundaries, integrate on existing branch/PR, run verify, return reviewed head and durable source via existing YAV-13. After this gate, Windows Engineer continues consent/entrypoint wiring and capture/VAD/STT, lifecycle/installer and production QA adapters. Preserve every YAV-5 parent criterion.

Primary API references:
- https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/ns-wtsapi32-wtsinfoex_level1_w
- https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/ns-wtsapi32-wtsinfoexw
- https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/nf-wtsapi32-wtsquerysessioninformationw
- https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/ne-wtsapi32-wts_info_class
