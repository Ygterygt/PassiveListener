# Reproducible STT measurement

Implemented: standard-library fixture generator, latency/scoring harness, summary
statistics and Windows in-process resource counters. No engine/model is selected
by this harness. Production adapter, engine comparison, actual speech measurement
and Windows acceptance remain unverified until their dependencies are available.

Run from repository root with Python 3.12.10:

```powershell
python -m unittest discover -s tests/acceptance -v
python -m benchmarks.harness fixtures "$env:TEMP/PassiveListener-synthetic"
python -m benchmarks.harness run --adapter local_adapter --manifest C:/qa/private/manifest.json --output C:/qa/private/results.json --repetitions 10
```

The generated 16 kHz mono PCM16 two-second silence, seeded noise and 440 Hz tone
are software stimuli, **not Turkish speech or accuracy evidence**. The source is
MIT under the repository license; no model or third-party audio is distributed.
Keep all generated audio and measured results outside the checkout. No microphone
opens, downloads, networking, prompt routing or TTS occur in this harness.

## Adapter contract (proposed for Windows Engineer)

Importable module exports `create()`. Object implements `load(config)`,
`stream(wav_path, emit)` and `close()`. `stream` resets utterance state, paces PCM
in real time from entry (first sample must be fed at entry), waits for final decode, and emits synchronous
`emit('partial'|'final', text)` events. Finals must be non-overlapping finalized
segments. It must not use subprocess inference: current counters cover this
process only. If production uses worker processes, extend instrumentation to
the complete process tree before using resource results for acceptance.
Ensure workers join before stream returns and outputs never escape local memory.

Private manifest example (replace every placeholder with measured/pinned data):

```json
{
  "engine": "PENDING_RESEARCH", "engine_version": "EXACT_COMMIT",
  "model_sha256": "LOCAL_MODEL_HASH", "model_license": "VERIFIED_LICENSE",
  "code_license": "VERIFIED_LICENSE", "config": {"language":"tr"},
  "fixtures": [{"id":"tr-short-01", "kind":"turkish_short",
    "path":"C:/qa/private/short.wav", "sha256":"INPUT_HASH",
    "onset_s":0.5, "reference_path":"C:/qa/private/short.txt"}]
}
```

Use kinds `turkish_short`, `turkish_long`, `silence`, `noise`. Synthetic tone may
use `tone`. Hash and onset validation fail closed. Missing partial/final is null,
never zero. Report completion rate alongside percentile values; percentiles use
nearest rank over present events only. A small n cannot establish tail latency.
WER uses NFC, Turkish I/İ case mapping and punctuation removal; CER includes
normalized word spaces. Empty reference gives null, not perfect accuracy.

Collect at least 10 repetitions per fixture/phase; prefer 30 for p95. Each pair
reloads the model then repeats warm; record load duration separately. Reload
does **not** clear OS caches and must not be called true cold start. For process
cold measurements launch separate processes and extend the report to record
startup, cache state and first inference separately. Speech onset is a manually
validated fixture annotation relative to the first fed sample; it is not VAD
activation or process launch. Device-path latency requires a separate live smoke.

Record CPU model, physical/logical cores, RAM, OS build, power mode, AC/battery,
GPU/driver, exact engine commit, Python/dependency versions, model hash and code
and model licenses separately. Save sanitized resolved configuration (device,
threads, quantization, tr, VAD threshold, segment/chunk length, output directory)
locally alongside report; the report exposes only its digest. Freeze inputs and
versions for comparisons. Report hardware load/thermal conditions and failures.
CPU percent uses process CPU/wall time with one core = 100%; multicore can exceed
100. RSS is before/after, not sampled peak. Silence is an idle/VAD workload, speech
active; verify inference is paused via production counters. Do not equate low
CPU or silence output alone to proof of suspended inference.

Speech corpus policy: use consenting local recordings outside Git/GitHub/task
comments until research verifies a public corpus with explicit redistribution
rights. Record consent/provenance locally and share only redacted numeric results.
No redistribution-safe Turkish corpus is claimed or bundled in this change.
Do not publish manifests, private reference text, raw output events or audio.

Parent requirements: https://github.com/Ygterygt/PassiveListener (bootstrap);
authoritative acceptance is Paperclip YAV-5. YAV-6 owns engine/license decision,
YAV-7 integration, YAV-9 observed Windows and microphone acceptance. Jason owns
final architecture, security/privacy and release approval.

Review correction: baseline RSS and CPU setup precede the monotonic stream-entry
origin. Percentiles include per-metric observed/missing counts and completion
rates. The duration guard only rejects streams shorter than real time; it cannot
prove individual feed pacing (a burst followed by sleep can pass). Feed timestamps
must be independently audited before production latency acceptance. Adapter setup
or buffering before the first sample violates this contract.
