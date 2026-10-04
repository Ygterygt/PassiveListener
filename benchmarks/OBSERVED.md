# Observed 2026-10-04

Hardware: AMD Ryzen 7 6800H with Radeon Graphics; 8 cores / 16 logical CPUs.
Windows 11 Pro 10.0.26200; visible memory 15,930,400 KiB.
Python 3.12.10; PowerShell 7.6.6.

Command: `python -m unittest discover -s tests/acceptance -v`

```text
test_fixture_repeatability_and_format ... ok
test_no_output_is_missing_not_zero_latency ... ok
test_percentiles_nearest_rank ... ok
test_reject_pre_onset_event ... ok
test_turkish_case_and_unicode ... ok
Ran 5 tests in 0.135s
OK
```

Command: `./tests/acceptance/Get-WindowsCapabilities.ps1`

```text
ServicePresent: false
ScheduledTaskPresent: false
Microphone: not opened; explicit smoke only
```

Checks queried default names PassiveListener and PassiveListenerArchive only.
PowerShell parser: Invoke-WindowsAcceptance.ps1 had 0 parse errors.

Command: `python -m benchmarks.harness fixtures "$env:PAPERCLIP_RUN_SCRATCH_DIR/synthetic"`

SHA-256 outputs:

```text
silence 20eaebffe1816e0ffa6f7f854f5ef4ea80d5349faaf0ce1fec1b713e7fde58fa
noise   ab56ef8ac129fa9592d6a14afef165e01400e267ba26b1810076939d72d78ca6
tone    9a444b5973a1c31fc641521b32e9eda0318e45c2f7b9c7979d438fde47ad60c5
```

No production engine/model adapter or acceptance driver was available in the
bootstrap checkout. All Turkish accuracy, latency, idle/active STT CPU/RAM,
microphone, lifecycle and scheduled-task acceptance measurements: NOT RUN.
There are no performance pass claims. No workflow existed at inspected bootstrap;
required verify CI integration and independent review remain pending with Jason.
