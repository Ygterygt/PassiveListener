# Lease cleanup handoff

Base: 44fba0933dc67634a962cdfd03d9157aba0e93d1, existing feature/yav-12-runnable-candidate / draft PR #7. Base source ZIP SHA-256 740188a71417f3591683bdf3c0baf3e720a7a4a373c58933a7c1bcf01ef8c50a verified before isolated extraction. No Git operations, shared-root changes or QA-owned file changes.

Implemented: shared lease release quarantine. Storage and model-input native CloseHandle results are checked, including pending metadata/CRT-transfer failures. CRT fd/stream release exceptions retain owners. Release failures use a fixed RuntimeError-derived LeaseCleanupError, so archive per-file OSError deferral cannot hide them. ExitStack continues attempting other releases. Later storage/input acquisition rejects quarantine; existing security admission checks used by host/worker also observe it. No retry of ambiguous handles/fds. Retained owners live until process termination.

Actual Windows verification: Python 3.12.10; fresh venv using pinned packaging/requirements-build.txt.

    python -m pytest tests/unit/test_lease_cleanup.py tests/unit/test_windows_input.py tests/unit/test_private_storage.py tests/unit/test_persistence.py tests/unit/test_archive.py tests/unit/test_security_cleanup.py tests/unit/test_control_host.py tests/unit/test_control_worker.py tests/unit/test_native_lifetime.py -q
    117 passed in 5.01s
    python -m ruff check src/passivelistener/lease_cleanup.py src/passivelistener/storage_handles.py src/passivelistener/windows_input.py src/passivelistener/private_storage.py tests/unit/test_lease_cleanup.py
    All checks passed!
    python -m mypy src/passivelistener/lease_cleanup.py src/passivelistener/storage_handles.py src/passivelistener/windows_input.py src/passivelistener/private_storage.py
    Success: no issues found in 4 source files

Nine new cases run in isolated subprocesses. Native tests verify all ancestor release attempts after false/raised CloseHandle, retained native handles with GetHandleInformation, cleanup failure overriding a body error, subsequent admission rejection and exact-handle invalidation on success. Synthetic CRT failures verify retained fd/stream owners. Duplicate-to-CRT transfer failure retains both original and duplicate when close fails. Earlier 47-test run preceded shared security guard integration; final result above supersedes it.

Limits: quarantine blocks later admission, not already in-flight calls. No atomic process-wide cancellation or hard cleanup deadline is introduced. CRT errors can mean ambiguous release; retained owners must not be retried. Existing worker/host fixed-error boundaries observe exceptions; fatal process-exit policy and frozen/live acceptance still require review. Exception chaining can retain an original error internally; no new logging or private data is added. Tests use synthetic bytes only. No actual microphone, engine, lock/disconnect, reboot, performance, CI, EXE or release result claimed.

Jason: independently review resource retention, caller exception boundaries and shutdown behavior, integrate this six-file delta on the existing branch, run verify and return exact reviewed head plus durable source through the existing review issue. Full parent requirements remain open. Then continue production consent/capture/VAD/STT, lifecycle/installer and QA adapters.
