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
        for trigger, replacement in self.replacements.items():
            if not trigger.strip():
                continue
            # Match whole phrase or word, case-insensitively
            pattern = re.compile(r'\b' + re.escape(trigger.strip()) + r'\b', re.IGNORECASE)
            self._compiled_patterns.append((pattern, replacement))

    def apply(self, text: str) -> str:
        """Applies all vocabulary replacement rules to the input text."""
        if not text or not self._compiled_patterns:
            return text

        result = text
        for pattern, replacement in self._compiled_patterns:
            result = pattern.sub(replacement, result)

        return result
