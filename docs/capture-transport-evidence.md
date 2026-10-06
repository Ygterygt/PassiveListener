# PCM transport evidence

Base: 4378a727e138fd9877c427987dd02a3e216cdef6, existing feature/yav-12-runnable-candidate / draft PR #7.
Restored reviewed 90-file source archive in isolated run scratch. SHA-256 matched
2d465d2e38949bde836c3e8e32e860d34877f7907058b30597207cc92b772183.
No Git/branch operations, shared-root edits or QA-owned file edits.

## Implemented

- In-memory FIFO for exactly 320 samples / 640 bytes of mono signed PCM16 little-endian at 16 kHz (20 ms). Device adapter must frame/resample upstream.
- Capacity defaults to 50 frames (one second, 32,000 PCM bytes), hard maximum 250 frames (five seconds, 160,000 PCM bytes). Python object overhead is additional.
- Per-run settings-bound consent checked before and after enqueue/dequeue. Native checks occur outside queue lock. Close revokes first, clears pending references, and is idempotent.
- Overflow, malformed frames and clock regression terminate admission and purge the queue; no silent drop/continuation. Fatal consent cleanup errors propagate unchanged in type. Fixed transport diagnostics and PCM-free frame repr.
- Sequence/sample offset and monotonic admission timestamp. Timestamp is NOT acoustic onset, device time or a measured latency result.

## Executed verification (Windows, Python 3.12.10)

Default Python initially lacked pytest/ruff/mypy. Created an isolated run venv with uv and installed packaging/requirements-build.txt. Resolved versions: pytest 8.4.0, ruff 0.11.13, mypy 1.16.0. Full resolved tool list is in the run transcript; transitive dependencies are not fully pinned by the existing requirements file.

Commands from restored source, using venv/Scripts/python.exe:

```
python -m pytest tests/unit/test_capture_transport.py tests/unit/test_capture_consent.py -q
40 passed in 0.13s
python -m ruff check src/passivelistener/capture_transport.py tests/unit/test_capture_transport.py
All checks passed!
python -m mypy src/passivelistener/capture_transport.py
Success: no issues found in 1 source file
```

First run: 39 tests passed; lint found one 102-character test line. Wrapped the assertion and added the post-enqueue revocation case; final commands above all passed.
Tests use generated zero/pattern PCM and simulated native session/consent guards. Actual threads exercise close while a guard is pending. Tests cover FIFO/sample continuity, bounded overflow, invalid input, clock regression, fatal cleanup propagation, invalid capacity, repeated close, and revocation during or after enqueue/dequeue.

## Limits / required continuation

Unwired transport only. No microphone opened, native engine run, private audio/transcript stored, frozen EXE built, CI run, actual session transition, reboot or performance measurement. This is not a runnable acceptance candidate or release approval.
Consent checks may block on native snapshots: methods are NOT real-time device callbacks and must run on an adapter-owned thread. Close is cooperative; it does not reap that thread or stop hardware. Snapshot checks cannot atomically exclude a stop immediately after the final check. Delivered frame ownership belongs to caller, which must stop inference and release frames on revocation. Clearing references is not secure erasure. Same-user malicious code is outside the boundary.

Jason: independently review transport/caller/revocation boundaries, integrate these three files on the existing branch/PR, run verify, return exact reviewed head and durable source via existing YAV-13. After handoff Windows Engineer continues device transport/fatal caller wiring, VAD/STT, service/task/installer and production QA adapters. All YAV-5 acceptance criteria remain open as applicable; Jason retains architecture/security/release approval.
