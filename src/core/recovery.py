"""Bounded session-only transcripts, independent of optional on-disk history."""
from collections import deque
from dataclasses import dataclass
import threading


@dataclass(frozen=True)
class TranscriptEntry:
    sequence: int
    text: str
    status: str
    reason: str = ""


class TranscriptRecovery:
    def __init__(self, limit=20):
        self._entries = deque(maxlen=limit)
        self._sequence = 0
        self._lock = threading.Lock()

    def add(self, text, status, reason=""):
        if not text.strip():
            return
        with self._lock:
            self._sequence += 1
            self._entries.append(TranscriptEntry(self._sequence, text[:20000], status, reason))

    def entries(self):
        with self._lock:
            return list(reversed(self._entries))

    def clear(self):
        with self._lock:
            self._entries.clear()
