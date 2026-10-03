"""
Live microphone diagnostic — see what Phonon-2 hears and decides, without typing anything.

    python tools/diagnose.py

Speak, hum, cough, play a video, type on the keyboard. For every sound the app would treat as an
utterance it prints what was measured and whether it would be TYPED or DROPPED (and why).
Press Ctrl+C to stop. Reuses the speech server if the app already started it.
"""

import os
import queue
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from src.config import load_config
from src.core.audio import AudioCaptureEngine
from src.core.engine import PhononEngine
from src.core.filters import judge
from src.core.pipeline import strip_fillers


def main():
    cfg = load_config()
    engine = PhononEngine(port=cfg.get("port", 8010), threads=cfg.get("engine_threads"))
    print("Starting / connecting to the speech server...")
    engine.start_server(wait_timeout=180)

    utts: "queue.Queue" = queue.Queue()
    activity = {"on": False}
    audio = AudioCaptureEngine(
        device_index=cfg.get("mic_index"),
        on_utterance=utts.put,
        on_activity=lambda a: activity.__setitem__("on", a),
        pause_ms=cfg.get("pause_ms", 700),
        voice_gate=cfg.get("voice_gate", True),
    )
    audio.warm_up()
    audio.start()
    print(f"\nListening. Voice level: learns from your first phrase  "
          f"(background gate {'ON' if cfg.get('voice_gate', True) else 'OFF'})")
    print("Speak, hum, play a video, type... Ctrl+C to stop.\n")
    print("  legend: voiced=seconds the VAD heard speech | p=VAD confidence | rms=loudness of your voice")
    print("          floor=room noise | level=loudness relative to your usual voice (<0.25 = background)\n")

    try:
        while True:
            try:
                u = utts.get(timeout=0.3)
            except queue.Empty:
                continue
            t0 = time.time()
            res = engine.transcribe(u.pcm)
            took = (time.time() - t0) * 1000
            if res.error:
                print("  !! speech server did not respond")
                continue
            tokens = res.text.split()
            if cfg.get("remove_fillers", True):
                tokens = strip_fillers(tokens, capitalize_next=True)
            text = " ".join(tokens)
            span = (res.words[-1]["end"] - res.words[0]["start"]) if res.words else None
            keep, why = judge(text, u, word_span_s=span)
            ratio = "n/a " if u.level_ratio is None else f"{u.level_ratio:.2f}"
            verdict = "TYPED  " if keep else f"DROPPED ({why})"
            print(f"  [{time.strftime('%H:%M:%S')}] {u.duration_s:4.1f}s voiced={u.speech_s:.2f} p={u.mean_prob:.2f} "
                  f"rms={u.speech_rms:5.0f} floor={u.noise_floor:3.0f} level={ratio} ({took:3.0f} ms)\n"
                  f"             heard: {res.text!r}\n             -> {verdict}")
    except KeyboardInterrupt:
        pass
    finally:
        audio.stop()
        audio.close_stream()
        engine.stop_server()   # no-op if the running app owns the server


if __name__ == "__main__":
    main()
