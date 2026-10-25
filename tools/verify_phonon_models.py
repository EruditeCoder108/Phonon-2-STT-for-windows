"""Compare cached speech models on public audio, without recording or typing.

Uses an owned server on a free loopback port per model. Downloads are disabled;
cache models first with `fermion transcribe MODEL --download-only`.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import socket
import sys
import time
import urllib.request
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import DEFAULT_PHONON_MODEL, PHONON_MODELS
from src.core.engine import PhononEngine
from tools.benchmark_whistle import word_error_rate
from tools.verify_live_typing import REFERENCE


def process_tree(root_pid):
    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]
    kernel = ctypes.windll.kernel32
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError('Could not inspect the owned speech process tree')
    entries = []
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(entry)
        available = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while available:
            entries.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID), entry.szExeFile))
            available = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    wanted = {root_pid}
    while True:
        expanded = wanted | {pid for pid, parent, _ in entries if parent in wanted}
        if expanded == wanted:
            return [(pid, name) for pid, _, name in entries if pid in wanted]
        wanted = expanded


def memory_mib(process):
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
    query = ctypes.windll.psapi.GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    rows = []
    for pid, name in process_tree(process.pid):
        handle = kernel.OpenProcess(0x410, False, pid)
        if not handle:
            raise OSError(f'Cannot measure owned child {pid}')
        try:
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if not query(handle, ctypes.byref(counters), counters.cb):
                raise OSError(f'Cannot query memory of owned child {pid}')
            rows.append({'pid': pid, 'name': name,
                         'working_set_mib': round(counters.WorkingSetSize / 2**20, 1),
                         'peak_working_set_mib': round(counters.PeakWorkingSetSize / 2**20, 1)})
        finally:
            kernel.CloseHandle(handle)
    return {'processes': rows, 'working_set_mib': round(sum(r['working_set_mib'] for r in rows), 1),
            'memory_measurement': 'Sum of owned launcher and descendant working sets; shared pages may be counted twice.'}


def read_pcm(path):
    with wave.open(str(path)) as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
            raise ValueError(f'{path} must be 16 kHz, mono, 16-bit PCM')
        return audio.readframes(audio.getnframes())


def verify(model, directory):
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    engine = PhononEngine(model=model, port=port)
    report = {'model': model, 'download_disabled': True}
    try:
        start = time.perf_counter()
        engine.start_server()
        report['cached_startup_s'] = round(time.perf_counter() - start, 3)
        with engine._health_opener.open(engine.base_url + '/health', timeout=3) as response:
            health = json.load(response)
        report['health'] = {k: health.get(k) for k in ('model', 'repo', 'version', 'kind', 'sha256', 'decode')}
        pcm = read_pcm(directory / 'jfk.wav')
        samples = []
        for _ in range(3):
            start = time.perf_counter()
            result = engine.transcribe(pcm)
            samples.append(round(time.perf_counter() - start, 3))
            assert not result.error, 'Speech request failed'
            assert word_error_rate(REFERENCE, result.text) == 0, 'Public fixture words changed'
            assert result.words, 'Word timestamps missing'
            assert all(0 <= w['start'] <= w['end'] <= len(pcm) / 32000 + .1 for w in result.words)
        report.update(transcript=result.text, reference_wer=word_error_rate(REFERENCE, result.text),
                      word_timestamps=len(result.words), speech_seconds=round(len(pcm) / 32000, 3),
                      full_clip_request_s=samples)
        with ThreadPoolExecutor(max_workers=2) as pool:
            simultaneous = list(pool.map(lambda _: engine.transcribe(pcm), range(2)))
        assert all(not r.error and word_error_rate(REFERENCE, r.text) == 0 for r in simultaneous)
        report['concurrent_requests_passed'] = True
        report['negative_audio'] = {}
        for filename in ('silence.wav', 'noise.wav'):
            result = engine.transcribe(read_pcm(directory / filename))
            report['negative_audio'][filename] = {'text': result.text, 'error': result.error}
        report['negative_audio_passed'] = all(not r['error'] and not r['text'].strip()
                                               for r in report['negative_audio'].values())
        report.update(memory_mib(engine._process))
        if model == DEFAULT_PHONON_MODEL:
            old_pid = engine._process.pid
            engine._terminate_process()
            deadline = time.monotonic() + 3
            while process_tree(old_pid) and time.monotonic() < deadline:
                time.sleep(.05)
            assert not process_tree(old_pid), 'Restart left an old speech descendant running'
            engine.start_watchdog(interval=.05, failures_before_restart=1)
            deadline = time.monotonic() + 30
            while not engine._is_ready and time.monotonic() < deadline:
                time.sleep(.05)
            assert engine._is_ready and engine.is_server_running(), 'Watchdog failed to recover'
            recovered = engine.transcribe(pcm)
            assert not recovered.error and word_error_rate(REFERENCE, recovered.text) == 0
            report['watchdog_recovery_passed'] = True
        report['passed'] = report['negative_audio_passed']
    except Exception as error:
        report.update(passed=False, error=str(error))
    finally:
        child = engine._process
        engine.stop_server()
        report['owned_server_stopped'] = child is None or child.poll() is not None
        if child is not None:
            deadline = time.monotonic() + 3
            while process_tree(child.pid) and time.monotonic() < deadline:
                time.sleep(.05)
            report['owned_descendants_stopped'] = not process_tree(child.pid)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', nargs='+', choices=list(PHONON_MODELS), default=[DEFAULT_PHONON_MODEL])
    parser.add_argument('--fixtures', type=Path, default=Path('.model-research'))
    args = parser.parse_args()
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    reports = []
    for model in args.models:
        report = verify(model, args.fixtures)
        reports.append(report)
        print(json.dumps(report, indent=2), flush=True)
    target = args.fixtures / 'phonon-model-verification.json'
    target.write_text(json.dumps(reports, indent=2), encoding='utf-8')
    if not all(r['passed'] and r['owned_server_stopped'] and r.get('owned_descendants_stopped') for r in reports):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
