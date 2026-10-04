# Windows acceptance protocol

`windows_cases.json` preserves lifecycle, scheduling, archiving and privacy
coverage from the parent requirements. `Invoke-WindowsAcceptance.ps1` is an
executable evidence runner, not a production implementation or a claim of pass.
It requires a locally reviewed driver supplied with the production integration.
Each driver invocation receives `-Case` and `-EvidenceDirectory` and returns one
hashtable containing Status (pass/fail/blocked), exact Command, Expected,
Observed and a redacted Evidence file reference. Missing evidence is an error.
The runner exits 1 on any non-pass. Never return transcripts, audio or secrets.

```powershell
./tests/acceptance/Get-WindowsCapabilities.ps1
./tests/acceptance/Invoke-WindowsAcceptance.ps1 -Driver C:/qa/driver.ps1 -EvidenceDirectory C:/qa/evidence -Suite archive
# Explicit user-bound microphone smoke only, on approved test host:
./tests/acceptance/Invoke-WindowsAcceptance.ps1 -Driver C:/qa/driver.ps1 -EvidenceDirectory C:/qa/evidence -Suite explicit_microphone_smoke -AllowMicrophone
```

Use an isolated test VM and synthetic transcript strings. Production driver must
implement assertions, not return unconditional pass. Do not install, reboot,
kill services, modify tasks or open a microphone on the agent host as a side
effect of deterministic CI. Keep signed/unsigned EXE hash/version and exact
commands with each run. Report unsigned SmartScreen behavior honestly.

Required driver assertions:

* Install twice, repair, restart and reinstall: one service/task; configured
  user capture context, idempotent output folder and preserved configuration.
* Reboot: persist pre-boot boot ID, version and state; resume after a distinct
  boot and prove auto-start without manual launch. Recovery: terminate only the
  test service, observe configured delay/retry ceiling and fresh healthy PID.
* Upgrade and rollback: old/new EXE hashes, configuration and synthetic persisted
  text remain valid. Uninstall twice: no service/task/worker; retention policy
  verified. Test EXE checksum and documented rollback command.
* Task XML: enabled, immediate install invocation evidence, CalendarTrigger with
  DaysInterval=2, StartWhenAvailable=true and defined MultipleInstancesPolicy.
  Save Get-ScheduledTaskInfo exit result/log correlation. Missed run needs actual
  host downtime or controlled VM clock experiment, not merely XML inspection.
* Archive with injected UTC clock: files at 48h minus one tick, exactly 48h,
  and 48h plus one tick. Only strictly older is eligible. Lock one old file
  exclusively and mark one old file active. Assert both retained and logged.
  Verify ZIP CRC/readability, member names and SHA-256 bytes before source
  deletion. Reject unsafe paths. Inject write/verification failure and prove
  sources remain. Retry after partial failure and assert no loss/duplicates.
* Capture: compare SYSTEM/LocalService session-0 outcomes with authenticated
  user broker, permission denial and device unplug/replug. Do not elevate
  capture privileges to make a test pass. Real mic requires explicit smoke;
  raw audio/reference/hypothesis remain private and out of evidence uploads.
* Offline: inspect connections/firewall evidence during capture/inference;
  no audio or transcript egress. Verify separate operational logs, restricted
  output ACL, atomic UTF-8 writes, timestamp collision handling and flush.
* Configuration: device/model/tr/VAD/segment/output controls; recent-ten-minute
  interface access bounds; wake-event default disabled; routing/TTS remain
  inactive stubs. Jason reviews privacy/security and final release acceptance.

CI owner must add `python -m unittest discover -s tests/acceptance -v` to the
required `verify` check. Changes to workflow files are outside this issue's
ownership. Synthetic benchmark tests prove harness behavior only. All real
service, archive and task cases remain NOT RUN until a production driver exists.
