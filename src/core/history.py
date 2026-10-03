"""
Dictation History Logger

Persists a searchable log of recent dictation snippets to ~/.phonon2_history.json
so the user can review past text or quickly copy it back to the clipboard.
"""

import json
import os
import time
import threading
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

HISTORY_PATH = os.path.join(os.path.expanduser("~"), ".phonon2_history.json")
MAX_HISTORY_ENTRIES = 100


class HistoryManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.entries: List[Dict] = self._load()

    def _load(self) -> List[Dict]:
        if os.path.exists(HISTORY_PATH):
            try:
                with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.debug(f"Failed to read history file: {e}")
        return []

    def _save(self):
        try:
            with open(HISTORY_PATH, "w", encoding="utf-8") as f:
                json.dump(self.entries, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving dictation history: {e}")

    def add_entry(self, text: str, duration_sec: float = 0.0):
        """Appends a new transcription entry to the history log."""
        if not text or not text.strip():
            return

        entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "text": text.strip(),
            "duration": round(duration_sec, 2),
            "words": len(text.strip().split()),
        }

        with self._lock:
            self.entries.insert(0, entry)  # Prepend newest
            if len(self.entries) > MAX_HISTORY_ENTRIES:
                self.entries = self.entries[:MAX_HISTORY_ENTRIES]
            self._save()

    def get_all(self) -> List[Dict]:
        with self._lock:
            return list(self.entries)

    def clear(self):
        with self._lock:
            self.entries.clear()
            self._save()
