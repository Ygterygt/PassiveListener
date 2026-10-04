# PassiveListener

Offline Turkish transcription for Windows, under development. MIT licensed.
Research baseline: whisper.cpp v1.7.6 multilingual base + Silero v6.0.

This first implementation slice provides explicit local artifact hash/size validation
and Windows `verify` CI: lint, strict type checking, deterministic unit tests,
PyInstaller EXE build, executable success/corruption tests and SHA-256 checksum.
The validator does not download anything or initialize a microphone.

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r packaging/requirements-build.txt
.venv/Scripts/python -m ruff check src tests/unit packaging/*.py
.venv/Scripts/python -m mypy src
.venv/Scripts/python -m pytest -q
# Activate the venv before building:
.venv/Scripts/Activate.ps1
./packaging/build.ps1
```

The candidate EXE is **unsigned** and is not an installer or a production release.
Windows may warn about an unknown publisher/SmartScreen reputation. Do not bypass
organizational execution policies. Candidate artifacts are for independent review.
Model pins are research inputs, not proof that model bytes have been staged locally.
The validator must be integrated with a protected staging/load lifecycle before
it can enforce production model integrity; checking a mutable path is not a lock.

Remaining parent scope: validated runtime settings; user-session capture;
whisper.cpp/Silero integration and partial/final text; service/authorized IPC;
secure atomic UTF-8 persistence; verified 48-hour archives; scheduled task;
installer repair/upgrade/uninstall/rollback; disabled future event contracts;
complete bundled dependency notices; device/reboot/performance acceptance.
No microphone, reboot, latency or production acceptance is claimed.
Jason owns architecture, security/privacy review and release approval.
