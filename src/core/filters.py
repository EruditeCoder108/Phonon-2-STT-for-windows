"""
Transcript Plausibility Filter

Decides whether a decoded utterance is real, intentional speech. It replaces the old chain of
whole-chunk RMS gates + a text blocklist.

The old gates measured energy over the whole buffer, silence included, so the verdict depended
on how long the user had paused before speaking (a normal-volume word after a 5 s pause was
dropped; a loud "yes" at session end was soft-blocked). Here every decision uses evidence the
segmenter measured on the VOICED frames only:

  * speech_rms / noise_floor   — signal-to-noise of the actual speech
  * mean_prob / speech_s       — how confidently and for how long the VAD heard speech

The text blocklists remain, but as a last line for the genuinely ambiguous case: a lone common
word coming out of a short, low-confidence burst.
"""

import re
from typing import Optional, Tuple

from src.core.segmenter import Utterance

# Never real dictation, whatever the audio looked like.
HARD_BLOCK = {
    "um", "uh", "hmm", "hm", "ah", "mm", "mhm", "mm-hmm", "erm", "er", "uh-huh", "huh",
}

# Real words, but also what a transducer tends to emit for a stray noise burst.
# Kept only when the audio was clearly intentional speech.
SOFT_BLOCK = {
    "yeah", "yep", "yes", "no", "nope", "ok", "okay", "alright", "all right", "right", "sure",
    "like", "so", "well", "but", "and", "or", "oh", "the", "a", "an", "i", "you", "bye",
    "thanks", "thank you", "thank", "hello", "hi", "hey", "what", "now",
}

MIN_SPEECH_RMS = 100.0        # int16 RMS over voiced frames; below this the mic gain is hopeless
MIN_SNR = 1.8                 # speech RMS / ambient RMS
SOFT_MIN_PROB = 0.85          # a lone soft-block word needs a confident VAD ...
SOFT_MIN_SPEECH_S = 0.20      # ... and to last long enough to be a word, not a click

MIN_LEVEL_RATIO = 0.30        # speech this far below the learned voice level is background (TV, people nearby)
LONE_MIN_PROB = 0.85          # ANY lone word needs a confident VAD (hums/breaths score ~0.8, speech ~0.95+)
LONE_MAX_SPEECH_S = 0.90      # a single real word is never voiced this long ...
DRAWN_OUT_RATIO = 2.5         # ... nor stretched far beyond the span the model itself gave the word

_WORD_RE = re.compile(r"[a-z0-9']+")


def normalized_words(text: str):
    return _WORD_RE.findall(text.lower())


def judge(text: str, utt: Utterance, word_span_s: Optional[float] = None) -> Tuple[bool, str]:
    """Returns (keep, reason). `reason` explains a rejection (for debug logs).

    `word_span_s` is the time the model assigned to the decoded words (from word timestamps),
    when available.
    """
    words = normalized_words(text)
    if not words:
        return False, "no words"

    phrase = " ".join(words)
    if phrase in HARD_BLOCK or all(w in HARD_BLOCK for w in words):
        return False, f"filler '{phrase}'"

    # Degenerate decoder loop ("the the the the ...").
    if len(words) >= 4 and len(set(words)) == 1:
        return False, "repetition loop"
    if len(words) >= 8 and len(set(words)) <= 2:
        return False, "repetition loop"

    if utt.speech_rms < MIN_SPEECH_RMS:
        return False, f"speech too quiet (rms={utt.speech_rms:.0f})"
    snr = utt.speech_rms / max(utt.noise_floor, 20.0)
    if snr < MIN_SNR:
        return False, f"no signal over noise floor (snr={snr:.1f})"

    if utt.level_ratio is not None and utt.level_ratio < MIN_LEVEL_RATIO:
        return False, f"background voice ({utt.level_ratio:.2f} of your level)"

    # A lone "word" out of a long or unsure voiced stretch is a sound, not speech: a hum, a
    # sigh or a breath that the decoder snapped onto some word ("hmmm" -> "True").
    if len(words) == 1:
        if utt.mean_prob < LONE_MIN_PROB:
            return False, f"unsure lone '{phrase}' (p={utt.mean_prob:.2f})"
        if utt.speech_s > LONE_MAX_SPEECH_S:
            return False, f"drawn-out sound decoded as '{phrase}' ({utt.speech_s:.2f}s voiced)"
        if word_span_s and utt.speech_s > 0.5 and utt.speech_s > DRAWN_OUT_RATIO * word_span_s:
            return False, (f"sound much longer than the word '{phrase}' "
                           f"({utt.speech_s:.2f}s voiced vs {word_span_s:.2f}s word)")

    if phrase in SOFT_BLOCK:
        if utt.mean_prob < SOFT_MIN_PROB or utt.speech_s < SOFT_MIN_SPEECH_S:
            return False, (f"unconfident lone '{phrase}' "
                           f"(p={utt.mean_prob:.2f}, {utt.speech_s:.2f}s voiced)")

    return True, ""
