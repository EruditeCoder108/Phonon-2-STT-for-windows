"""
Dictation Pipeline — utterance in, correctly punctuated text out.

The speech model decodes each clip as a standalone sentence and has no text-prompt context. So
when you pause mid-sentence, the clip that follows starts with a capital letter and the clip
before it already ended with a period: "I was thinking. About going to the store."

Two techniques fix that without changing the model:

  1. Audio context. A phrase that follows closely after the previous one is decoded together
     with the previous phrase's audio (plus a short gap). Word timestamps tell us which words
     are new, and the model now hears one continuous sentence, so casing and punctuation at the
     join come out right.

  2. Deferred sentence-final punctuation. The punctuation at the END of a phrase ("." "?" ",")
     is held back and typed only once we know what follows: the next phrase's decode tells us
     what the join should be, or, if nothing follows within a short hold, we type it as-is.
     Words themselves are typed immediately; only the trailing mark waits.

Everything runs on the single transcription worker thread, in strict FIFO order.
"""

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import numpy as np

from src.core.engine import TranscribeResult
from src.core.filters import judge, normalized_words
from src.core.segmenter import Utterance

logger = logging.getLogger(__name__)

BYTES_PER_SEC = 32000                      # 16 kHz * 2 bytes
CONTEXT_GAP_S = 3.0                        # max pause between phrases that still counts as "same thought"
CONTEXT_MAX_S = 8.0                        # most previous audio re-fed as context
CONTEXT_SILENCE_S = 0.12                   # synthetic gap between context and new phrase. Measured on 8 phrase pairs:
                                           # 0.10s joined 10/10 mid-sentence splits, 0.35s only 8/10, 0.50s 6/10, while
                                           # real sentence boundaries stayed intact at every setting.
TAIL_PAD_S = 0.30                          # room-level noise appended to every decode so the model hears a clear
                                           # end of speech (steadies the closing '.' vs ',')
PUNCT_HOLD_S = 2.0                         # how long a deferred trailing mark waits for a follow-up phrase

_TRAIL_RE = re.compile(r"[.,!?;:…\"”’)\]]+$")


# Hesitation sounds the model sometimes writes into the middle of a sentence ("so, uh, I think").
FILLER_WORDS = {"uh", "um", "umm", "uhh", "uhm", "er", "erm", "hmm", "hm", "hmmm", "mm", "mmm", "mhm", "uh-huh"}
_NON_WORD_RE = re.compile(r"[^\w'-]")


def _pad(pcm: bytes, noise_rms: float) -> bytes:
    """Appends TAIL_PAD_S of room-level noise (deterministic) to the audio sent for decoding."""
    rng = np.random.default_rng(0)
    noise = rng.standard_normal(int(TAIL_PAD_S * 16000)) * min(max(noise_rms, 10.0), 120.0)
    return pcm + noise.astype(np.int16).tobytes()


def split_trail(token: str) -> Tuple[str, str]:
    """'store.' -> ('store', '.'); 'it' -> ('it', '')."""
    m = _TRAIL_RE.search(token)
    if not m or m.start() == 0:
        return token, ""
    return token[:m.start()], token[m.start():]


def strip_fillers(tokens: List[str], capitalize_next: bool) -> List[str]:
    """Removes hesitation words, keeping any sentence-ending mark and sentence-initial capital."""
    out: List[str] = []
    cap_pending = False
    for tok in tokens:
        core = _NON_WORD_RE.sub("", tok).lower()
        if core not in FILLER_WORDS:
            if cap_pending and tok[:1].islower():
                tok = tok[:1].upper() + tok[1:]
            cap_pending = False
            out.append(tok)
            continue
        _, trail = split_trail(tok)
        if out and trail and trail[0] in ".?!\u2026" and not split_trail(out[-1])[1]:
            out[-1] += trail                      # "...done. Um," -> "...done."
        if not out and capitalize_next and tok[:1].isupper():
            cap_pending = True                    # "Um the report" -> "The report"
    return out


@dataclass
class _Prev:
    pcm: bytes
    end_s: float
    trail: str                 # deferred trailing punctuation as the standalone decode produced it
    last_word: str
    hwnd: int
    deadline: float


