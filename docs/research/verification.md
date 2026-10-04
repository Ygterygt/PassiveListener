# Verification evidence

Executed on 2026-10-04. Synthetic inputs only. Warm rows are means of five per-pass p95 values, not a pooled p95. CPU clock resolution can round tiny runs; use wall time for these kernel loops. Both engines reset recurrent/detector state per pass.

| Engine/input | init ms | first-pass wall ms | mean warm p95 kernel ms | mean warm wall ms | positive frames, warm |
|---|---:|---:|---:|---:|---|
| webrtc/silence | 0.0135 | 28.2818 | 0.0017 | 1.1593 | 0,0,0,0,0 |
| webrtc/noise | 0.0066 | 1.5540 | 0.0021 | 1.3621 | 4,4,4,4,4 |
| silero/silence | 171.8723 | 60.8531 | 0.2513 | 52.8178 | 0,0,0,0,0 |
| silero/noise | 96.5937 | 49.9349 | 0.2448 | 50.0373 | 0,0,0,0,0 |

Commands and actual outputs:

```text
python -m venv <issue scratch>/bench-env
python -m pip install numpy==2.2.6 onnxruntime==1.22.1 webrtcvad-wheels==2.0.14
Successfully installed (full resolved versions: bench-requirements.txt)
python docs/research/vad_microbench.py --model <scratch>/silero_vad.onnx
Exit 0; four cases, first plus five warm passes each; raw output: vad-results.json.
python -c "import importlib.util; ..." (initial system environment)
{'vosk': False, 'faster_whisper': False, 'onnxruntime': False, 'webrtcvad': False, 'numpy': False}
Get-Command cmake -ErrorAction SilentlyContinue
No command returned.
hf models info ggerganov/whisper.cpp --expand sha --format json
{"id":"ggerganov/whisper.cpp","sha":"5359861c739e955e79d9a303bcbc70fb988958b1"}
gh auth status
You are not logged into any GitHub hosts.
GitHub connection search: ready (installed connector used for publication).
```

No end-to-end STT, Turkish WER, speech/noise recall, idle CPU, RAM, microphone or service acceptance measurement is included. First session load is not OS-cache-cold. No network calls occur in the measurement script. No production dependency was installed globally.

Smallest checks: JSON parsing, Python compile and 4-case/5-warm-pass shape passed. Initial git diff check found CRLF whitespace; normalized research files to LF and reran the check.
