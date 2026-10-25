"""Phonon's live transport. One persistent server, one socket per spoken phrase."""
import json
import threading

from websockets.sync.client import connect

from src.core.engine import TranscribeResult


class PhononStream:
    def __init__(self, engine, on_partial):
        self._connection = connect(f"ws://{engine.host}:{engine.port}/v1/audio/stream",
                               open_timeout=3, close_timeout=1, ping_interval=None,
                               max_size=256 * 1024, max_queue=16, proxy=None)
        self._socket = self._connection.__enter__()
        try:
            self._socket.send(json.dumps({"sample_rate": 16000, "format": "pcm_s16le"}))
        except Exception:
            self._connection.__exit__(None, None, None)
            raise
        self._on_partial = on_partial
        self._done = threading.Event()
        self._error = False
        self._text = ""
        self._finals = []
        self._sent_bytes = 0
        self._reader = threading.Thread(target=self._receive, daemon=True, name="LiveSpeechReader")
        self._reader.start()

    def _receive(self):
        try:
            for raw in self._socket:
                message = json.loads(raw)
                kind = message.get("type")
                text = message.get("text", "")
                if not isinstance(text, str):
                    raise ValueError("Invalid live transcript")
                if kind == "partial":
                    self._on_partial(" ".join([*self._finals, text]).strip())
                elif kind == "final":
                    self._finals.append(text)
                elif kind == "done":
                    self._text = text
                    self._done.set()
                    return
                elif kind == "error":
                    self._error = True
                    return
        except Exception:
            self._error = True
        finally:
            if not self._done.is_set():
                self._error = True
            self._done.set()

    def update(self, pcm):
        if self._done.is_set():
            raise RuntimeError("Live stream ended unexpectedly")
        if len(pcm) < self._sent_bytes:
            raise ValueError("Live audio cannot shrink")
        # Preserve audio, but do not manufacture old intermediate snapshots.
        # The app uses scheduled HTTP previews; this transport is retained for
        # explicit callers that own their own pacing and finalization budget.
        if len(pcm) > self._sent_bytes:
            self._socket.send(pcm[self._sent_bytes:])
        self._sent_bytes = len(pcm)

    def finish(self, pcm, timeout=30):
        try:
            self.update(pcm)
            self._socket.send('{"type":"end"}')
            if not self._done.wait(timeout):
                self._error = True
            return TranscribeResult(text=self._text, error=self._error)
        except Exception:
            return TranscribeResult(error=True)
        finally:
            self.close()

    def close(self):
        self._connection.__exit__(None, None, None)
        if threading.current_thread() is not self._reader:
            self._reader.join(timeout=2)