class DictationPipeline:
    def __init__(
        self,
        engine,
        vocab,
        inject: Callable[[str], bool],
        history=None,
        get_config: Callable[[str, object], object] = lambda k, d=None: d,
        get_hwnd: Callable[[], int] = lambda: 0,
        on_event: Optional[Callable[[str, str], None]] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.engine = engine
        self.vocab = vocab
        self.inject = inject
        self.history = history
        self.get_config = get_config
        self.get_hwnd = get_hwnd
        self.on_event = on_event            # (kind, message): "error" | "blocked"
        self.clock = clock
        self.speech_active = threading.Event()
        self._prev: Optional[_Prev] = None

    def _shown(self, text: str) -> str:
        """Dictated text only goes into logs when the user keeps history on (privacy)."""
        if self.history is not None and not getattr(self.history, "enabled", True):
            return "<hidden>"
        return text

    @staticmethod
    def _span(words) -> Optional[float]:
        return (words[-1]["end"] - words[0]["start"]) if words else None

    # ── Session control ──

    def reset(self):
        """Forget all context (new dictation session). Does not type anything."""
        self._prev = None

    def set_speech_active(self, active: bool):
        if active:
            self.speech_active.set()
        else:
            self.speech_active.clear()

    def tick(self):
        """Call periodically from the worker; types a deferred mark once nothing follows it."""
        prev = self._prev
        if prev and self.clock() >= prev.deadline and not self.speech_active.is_set():
            self.flush("hold expired")

    def flush(self, reason: str = ""):
        """Types the deferred trailing mark (and the separating space), then drops the context."""
        prev, self._prev = self._prev, None
        if not prev:
            return
        hwnd = self.get_hwnd()
        if prev.hwnd and hwnd and hwnd != prev.hwnd:
            logger.debug(f"Dropped deferred '{prev.trail}' — focus moved to another window ({reason}).")
            return
        self._emit(prev.trail + " ")
        logger.debug(f"Flushed deferred '{prev.trail}' ({reason}).")

    # ── Main entry ──

    def process(self, utt: Utterance):
        prev = self._prev
        stitched = False
        ctx_punct = ""
        tokens: List[str] = []
        span: Optional[float] = None

        if (prev and self.get_config("context_stitch", True)
                and -0.5 <= utt.start_s - prev.end_s <= CONTEXT_GAP_S):
            res, tokens, ctx_punct, span = self._decode_stitched(prev, utt)
            if res.error:
                self._report("error", "Speech server did not respond.")
                return
            stitched = bool(tokens)

        if not stitched:
            res = self.engine.transcribe(_pad(utt.pcm, utt.noise_floor))
            if res.error:
                self._report("error", "Speech server did not respond.")
                return
            tokens = res.text.split()
            span = self._span(res.words)

        if self.get_config("remove_fillers", True):
            tokens = strip_fillers(tokens, capitalize_next=not stitched)

        text = " ".join(tokens)
        keep, reason = judge(text, utt, word_span_s=span)
        ratio = "n/a" if utt.level_ratio is None else f"{utt.level_ratio:.2f}"
        logger.info(f"utt dur={utt.duration_s:.1f}s voiced={utt.speech_s:.2f}s p={utt.mean_prob:.2f} "
                    f"rms={utt.speech_rms:.0f} floor={utt.noise_floor:.0f} level={ratio} -> "
                    f"{'KEEP' if keep else 'DROP: ' + reason} {self._shown(repr(text))}")
        if not keep:
            return

        body_tokens = list(tokens)
        body_tokens[-1], trail = split_trail(body_tokens[-1])
        body = self.vocab.apply(" ".join(body_tokens).strip())
        if not body:
            return

        lead = ""
        if prev:
            lead = (ctx_punct if stitched else prev.trail) + " "

        if not self._emit(lead + body):
            self._report("blocked", "Windows blocked typing into the focused window "
                                    "(it may be running as administrator).")

        self._prev = _Prev(
            pcm=utt.pcm,
            end_s=utt.end_s,
            trail=trail,
            last_word=(normalized_words(body_tokens[-1]) or [""])[-1],
            hwnd=self.get_hwnd(),
            deadline=self.clock() + PUNCT_HOLD_S,
        )
        if self.history is not None:
            self.history.add_entry(body + trail, duration_sec=utt.speech_s)

    # ── Helpers ──

    def _decode_stitched(self, prev: _Prev, utt: Utterance) -> Tuple[TranscribeResult, List[str], str, Optional[float]]:
        """Decodes previous-phrase audio + gap + this phrase; returns only the NEW words.

        Returns (result, new_tokens, punctuation_after_the_previous_phrase, new_word_span_s). `new_tokens` is
        empty when stitching could not be trusted, and the caller falls back to a plain decode.
        """
        start = max(0, len(prev.pcm) - int(CONTEXT_MAX_S * BYTES_PER_SEC))
        start -= start % 2
        ctx = prev.pcm[start:]
        gap = b"\x00" * int(CONTEXT_SILENCE_S * BYTES_PER_SEC)
        ctx_dur = len(ctx) / BYTES_PER_SEC
        boundary = ctx_dur + CONTEXT_SILENCE_S / 2

        res = self.engine.transcribe(_pad(ctx + gap + utt.pcm, utt.noise_floor))
        if res.error or not res.words:
            return res, [], "", None

        new_words, ctx_words = [], []
        for w in res.words:
            (new_words if (w["start"] + w["end"]) / 2 > boundary else ctx_words).append(w)
        if not new_words:
            return res, [], "", None

        if ctx_words:
            _, ctx_punct = split_trail(ctx_words[-1]["word"])
            heard = (normalized_words(ctx_words[-1]["word"]) or [""])[-1]
            if prev.last_word and heard != prev.last_word:
                logger.debug(f"Stitch context mismatch: typed '{prev.last_word}', heard '{heard}'")
        else:
            ctx_punct = prev.trail
        return res, [w["word"] for w in new_words], ctx_punct, self._span(new_words)

    def _emit(self, text: str) -> bool:
        try:
            return bool(self.inject(text))
        except Exception as e:
            logger.error(f"Injection error: {e}", exc_info=True)
            return False

    def _report(self, kind: str, message: str):
        logger.warning(message)
        if self.on_event:
            try:
                self.on_event(kind, message)
            except Exception:
                pass
