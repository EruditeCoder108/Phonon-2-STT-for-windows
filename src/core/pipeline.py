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
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Callable, List, Optional, Tuple

import numpy as np

from src.core.engine import TranscribeResult
from src.core.filters import judge, normalized_words
from src.core.segmenter import Utterance
from src.core.recovery import TranscriptRecovery

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


def strip_wrapping_quotes(text: str, partial=False) -> str:
    """Remove model-added sentence wrappers, preserving quotes inside the phrase."""
    text = text.strip()
    pairs = {'"': '"', '“': '”', '«': '»'}
    if text[:1] in pairs:
        closing = pairs[text[0]]
        if text.endswith(closing) and len(text) > 1:
            return text[1:-1].strip()
        if partial:
            return text[1:].lstrip()
    return text


def _word_key(token):
    return token.strip('.,!?;:…"“”«»()[]').casefold()


def _canonical(tokens):
    """Keep raw-token boundaries when comparing contractions such as I'm / I am."""
    contractions = {"i'm": "i am", "it's": "it is", "that's": "that is",
                    "we're": "we are", "you're": "you are", "they're": "they are",
                    "i've": "i have", "we've": "we have", "don't": "do not",
                    "doesn't": "does not", "can't": "can not", "cannot": "can not",
                    "won't": "will not", "i'll": "i will", "we'll": "we will"}
    keys, ends = [], []
    for index, token in enumerate(tokens):
        key = _word_key(token).replace('’', "'")
        for word in contractions.get(key, key).split():
            keys.append(word)
            ends.append(index + 1)
    return keys, ends


def continuation_index(committed, final):
    """Locate a unique continuation; a mismatch never authorizes deleting user text."""
    before, _ = _canonical(committed)
    after, ends = _canonical(final)
    count = len(before)
    if after[:count] == before and count <= len(after):
        if not count:
            return 0
        # Never cut halfway through one expanded contraction.
        if count == len(after) or ends[count - 1] != ends[count]:
            return ends[count - 1]
    width = 4 if count >= 8 else 1
    if count < 3:
        return None
    anchor = before[-width:]
    matches = [i + width for i in range(len(after) - width + 1)
               if after[i:i + width] == anchor]
    if len(matches) != 1:
        return None
    cut = matches[0]
    threshold = .85 if count >= 8 else 2 / 3
    if (abs(cut - count) <= 2 and before[0] == after[0]
            and SequenceMatcher(None, before, after[:cut], autojunk=False).ratio() >= threshold
            and (cut == len(after) or ends[cut - 1] != ends[cut])):
        return ends[cut - 1]
    return None


@dataclass
class _LiveState:
    utterance_id: int
    hwnd: int
    previous: List[str]
    committed: List[str]
    blocked: bool = False
    hypotheses: List[List[str]] = field(default_factory=list)


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
    focus: Optional[tuple] = None


