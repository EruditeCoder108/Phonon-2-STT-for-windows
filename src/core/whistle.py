"""Whistle adapter for the official, pinned Windows Needle CLI.

Audio stays in a private temporary directory, removed after every request. Each
decode is a fresh child process; no persistent speech HTTP server is required.
"""
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import urllib.request

from src.core.engine import PhononEngine, TranscribeResult, _create_kill_on_close_job, _close_job

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
ASSETS = {
    "needle.exe": (
        "https://huggingface.co/Cactus-Compute/needle3/resolve/2ae11323dc000f5e70c49f7403efa6af12ba9e67/windows-x86_64/needle.exe",
        "e8863ca0c06a47d406777077f1ba728b58e57d77fedff252dbe6827d588acc8c",
    ),
    "whistle.cact": (
        "https://huggingface.co/Cactus-Compute/whistle/resolve/b358ddadd89b7a713b5aa131f23032d3cca1b251/whistle.cact",
        "b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb",
    ),
}


def verified(path: Path, digest: str) -> bool:
    if not path.is_file():
        return False
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest() == digest


def ensure_assets(directory: Path, cancelled: threading.Event):
    """Reuse verified research downloads or fetch pinned files atomically."""
    directory.mkdir(parents=True, exist_ok=True)
    for filename, (url, digest) in ASSETS.items():
        if cancelled.is_set():
            raise RuntimeError("Whistle loading cancelled.")
        target = directory / filename
        if verified(target, digest):
            continue
        # A unique file prevents two app instances/installers sharing a partial download.
        handle, temporary = tempfile.mkstemp(prefix=filename, suffix=".part", dir=directory)
        os.close(handle)
        part = Path(temporary)
        try:
            research = ROOT / ".model-research" / filename
            if verified(research, digest):
                shutil.copyfile(research, part)
            else:
                with urllib.request.urlopen(url, timeout=30) as response, part.open("wb") as output:
                    total = 0
                    while chunk := response.read(256 * 1024):
                        if cancelled.is_set():
                            raise RuntimeError("Whistle download cancelled.")
                        total += len(chunk)
                        if total > 32 * 1024 * 1024:
                            raise ValueError("Whistle download exceeded its size limit.")
                        output.write(chunk)
            if not verified(part, digest):
                raise ValueError(f"Whistle checksum mismatch: {filename}")
            if cancelled.is_set():
                raise RuntimeError("Whistle loading cancelled.")
            os.replace(part, target)
        finally:
            part.unlink(missing_ok=True)


class WhistleEngine:
    name = "Whistle"
    max_audio_seconds = 30.0
    live_guard_words = 2
    stable_hypotheses = 3

    def __init__(self, directory=None, threads=None, timeout=60):
        self.directory = Path(directory) if directory else ROOT / ".models" / "whistle"
        self.threads = threads
        self.timeout = timeout
        self._is_ready = False
        self.on_state_change = None
        self._stop_event = threading.Event()
        self._start_lock = threading.Lock()
        self._decode_lock = threading.Lock()
        self._process_lock = threading.Lock()
        self._process = None
        self._job = _create_kill_on_close_job()

    def _state(self, ready, message):
        self._is_ready = ready
        if self.on_state_change:
            self.on_state_change(ready, message)

    def start_server(self, wait_timeout=120):
        with self._start_lock:
            if self._stop_event.is_set():
                raise RuntimeError("Whistle is shutting down.")
            self._state(False, "Preparing Whistle (first use downloads 18.5 MB)...")
            try:
                ensure_assets(self.directory, self._stop_event)
                # Load and exercise the actual model, not just check that files exist.
                result = self._decode(bytes(16000 * 2), allow_unready=True)
                if result.error or self._stop_event.is_set():
                    raise RuntimeError("Whistle startup test failed.")
                self._state(True, "ready")
            except Exception:
                self._state(False, "Whistle could not load. Re-select it to retry.")
                raise

    def start_watchdog(self, **kwargs):
        # Each request starts a bounded, isolated process; the next request retries
        # after a crashed decoder without restarting the application.
        pass

    def request_stop(self):
        # Preserve already queued final phrases, matching Phonon's drain contract.
        self._stop_event.set()

    def stop_server(self):
        self.request_stop()
        with self._start_lock, self._decode_lock:
            self._is_ready = False
            job, self._job = self._job, None
            _close_job(job)

    def transcribe(self, pcm_bytes, sample_rate=16000):
        return self._decode(pcm_bytes, sample_rate)

    def _decode(self, pcm_bytes, sample_rate=16000, allow_unready=False):
        if not pcm_bytes:
            return TranscribeResult()
        if ((not self._is_ready and not allow_unready) or sample_rate != 16000 or len(pcm_bytes) % 2
                or len(pcm_bytes) > int(self.max_audio_seconds * sample_rate * 2)):
            return TranscribeResult(error=True)
        with self._decode_lock:
            process = None
            try:
                with tempfile.TemporaryDirectory(prefix="phonon-whistle-") as temporary:
                    audio = Path(temporary) / "phrase.wav"
                    audio.write_bytes(PhononEngine._pcm_to_wav(pcm_bytes, sample_rate))
                    args = [str((self.directory / "needle.exe").resolve()), "--model",
                            str((self.directory / "whistle.cact").resolve()), "--audio", str(audio),
                            "--audio-language", "en", "--audio-word-timestamps"]
                    if self.threads:
                        args += ["--threads", str(self.threads)]
                    process = subprocess.Popen(
                        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                        encoding="utf-8", creationflags=0x08000000 if os.name == "nt" else 0,
                        env={**os.environ, "NEEDLE_TELEMETRY": "0", "DO_NOT_TRACK": "1"},
                    )
                    with self._process_lock:
                        self._process = process
                    if self._job and os.name == "nt":
                        import ctypes
                        from ctypes import wintypes
                        try:
                            ctypes.windll.kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
                            ctypes.windll.kernel32.AssignProcessToJobObject(self._job, int(process._handle))
                        except Exception:
                            logger.debug("Whistle job assignment failed", exc_info=True)
                    try:
                        stdout, stderr = process.communicate(timeout=self.timeout)
                    except Exception:
                        # Release the WAV before TemporaryDirectory attempts cleanup
                        # (Windows prevents deleting a file held by the decoder).
                        if process.poll() is None:
                            process.kill()
                        process.communicate()
                        raise
                    if process.returncode:
                        # Runtime output can contain dictated text. Keep it out of logs.
                        raise RuntimeError(f"Whistle exited with code {process.returncode}")
                    payload = json.loads(stdout)
                    if not isinstance(payload.get("text"), str):
                        raise ValueError("Whistle returned an invalid transcript")
                    words = []
                    for word in payload.get("words", []) or []:
                        start, end = float(word["start"]), float(word["end"])
                        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= end):
                            raise ValueError("Whistle returned invalid timestamps")
                        words.append({"word": str(word["word"]), "start": start, "end": end})
                    return TranscribeResult(text=payload["text"].strip(), words=words)
            except Exception as error:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.communicate()
                logger.error("Whistle transcription failed (%s)", type(error).__name__)
                return TranscribeResult(error=True)
            finally:
                with self._process_lock:
                    self._process = None

    def transcribe_wav_bytes(self, pcm_bytes, sample_rate=16000):
        return self.transcribe(pcm_bytes, sample_rate).text
