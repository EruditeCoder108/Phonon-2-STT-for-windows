"""Local Whistle probe: timings, timestamps and optional reference-word error rate.

Requires downloaded official needle.exe + whistle.cact. No microphone or text injection.
Each run starts a fresh process, so wall time includes startup and model loading.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import time
import wave


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Case/punctuation-insensitive edit rate; not the full Whisper benchmark normalizer."""
    ref = re.findall(r"\w+(?:'\w+)?", reference.lower())
    hyp = re.findall(r"\w+(?:'\w+)?", hypothesis.lower())
    if not ref:
        raise ValueError('Reference must contain words.')
    previous = list(range(len(hyp) + 1))
    for i, expected in enumerate(ref, 1):
        current = [i]
        for j, actual in enumerate(hyp, 1):
            current.append(min(previous[j] + 1, current[j-1] + 1,
                               previous[j-1] + (expected != actual)))
        previous = current
    return previous[-1] / len(ref)


def probe(engine: Path, model: Path, audio: Path, runs: int = 3, reference: str | None = None):
    for path in [engine, model, audio]:
        if not path.is_file():
            raise FileNotFoundError(path)
    with wave.open(str(audio)) as wav:
        duration = wav.getnframes() / wav.getframerate()
    results = []
    for _ in range(runs):
        start = time.perf_counter()
        child = subprocess.run(
            [str(engine.resolve()), '--model', str(model.resolve()), '--audio', str(audio.resolve()),
             '--audio-language', 'en', '--audio-word-timestamps'],
            capture_output=True, text=True, encoding='utf-8', timeout=60,
            creationflags=0x08000000 if os.name == 'nt' else 0,
            env={**os.environ, 'NEEDLE_TELEMETRY': '0', 'DO_NOT_TRACK': '1'},
        )
        elapsed = time.perf_counter() - start
        if child.returncode:
            raise RuntimeError(f'Whistle exited {child.returncode}: {child.stderr[:400]}')
        payload = json.loads(child.stdout)
        if not isinstance(payload.get('text'), str):
            raise ValueError('Runtime did not return a text transcript.')
        entry = {**payload, 'process_wall_ms': round(elapsed * 1000, 2),
                 'process_real_time_factor': round(elapsed / duration, 4) if duration else None}
        if reference is not None:
            entry['reference_wer'] = word_error_rate(reference, payload['text'])
        results.append(entry)
    return {
        'audio': str(audio), 'audio_seconds': duration, 'runs': results,
        'model_bytes': model.stat().st_size,
        'model_sha256': hashlib.sha256(model.read_bytes()).hexdigest(),
        'runtime_sha256': hashlib.sha256(engine.read_bytes()).hexdigest(),
        'median_process_wall_ms': statistics.median(r['process_wall_ms'] for r in results),
        'measurement': 'Fresh CLI process each run; includes startup/model load. Not live-dictation latency.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audio', type=Path)
    parser.add_argument('--engine', type=Path, default=Path('.model-research/needle.exe'))
    parser.add_argument('--model', type=Path, default=Path('.model-research/whistle.cact'))
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--reference', help='Known transcript for this clip; optional simple WER.')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if not 1 <= args.runs <= 100:
        parser.error('--runs must be 1..100')
    result = probe(args.engine, args.model, args.audio, args.runs, args.reference)
    output = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
