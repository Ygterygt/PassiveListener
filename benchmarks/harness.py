"""Offline, in-process streaming benchmark. Output contains no transcript text."""
import argparse
import hashlib
import importlib
import json
import math
import os
import platform
import random
import struct
import time
import unicodedata
import wave
from pathlib import Path


def fixtures(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for kind in ('silence', 'noise', 'tone'):
        rng = random.Random(20261004)
        samples = []
        for i in range(32000):
            value = 0 if kind == 'silence' else (rng.randint(-1000, 1000) if kind == 'noise'
                     else round(6000 * math.sin(2 * math.pi * 440 * i / 16000)))
            samples.append(value)
        path = root / (kind + '.wav')
        with wave.open(str(path), 'wb') as out:
            out.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            out.writeframes(struct.pack('<' + 'h' * len(samples), *samples))
        hashes[kind] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def normalize(text):
    text = unicodedata.normalize('NFC', text).replace('I', 'ı').replace('İ', 'i').lower()
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in text).split())


def distance(a, b):
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        nxt = [i]
        for j, y in enumerate(b, 1):
            nxt.append(min(nxt[-1] + 1, row[j] + 1, row[j - 1] + (x != y)))
        row = nxt
    return row[-1]


def score(reference, hypothesis):
    a, b = normalize(reference), normalize(hypothesis)
    return {'wer': distance(a.split(), b.split()) / len(a.split()) if a else None,
            'cer': distance(a, b) / len(a) if a else None}


def percentile(values, p):
    return sorted(values)[max(0, math.ceil(len(values) * p) - 1)] if values else None


def working_set():
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
            (n, ctypes.c_size_t) for n in ('peak', 'rss', 'paged_peak', 'paged',
                                          'nonpaged_peak', 'nonpaged', 'page', 'page_peak')]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.rss


def measure(adapter, path, onset_s, *, clock=time.perf_counter_ns,
            cpu_clock=time.process_time_ns, resource_probe=working_set):
    events, finals = [], []
    def emit(kind, text):
        if kind not in ('partial', 'final') or not isinstance(text, str):
            raise ValueError('invalid adapter event')
        if text.strip():
            events.append((kind, clock()))
            if kind == 'final':
                finals.append(text)
    before = resource_probe()
    cpu = cpu_clock()
    # stream entry is the first-sample origin required by the adapter contract.
    start = clock()
    adapter.stream(str(path), emit)
    elapsed = clock() - start
    result = {'wall_ms': elapsed / 1e6,
              'cpu_percent_one_core': (cpu_clock() - cpu) / elapsed * 100 if elapsed else None,
              'rss_before_bytes': before, 'rss_after_bytes': resource_probe(),
              'nonempty_events': len(events)}
    for kind in ('partial', 'final'):
        first = next((t for k, t in events if k == kind), None)
        result[kind + '_ms'] = (first - start) / 1e6 - onset_s * 1000 if first is not None and onset_s is not None else None
        if result[kind + '_ms'] is not None and result[kind + '_ms'] < 0:
            raise ValueError('event before annotated speech onset; invalid measurement')
    return result, ' '.join(finals)


def summarize(rows):
    result = {'n': len(rows)}
    for metric in ('partial_ms', 'final_ms', 'cpu_percent_one_core'):
        values = [r[metric] for r in rows if r[metric] is not None]
        result[metric + '_observed'] = len(values)
        result[metric + '_missing'] = len(rows) - len(values)
        result[metric + '_completion_rate'] = len(values) / len(rows) if rows else None
        for p in (50, 95, 99):
            result[f'{metric}_p{p}'] = percentile(values, p / 100)
    return result


def run(args):
    manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    for key in ('engine', 'engine_version', 'model_sha256', 'model_license', 'code_license', 'config', 'fixtures'):
        if key not in manifest:
            raise ValueError('missing ' + key)
    if args.repetitions < 2:
        raise ValueError('at least two repetitions required')
    module = importlib.import_module(args.adapter)
    rows = []
    for fixture in manifest['fixtures']:
        path = Path(fixture['path'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != fixture['sha256']:
            raise ValueError('fixture hash mismatch')
        with wave.open(str(path), 'rb') as wav:
            duration = wav.getnframes() / wav.getframerate()
        onset = fixture.get('onset_s')
        if onset is not None and not 0 <= onset < duration:
            raise ValueError('invalid onset')
        if fixture['kind'] in ('turkish_short', 'turkish_long') and (onset is None or not fixture.get('reference_path')):
            raise ValueError('speech requires reference and onset annotation')
        for repetition in range(args.repetitions):
            adapter = module.create()
            try:
                begin = time.perf_counter_ns()
                adapter.load(manifest['config'])
                load_ms = (time.perf_counter_ns() - begin) / 1e6
                for phase in ('model_reload', 'warm'):
                    result, hypothesis = measure(adapter, path, onset)
                    if result['wall_ms'] < duration * 1000 * 0.98:
                        raise ValueError('adapter must pace audio in real time')
                    result.update(fixture=fixture['id'], kind=fixture['kind'], phase=phase,
                                  repetition=repetition, load_ms=load_ms)
                    if fixture['kind'] in ('turkish_short', 'turkish_long'):
                        result.update(score(Path(fixture['reference_path']).read_text(encoding='utf-8'), hypothesis))
                    rows.append(result)
            finally:
                adapter.close()
    summaries = []
    for fixture in manifest['fixtures']:
        for phase in ('model_reload', 'warm'):
            group = [r for r in rows if r['fixture'] == fixture['id'] and r['phase'] == phase]
            summaries.append({'fixture': fixture['id'], 'phase': phase,
                              **summarize(group)})
    # Allowlist metadata: no paths, references, hypotheses or arbitrary config in report.
    report = {'schema': 1, 'platform': platform.platform(), 'python': platform.python_version(),
              'logical_cpus': os.cpu_count(), 'engine': manifest['engine'],
              'engine_version': manifest['engine_version'], 'model_sha256': manifest['model_sha256'],
              'config_sha256': hashlib.sha256(json.dumps(manifest['config'], sort_keys=True).encode()).hexdigest(),
              'resource_scope': 'in-process only; RSS endpoints, not peak; CPU one-core percent',
              'cold_scope': 'model reload, OS cache uncontrolled; not process-cold',
              'rows': rows, 'summaries': summaries}
    target = Path(args.output)
    temp = target.with_suffix(target.suffix + '.tmp')
    temp.write_text(json.dumps(report, indent=2), encoding='utf-8')
    os.replace(temp, target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    generate = sub.add_parser('fixtures')
    generate.add_argument('directory')
    bench = sub.add_parser('run')
    bench.add_argument('--adapter', required=True)
    bench.add_argument('--manifest', required=True)
    bench.add_argument('--output', required=True)
    bench.add_argument('--repetitions', type=int, default=10)
    args = parser.parse_args()
    if args.command == 'fixtures':
        print(json.dumps(fixtures(args.directory), indent=2))
    else:
        run(args)
