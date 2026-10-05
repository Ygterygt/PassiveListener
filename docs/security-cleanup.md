# Security-helper cleanup contract

Base: 6a810c83987a0fe2d10ce8b656038045d9113e33. This slice changes shared
private_storage helpers before control-event/bootstrap production wiring.

LocalFree success is NULL; CloseHandle success is nonzero. Both results are now
checked. Failed releases retain the resource kind and numeric value in a locked,
process-local quarantine and raise SecurityCleanupError with fixed text. Values
are never logged. There is no retry/reset API: terminate the owning process.
Further current_user_sid, _descriptor and _sddl acquisitions reject quarantine.
Already-running calls are not cancelled; independent finally blocks still release
their own resources. This is not a transactional cross-thread stop barrier.

Callers must treat SecurityCleanupError as fatal, stop admission and terminate/reap
the worker. That lifecycle wiring remains open. Existing event handles must still
be retained and closed by their owners, including failed acquisition objects.
The quarantine is not proof that other modules close every native resource, nor
an explanation of previously observed aggregate handle variability.

Verification: Python 3.12.10, pytest 8.4.0, ruff 0.11.13, mypy 1.16.0.
Five isolated native regression cases inject failed releases of real identity,
SDDL and security descriptor allocations and a real token. They verify retained
values and rejected subsequent acquisition. The normal path executes 100 native
identity/descriptor cycles without quarantine. OS process teardown owns failed
resources; tests do not add a production reset or retry path.

Commands (isolated source root, Python from run-owned venv312):

    python -m pytest -q tests/unit/test_security_cleanup.py tests/unit/test_private_storage.py tests/unit/test_control_event.py
    # 28 passed in 1.62s
    python -m ruff check src/passivelistener/private_storage.py tests/unit/test_security_cleanup.py
    # All checks passed!
    python -m mypy src/passivelistener/private_storage.py
    # Success: no issues found in 1 source file

Initial system-Python check could not start (pytest/ruff/mypy missing). uv initially
selected 3.14.7; no verification used that environment. Created separate venv312
explicitly from Python312 and installed packaging/requirements-build.txt. Top-level
tool pins are fixed; transitive dependencies are not fully locked by that file.

API references:
- https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-localfree
- https://learn.microsoft.com/en-us/windows/win32/api/handleapi/nf-handleapi-closehandle

No microphone, audio, real transcript, native STT, reboot, performance, new EXE,
CI or release claim. Ready/stop IPC, session events, capture/VAD/STT, service/task/
installer, production QA adapters and all remaining parent acceptance stay open.

Independent review additions: combined allocation/token release failure retains
both values without retry; enclosing event descriptor release failure leaves the
published event handle owned and explicitly closable. Both regressions execute in
isolated Windows subprocesses. A later event acquisition rejects quarantine, but
an already-owned event can still signal/wait: fatal worker shutdown must enforce
the production admission boundary. Current maintenance CLI exits nonzero on
these errors; configuration/provisioning propagate failure. Existing generic
storage lease CloseHandle reporting is outside this helper change and is not
validated by it.
