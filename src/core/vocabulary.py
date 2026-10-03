"""
Custom Vocabulary & Word Replacement Engine

Allows users to define custom text substitutions, acronyms, and formatting rules
that are automatically applied to recognized speech before text is typed.
"""

import re
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class VocabularyEngine:
    def __init__(self, replacements: Dict[str, str] = None):
        self.replacements: Dict[str, str] = replacements or {}
        self._compiled_patterns = []
        self._rebuild_patterns()

    def set_replacements(self, replacements: Dict[str, str]):
        """Updates the replacement dictionary and recompiles regex patterns."""
        self.replacements = replacements.copy()
        self._rebuild_patterns()

    def _rebuild_patterns(self):
        """Compiles case-insensitive word boundary regexes for fast replacement."""
        self._compiled_patterns = []
        # Longest trigger first so "my email address" wins over "my email".
        for trigger, replacement in sorted(self.replacements.items(), key=lambda kv: -len(kv[0].strip())):
            trigger = trigger.strip()
            if not trigger:
                continue
            # \b only works next to a word character; triggers like "c++" need look-arounds.
            left = r'\b' if re.match(r'\w', trigger[0]) else r'(?<!\w)'
            right = r'\b' if re.match(r'\w', trigger[-1]) else r'(?!\w)'
            pattern = re.compile(left + re.escape(trigger) + right, re.IGNORECASE)
            # A function replacement is inserted literally. A plain string has its backslashes
            # parsed as regex escapes (a replacement like C:\Users raised an error and the
            # whole dictated phrase was lost).
            self._compiled_patterns.append((pattern, lambda m, r=replacement: r))

    def apply(self, text: str) -> str:
        """Applies all vocabulary replacement rules to the input text."""
        if not text or not self._compiled_patterns:
            return text

        result = text
        for pattern, replacement in self._compiled_patterns:
            result = pattern.sub(replacement, result)

        return result
