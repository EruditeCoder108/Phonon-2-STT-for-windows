"""
Live Progressive Text Committer (Lookahead Streaming)

Receives in-progress partial transcription text and commits settled words
directly into the active Windows foreground window in real-time as you speak.
Eliminates end-of-speech latency by typing words progressively.
"""

import threading
import logging
from typing import List, Callable, Optional
from src.core.injector import type_unicode_chars

logger = logging.getLogger(__name__)


class ProgressiveTextCommitter:
    """
    Manages real-time word-by-word injection into the active cursor position.
    
    Uses a lookahead buffer:
    - Words with at least `lookahead_words` following them in the partial string
      are considered 'settled' and typed immediately via SendInput Unicode.
    - When a final sentence arrives or session ends, all remaining uncommitted
      words are committed with proper punctuation.
    """

    def __init__(
        self,
        lookahead_words: int = 1,
        text_transform: Optional[Callable[[str], str]] = None,
    ):
        self.lookahead_words = max(1, lookahead_words)
        self.text_transform = text_transform

        self._committed_words: List[str] = []
        self._lock = threading.Lock()
        self._is_first_word = True

    def reset(self):
        """Resets committer state for a new dictation session."""
        with self._lock:
            self._committed_words.clear()
            self._is_first_word = True

    def process_partial(self, partial_text: str):
        """
        Evaluates a new partial transcription string.
        Extracts and types settled words that have enough lookahead words after them.
        """
        if not partial_text:
            return

        words = partial_text.strip().split()
        if len(words) <= self.lookahead_words:
            # Need more words before we can consider the early words settled
            return

        with self._lock:
            # Words up to (len - lookahead) are settled
            settled_count = len(words) - self.lookahead_words
            already_committed = len(self._committed_words)

            if settled_count > already_committed:
                new_words = words[already_committed:settled_count]
                self._type_words(new_words)
                self._committed_words.extend(new_words)

    def process_final(self, final_text: str):
        """
        Commits any remaining uncommitted words from the finalized sentence.
        """
        if not final_text:
            return

        final_words = final_text.strip().split()

        with self._lock:
            already_committed = len(self._committed_words)
            if len(final_words) > already_committed:
                remaining_words = final_words[already_committed:]
                self._type_words(remaining_words)
                self._committed_words.extend(remaining_words)

    def flush(self):
        """Signals end of session."""
        with self._lock:
            self._committed_words.clear()
            self._is_first_word = True

    def _type_words(self, words: List[str]):
        if not words:
            return

        text_to_type = " ".join(words)

        # Apply custom vocabulary / replacements if configured
        if self.text_transform:
            text_to_type = self.text_transform(text_to_type)

        # Prepend space if not the first word of the session
        prefix = "" if self._is_first_word else " "
        self._is_first_word = False

        full_output = prefix + text_to_type
        logger.debug(f"Progressive commit: '{full_output}'")

        # Type directly into the active foreground window using Win32 SendInput Unicode
        type_unicode_chars(full_output)
