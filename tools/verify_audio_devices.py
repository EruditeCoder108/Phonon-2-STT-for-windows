"""Check enumeration and live capture callbacks without saving or transcribing audio.

With --open, briefly open the named/default mic, close it, then test a hardware
refresh and reopen. Useful after attaching USB hardware. No user settings change.
"""
import argparse
import json
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.core import devices
from src.core.audio import AudioCaptureEngine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--open", action="store_true", help="Test live capture callbacks (no audio saved)")
    parser.add_argument("--mic", help="Full microphone name; default uses Windows default")
    args = parser.parse_args()
    report = {"microphones": devices.list_microphones(), "default": devices.default_microphone_name(),
              "fresh_scan": devices.fresh_scan()}
    if args.open:
        callback = threading.Event()
        capture = AudioCaptureEngine(device_name=args.mic, on_level_update=lambda level: callback.set())
        try:
            capture.warm_up()
            assert callback.wait(3), "Microphone opened but delivered no callbacks"
            assert not capture.needs_recovery, "Stream failed immediately after opening"
            report["opened"] = capture.active_device
            report["fallback"] = capture.fallback_reason
            callback.clear()
            capture.reopen()
            # close sends a zero level; clear it after reopen before checking live frames
            callback.clear()
            assert callback.wait(3), "Reopened microphone delivered no callbacks"
            assert not capture.needs_recovery
            report["reopened"] = capture.active_device
        finally:
            capture.close_stream()
        assert capture._stream is None
        report["closed"] = True
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
