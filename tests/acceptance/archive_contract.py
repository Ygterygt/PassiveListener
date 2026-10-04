"""QA-owned filesystem oracle. Driver performs operations, never supplies verdicts."""
import argparse
import contextlib
import ctypes
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

NOW_NS = 1791136800000000000
AGE_NS = 48 * 3600 * 1_000_000_000
def require(condition, message):
    if not condition:
        raise AssertionError(message)


CASES = ('boundary', 'exclusions', 'verification_failure', 'write_failure', 'partial_retry')


@contextlib.contextmanager
def exclusive_lock(path):
    if os.name != 'nt':
        raise RuntimeError('Windows exclusive-lock acceptance requires Windows')
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        kernel.CloseHandle(handle)


def inspect(root, original, retained):
    archived = {}
    for archive in root.glob('*.zip'):
        with zipfile.ZipFile(archive) as z:
            require(z.testzip() is None, 'ZIP CRC failure')
            for member in z.infolist():
                require(member.filename in original, 'unexpected/unsafe ZIP member')
                require(member.filename not in archived, 'duplicate archived member')
                archived[member.filename] = z.read(member)
                require(archived[member.filename] == original[member.filename], 'ZIP bytes differ')
    actual = {p.name: p.read_bytes() for p in root.glob('*.txt')}
    require(set(actual) == set(retained), 'incorrect retained sources')
    require(all(actual[n] == original[n] for n in actual), 'source changed')
    require(set(archived) == set(original) - set(retained), 'missing or extra archived sources')
    require(not list(root.glob('*.tmp')), 'unfinished archive left behind')


def exercise(driver, case, directory):
    """driver(request) synchronously invokes injected-clock production archive operation."""
    require(case in CASES, 'unknown case')
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=False)
    ages = {'old.txt': AGE_NS + 100}
    if case == 'boundary':
        ages.update({'exact.txt': AGE_NS, 'young.txt': AGE_NS - 100})
    if case == 'exclusions':
        ages.update({'locked.txt': AGE_NS + 100, 'active.txt': AGE_NS + 100})
    if case == 'partial_retry':
        ages['second.txt'] = AGE_NS + 100
    original = {name: ('synthetic fixture ' + name).encode() for name in ages}
    for name, age in ages.items():
        path = root / name
        path.write_bytes(original[name])
        os.utime(path, ns=(NOW_NS - age, NOW_NS - age))
        require(path.stat().st_mtime_ns == NOW_NS - age, 'filesystem lacks 100ns fixture precision')
    request = dict(schema=1, operation='archive', root=str(root.resolve()), now_ns=NOW_NS,
                   active=['active.txt'] if case == 'exclusions' else [],
                   fault={'verification_failure': 'verify', 'write_failure': 'write',
                          'partial_retry': 'after_first_commit'}.get(case))
    lock = exclusive_lock(root / 'locked.txt') if case == 'exclusions' else contextlib.nullcontext()
    with lock:
        driver(request)
    if case in ('verification_failure', 'write_failure'):
        inspect(root, original, original)
    elif case == 'partial_retry':
        remaining = [p.name for p in root.glob('*.txt')]
        require(len(remaining) == 1, 'fault must occur after exactly one committed source')
        inspect(root, original, remaining)
    else:
        retained = {'exact.txt', 'young.txt'} if case == 'boundary' else {'active.txt', 'locked.txt'}
        inspect(root, original, retained)
    request['fault'] = None
    # Unlock is intentional; active marker remains. Retry twice proves recovery/idempotency.
    retained = {'exact.txt', 'young.txt'} if case == 'boundary' else ({'active.txt'} if case == 'exclusions' else set())
    for _ in range(2):
        driver(request)
        inspect(root, original, retained)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--driver', required=True)
    parser.add_argument('--evidence', required=True)
    args = parser.parse_args()
    evidence = Path(args.evidence).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix='archive-qa-', dir=evidence) as temporary:
        def driver(request):
            request_path = Path(temporary) / 'request.json'
            request_path.write_text(json.dumps(request), encoding='utf-8')
            subprocess.run(['pwsh', '-NoProfile', '-File', str(Path(args.driver).resolve()),
                            '-RequestPath', str(request_path)], check=True, capture_output=True)
        for case in CASES:
            try:
                exercise(driver, case, Path(temporary) / case)
                results.append(dict(case=case, status='pass'))
            except Exception as error:
                # No driver output/private data in report.
                results.append(dict(case=case, status='fail', error_type=type(error).__name__))
    target = evidence / 'archive.json'
    temporary_report = target.with_suffix('.json.tmp')
    temporary_report.write_text(json.dumps(dict(schema=1, results=results), indent=2), encoding='utf-8')
    os.replace(temporary_report, target)
    return int(any(r['status'] != 'pass' for r in results))


if __name__ == '__main__':
    raise SystemExit(main())
