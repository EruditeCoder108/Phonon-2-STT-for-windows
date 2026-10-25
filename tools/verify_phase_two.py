"""Exercise the actual Whistle adapter and dictation pipeline on local test WAVs.

Uses public/fixture audio only; no microphone, global hook or text injection.
Run from the repository root after obtaining .model-research/jfk.wav,
silence.wav and noise.wav (see docs/model-research.md).
"""
import json
from pathlib import Path
import statistics
import sys
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.core.pipeline import DictationPipeline
from src.core.segmenter import Utterance
from src.core.vocabulary import VocabularyEngine
from src.core.whistle import WhistleEngine
from tools.benchmark_whistle import word_error_rate

REFERENCE = "And so my fellow Americans ask not what your country can do for you ask what you can do for your country"


def read_pcm(path):
    with wave.open(str(path)) as source:
        assert (source.getframerate(), source.getnchannels(), source.getsampwidth()) == (16000, 1, 2)
        return source.readframes(source.getnframes())


def main():
    root = Path(__file__).resolve().parents[1]
    engine = WhistleEngine()
    start = time.perf_counter()
    engine.start_server()
    report = {"startup_ms": round((time.perf_counter() - start) * 1000, 2), "speech_runs": []}
    try:
        pcm = read_pcm(root / ".model-research" / "jfk.wav")
        for _ in range(3):
            start = time.perf_counter()
            result = engine.transcribe(pcm)
            assert not result.error and result.timed
            wer = word_error_rate(REFERENCE, result.text)
            assert wer == 0, result.text
            report["speech_runs"].append({"wall_ms": round((time.perf_counter() - start) * 1000, 2),
                                          "wer": wer, "word_count": len(result.words)})
        report["median_speech_ms"] = statistics.median(r["wall_ms"] for r in report["speech_runs"])
        for filename in ("silence", "noise"):
            result = engine.transcribe(read_pcm(root / ".model-research" / f"{filename}.wav"))
            assert not result.error and result.text == "", result
            report[filename] = "empty transcript"
        output = []
        pipeline = DictationPipeline(engine, VocabularyEngine({}), inject=lambda text: output.append(text) or True,
                                     get_hwnd=lambda: 1)
        duration = len(pcm) / 32000
        utterance = Utterance(pcm, 0, duration, duration, .99, 3000, 40, target_hwnd=1)
        pipeline.process(utterance)
        pipeline.flush("integration verification")
        assert word_error_rate(REFERENCE, "".join(output)) == 0, output
        report["pipeline"] = "Whistle words and final punctuation reached the output collector"
        engine.request_stop()
        final = engine.transcribe(pcm)
        assert not final.error and word_error_rate(REFERENCE, final.text) == 0
        report["shutdown_drain"] = "final queued decode succeeds after request_stop"
    finally:
        engine.stop_server()
    target = root / ".model-research" / "phase-two-verification.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