@dataclass
class _SpeculativeMatch:
    spec_id: int
    utt_pcm: bytes
    res: TranscribeResult
    tokens: List[str]
    ctx_punct: str
    span: Optional[float]
    stitched: bool
    hwnd: int
    prev: Optional[_Prev]


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
        on_preview: Optional[Callable[[str], None]] = None,
        clock: Callable[[], float] = time.monotonic,
        inject_target: Optional[Callable[[str, int], bool]] = None,
        get_focus: Optional[Callable[[], object]] = None,
    ):
        self.engine = engine
        self.vocab = vocab
        self.inject = inject
        self.inject_target = inject_target
        self.get_focus = get_focus
        self._focus_key = None
        self.history = history
        self.get_config = get_config
        self.get_hwnd = get_hwnd
        self.on_event = on_event            # (kind, message): "error" | "blocked"
        self.on_preview = on_preview        # (text): live partial hypothesis for the HUD
        self.clock = clock
        self.speech_active = threading.Event()
        self._prev: Optional[_Prev] = None
        self._spec_match: Optional[_SpeculativeMatch] = None
        self._cancelled_spec_id: int = 0
        self._cancel_lock = threading.RLock()
        self._live: Optional[_LiveState] = None
        self._live_completed_id = 0
        self.recovery = TranscriptRecovery()
        self._delivery_text = ""
        self._delivery_status = "Not typed"
        self._delivery_reason = ""

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
        self._spec_match = None
        self._live = None
        if self.on_preview:
            try:
                self.on_preview("")
            except Exception:
                pass

    def cancel_speculative(self, spec_id: int):
        """Cancels an in-flight speculative decode (e.g. speaker resumed speaking)."""
        with self._cancel_lock:
            self._cancelled_spec_id = max(self._cancelled_spec_id, spec_id)
            if self.on_preview:
                try:
                    self.on_preview("")
                except Exception:
                    pass

    def _cancelled(self, spec_id: int) -> bool:
        with self._cancel_lock:
            return spec_id <= self._cancelled_spec_id

    def _target(self, utt: Utterance) -> int:
        return self.get_hwnd() if utt.target_hwnd is None else utt.target_hwnd

    def _focus_matches(self, hwnd: int) -> bool:
        return (self.get_hwnd() == hwnd and
                (self.get_focus is None or self._focus_key is None or self.get_focus() == self._focus_key))

    def process_speculative(self, utt: Utterance, spec_id: int):
        """Pre-decodes an in-flight utterance during pause silence so final commit is instant."""
        if self._cancelled(spec_id):
            return
        hwnd = self._target(utt)
        if not self._focus_matches(hwnd):
            return
        prev = self._prev if self._prev and self._prev.hwnd == hwnd else None
        stitched = False
        ctx_punct = ""
        tokens: List[str] = []
        span: Optional[float] = None

        if (prev and self.get_config("context_stitch", True)
                and -0.5 <= utt.start_s - prev.end_s <= CONTEXT_GAP_S):
            res, tokens, ctx_punct, span = self._decode_stitched(prev, utt)
            if res.error:
                return
            stitched = bool(tokens)

        if not stitched:
            res = self.engine.transcribe(_pad(utt.pcm, utt.noise_floor))
            if res.error:
                return
            tokens = res.text.split()
            span = self._span(res.words)

        # Publish and cancel are ordered together, so a cancelled preview cannot follow its clear.
        with self._cancel_lock:
            if self._cancelled(spec_id) or not self._focus_matches(hwnd):
                return
            self._spec_match = _SpeculativeMatch(
                spec_id=spec_id, utt_pcm=utt.pcm, res=res, tokens=tokens,
                ctx_punct=ctx_punct, span=span, stitched=stitched, hwnd=hwnd, prev=prev,
            )
            if self.on_preview and tokens:
                try:
                    preview_tokens = list(tokens)
                    if self.get_config("remove_fillers", True):
                        preview_tokens = strip_fillers(preview_tokens, capitalize_next=not stitched)
                    preview_text = self.vocab.apply(" ".join(preview_tokens).strip())
                    self.on_preview(preview_text)
                except Exception:
                    pass

    def set_speech_active(self, active: bool):
        if active:
            self.speech_active.set()
        else:
            self.speech_active.clear()

    def _clean_text(self, text, partial=False):
        if self.get_config("strip_outer_quotes", True):
            text = strip_wrapping_quotes(text, partial=partial)
        return text

    def _live_tokens(self, text, partial=False):
        tokens = self._clean_text(text, partial).split()
        if self.get_config("remove_fillers", True):
            tokens = strip_fillers(tokens, capitalize_next=True)
        return self.vocab.apply(" ".join(tokens)).split()

    def process_live(self, utt: Utterance, result: TranscribeResult):
        """Append words agreed across successive hypotheses; never backspace user text."""
        ident = utt.utterance_id
        self._focus_key = utt.target_focus
        if ident is None or ident <= self._live_completed_id or result.error:
            return
        hwnd = self._target(utt)
        state = self._live
        if state is None or state.utterance_id != ident:
            state = self._live = _LiveState(ident, hwnd, [], [])
        if state.blocked:
            return
        if not self._focus_matches(state.hwnd) or hwnd != state.hwnd:
            state.blocked = True
            self._prev = None
            self._report("focus", "Focus changed; live dictation for this phrase stopped.")
            return
        tokens = self._live_tokens(result.text, partial=True)
        keep, _ = judge(" ".join(tokens), utt, word_span_s=self._span(result.words))
        if not keep:
            state.previous = []
            state.hypotheses.clear()
            return
        if self.on_preview:
            try:
                self.on_preview(" ".join(tokens))
            except Exception:
                pass
        if self.get_config("dictation_mode", "accurate") != "fast":
            return  # preview is revisable; only a completed phrase may reach the field
        # Keep a small tail provisional: endings, punctuation and proper names
        # often change when the model hears the next syllable.
        required = max(2, int(getattr(self.engine, "stable_hypotheses", 2)))
        guard = max(1, int(getattr(self.engine, "live_guard_words", 1)))
        state.hypotheses.append(tokens)
        state.hypotheses = state.hypotheses[-required:]
        if len(state.hypotheses) < required:
            state.previous = tokens
            return
        agreed = 0
        for versions in zip(*state.hypotheses):
            if len({_word_key(word) for word in versions}) != 1:
                break
            agreed += 1
        count = min(agreed, max(0, len(tokens) - guard))
        state.previous = tokens
        committed_keys = [_word_key(t) for t in state.committed]
        if [_word_key(t) for t in tokens[:len(committed_keys)]] != committed_keys:
            return  # final decode can resolve the untyped tail; never rewrite earlier input
        if count <= len(state.committed):
            return
        additions = tokens[len(state.committed):count]
        # A partial's sentence-ending punctuation is tentative too.
        additions = [t.rstrip('.!?…') for t in additions]
        additions = [t for t in additions if t]
        if not additions:
            return
        lead = ""
        if not state.committed and self._prev:
            if self._prev.hwnd == hwnd:
                lead = self._prev.trail + " "
            self._prev = None
        if not self._emit(lead + " ".join(additions) + " ", hwnd):
            state.blocked = True
            self._report("blocked", "Could not type live words into the original field.")
            return
        state.committed.extend(additions)

    def _finish_live(self, utt, state, result):
        """Finish just the untyped tail of a live phrase, with no duplicate prefix."""
        tokens = self._live_tokens(result.text)
        self._delivery_text = " ".join(tokens)
        if state.blocked or not self._focus_matches(state.hwnd):
            self._prev = None
            self._report("focus", "This phrase was not finished in the field. Open Recent transcripts to recover it.")
            return
        if result.error:
            self._report("error", "Speech engine could not finish this live phrase.")
            return
        keep, reason = judge(" ".join(tokens), utt, word_span_s=self._span(result.words))
        if not keep:
            self._delivery_status, self._delivery_reason = "Filtered", reason
            return
        committed_keys = [_word_key(t) for t in state.committed]
        final_keys = [_word_key(t) for t in tokens]
        tail_start = len(committed_keys)
        if final_keys[:tail_start] != committed_keys:
            # An older word can change after it was consistently recognized.
            # A unique four-word ending plus a nearly identical prefix can still
            # locate the untyped tail, without rewriting the user's field.
            aligned = continuation_index(state.committed, tokens)
            if aligned is not None:
                tail_start = aligned
                if _canonical(state.committed)[0] != _canonical(tokens[:tail_start])[0]:
                    self._report("revised", "An earlier live word changed. The continuation was typed; the complete final is in Recent transcripts.")
            else:
                # Preserve input when the untyped tail cannot be located reliably.
                self._report("revised", "Live words changed and could not be safely joined. The complete final is available in Recent transcripts. Use Complete phrases to avoid this.")
                if self.history is not None:
                    self.history.add_entry(" ".join(state.committed), duration_sec=utt.speech_s)
                return
        remaining = tokens[tail_start:]
        if not remaining:
            # The last hypothesis shortened after a prefix ending with a space
            # was typed. Preserve it; do not place punctuation after that space.
            self._prev = None
            if self._delivery_status != "Needs review":
                self._delivery_status = "Delivered"
            if self.history is not None:
                self.history.add_entry(" ".join(state.committed), duration_sec=utt.speech_s)
            return
        _, trail = split_trail(tokens[-1])
        if remaining:
            remaining[-1], trail = split_trail(remaining[-1])
            body = " ".join(remaining).strip()
            if body and not self._emit(body, state.hwnd):
                self._prev = None
                self._report("blocked", "Could not finish live text in the original field.")
                return
        self._prev = _Prev(utt.pcm, utt.end_s, trail,
                           (normalized_words(tokens[-1]) or [""])[-1],
                           state.hwnd, self.clock() + PUNCT_HOLD_S, self._focus_key)
        if self._delivery_status != "Needs review":
            self._delivery_status = "Delivered"
        if self.history is not None:
            self.history.add_entry(" ".join(state.committed + remaining) + trail, duration_sec=utt.speech_s)

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
        if hwnd != prev.hwnd or (self.get_focus and prev.focus is not None and self.get_focus() != prev.focus):
            logger.debug(f"Dropped deferred '{prev.trail}' — focus moved to another window ({reason}).")
            return
        if not self._emit(prev.trail + " ", prev.hwnd):
            self._report("blocked", "Could not type the final punctuation into the original window.")
        logger.debug(f"Flushed deferred '{prev.trail}' ({reason}).")

    # ── Main entry ──

    def process(self, utt: Utterance, result: Optional[TranscribeResult] = None):
        self._delivery_text, self._delivery_status, self._delivery_reason = "", "Not typed", ""
        try:
            self._process(utt, result)
        finally:
            self.recovery.add(self._delivery_text, self._delivery_status, self._delivery_reason)

    def _process(self, utt: Utterance, result: Optional[TranscribeResult] = None):
        self._focus_key = utt.target_focus
        if self.on_preview:
            try:
                self.on_preview("")
            except Exception:
                pass
        live, self._live = self._live, None
        if utt.utterance_id is not None:
            self._live_completed_id = max(self._live_completed_id, utt.utterance_id)
        if live and live.utterance_id == utt.utterance_id and (live.committed or live.blocked):
            res = result if result is not None else self.engine.transcribe(_pad(utt.pcm, utt.noise_floor))
            self._finish_live(utt, live, res)
            return
        hwnd = self._target(utt)
        if not self._focus_matches(hwnd):
            self._spec_match = None
            self._prev = None
            res = result if result is not None else self.engine.transcribe(_pad(utt.pcm, utt.noise_floor))
            if not res.error:
                self._delivery_text = " ".join(self._live_tokens(res.text))
            self._report("focus", "Focus changed; this phrase was not typed. Keep the target window focused until dictation finishes.")
            return
        if self._prev and (self._prev.hwnd != hwnd or self._prev.focus != self._focus_key):
            self._prev = None
        prev = self._prev
        stitched = False
        ctx_punct = ""
        tokens: List[str] = []
        span: Optional[float] = None

        # Check for pre-decoded speculative cache hit
        spec = self._spec_match
        self._spec_match = None
        if result is not None:
            res = result
            if res.error:
                self._report("error", "Speech engine could not transcribe this phrase.")
                return
            tokens = self._clean_text(res.text).split()
            span = self._span(res.words)
        elif (spec is not None
                and utt.spec_id is not None
                and spec.spec_id == utt.spec_id
                and not self._cancelled(spec.spec_id)
                and spec.hwnd == hwnd
                and spec.prev is prev
                and spec.utt_pcm == utt.pcm):
            res = spec.res
            tokens = list(spec.tokens)
            ctx_punct = spec.ctx_punct
            span = spec.span
            stitched = spec.stitched
            logger.debug(f"Speculative cache hit for spec_id={spec.spec_id} (0 ms decode)")
        else:
            if (prev and self.get_config("context_stitch", True)
                    and -0.5 <= utt.start_s - prev.end_s <= CONTEXT_GAP_S):
                res, tokens, ctx_punct, span = self._decode_stitched(prev, utt)
                if res.error:
                    self._report("error", "Speech engine could not transcribe this phrase.")
                    return
                stitched = bool(tokens)

            if not stitched:
                res = self.engine.transcribe(_pad(utt.pcm, utt.noise_floor))
                if res.error:
                    self._report("error", "Speech engine could not transcribe this phrase.")
                    return
                tokens = self._clean_text(res.text).split()
                span = self._span(res.words)

        if self.on_preview:
            try:
                self.on_preview("")
            except Exception:
                pass

        if self.get_config("remove_fillers", True):
            tokens = strip_fillers(tokens, capitalize_next=not stitched)

        tokens = self._clean_text(" ".join(tokens)).split()

        text = " ".join(tokens)
        self._delivery_text = self.vocab.apply(text)
        keep, reason = judge(text, utt, word_span_s=span)
        ratio = "n/a" if utt.level_ratio is None else f"{utt.level_ratio:.2f}"
        logger.info(f"utt dur={utt.duration_s:.1f}s voiced={utt.speech_s:.2f}s p={utt.mean_prob:.2f} "
                    f"rms={utt.speech_rms:.0f} floor={utt.noise_floor:.0f} level={ratio} -> "
                    f"{'KEEP' if keep else 'DROP: ' + reason} {self._shown(repr(text))}")
        if not keep:
            self._delivery_status, self._delivery_reason = "Filtered", reason
            return

        body_tokens = list(tokens)
        body_tokens[-1], trail = split_trail(body_tokens[-1])
        body = self.vocab.apply(" ".join(body_tokens).strip())
        if not body:
            return

        lead = ""
        if prev:
            lead = (ctx_punct if stitched else prev.trail) + " "

        if not self._focus_matches(hwnd):
            self._prev = None
            self._report("focus", "Focus changed during transcription; this phrase was not typed.")
            return
        if not self._emit(lead + body, hwnd):
            self._prev = None
            self._report("blocked", "Could not type into the original window (focus changed or Windows blocked input).")
            return

        self._prev = _Prev(
            pcm=utt.pcm,
            end_s=utt.end_s,
            trail=trail,
            last_word=(normalized_words(body_tokens[-1]) or [""])[-1],
            hwnd=hwnd,
            deadline=self.clock() + PUNCT_HOLD_S,
            focus=self._focus_key,
        )
        self._delivery_status = "Delivered"
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

        padded = _pad(ctx + gap + utt.pcm, utt.noise_floor)
        limit = getattr(self.engine, "max_audio_seconds", float("inf"))
        if len(padded) / BYTES_PER_SEC > limit:
            return TranscribeResult(), [], "", None  # decode the new phrase alone
        res = self.engine.transcribe(padded)
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
                logger.debug("Stitch context mismatch; using a standalone decode.")
                return res, [], "", None
        else:
            ctx_punct = prev.trail
        return res, [w["word"] for w in new_words], ctx_punct, self._span(new_words)

    def _emit(self, text: str, hwnd: int) -> bool:
        try:
            if not self._focus_matches(hwnd):
                return False
            if self.inject_target:
                return bool(self.inject_target(text, hwnd))
            return bool(self.inject(text))
        except Exception as e:
            logger.error(f"Injection error: {e}", exc_info=True)
            return False

    def _report(self, kind: str, message: str):
        self._delivery_status = "Needs review" if kind == "revised" else "Not typed"
        self._delivery_reason = message
        logger.warning(message)
        if self.on_event:
            try:
                self.on_event(kind, message)
            except Exception:
                pass
