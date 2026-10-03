"""
VAD-driven Utterance Segmenter

Turns a continuous 16 kHz int16 mic stream into discrete utterances that contain only speech
(plus a little padding), replacing the old "accumulate everything, cut on RMS silence" logic.

Why this matters for accuracy:
  * Speech starts only after several CONSECUTIVE voiced frames, so isolated keyboard clicks,
    coughs and breaths never open an utterance (the old counter never decayed, so scattered
    noise eventually added up to "speech").
  * A short pre-roll keeps soft word onsets; trailing silence is trimmed, so the audio sent to
    the model is not padded with seconds of dead air.
  * Relative loudness ("voice gate"): the segmenter learns how loud YOU are. Speech much quieter
    than that (a video playing nearby, people talking across the room) neither opens an
    utterance nor keeps one open, so background audio can no longer glue your sentences to it
    or get typed itself.
  * Each utterance carries evidence (voiced duration, mean VAD probability, RMS of the voiced
    frames only, ambient noise floor) so later stages can judge it without being fooled by how
    long the surrounding pause was.
"""

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, List, Optional, Tuple

import numpy as np

from src.core.vad import FRAME_SAMPLES, SAMPLE_RATE

logger = logging.getLogger(__name__)

FRAME_S = FRAME_SAMPLES / SAMPLE_RATE      # 0.032 s
VOICE_LEVEL_TTL_S = 20 * 60                # forget the learned loudness after this long without updates
_FRAME_BYTES = FRAME_SAMPLES * 2


@dataclass
class Utterance:
    pcm: bytes
    start_s: float          # position on the stream clock (seconds) where pcm begins
    end_s: float            # ... and where it ends
    speech_s: float         # seconds of VAD-voiced audio inside pcm
    mean_prob: float        # mean VAD probability over the voiced frames
    speech_rms: float       # RMS over voiced frames only (not diluted by silence)
    noise_floor: float      # ambient RMS estimate when this utterance was cut
    forced: bool = False    # True when cut by max length or session end, not by a natural pause
    level_ratio: Optional[float] = None   # speech_rms / learned voice level (None = not known / gate off)

    @property
    def duration_s(self) -> float:
        return len(self.pcm) / 2 / SAMPLE_RATE


_Frame = Tuple[bytes, float, float]   # (raw bytes, vad probability, rms)


class UtteranceSegmenter:
    def __init__(
        self,
        vad,
        on_utterance: Callable[[Utterance], None],
        on_activity: Optional[Callable[[bool], None]] = None,
        pause_ms: int = 700,
        min_speech_s: float = 0.15,
        preroll_s: float = 0.30,
        tail_keep_s: float = 0.25,
        max_utterance_s: float = 25.0,
        start_threshold: float = 0.5,
        end_threshold: float = 0.35,
        start_frames: int = 3,
        voice_gate: bool = True,
        start_rel: float = 0.28,
        cont_rel: float = 0.30,
    ):
        self.vad = vad
        self.on_utterance = on_utterance
        self.on_activity = on_activity
        self.min_speech_s = min_speech_s
        self.start_threshold = start_threshold
        self.end_threshold = end_threshold
        self.start_frames = start_frames
        self.voice_gate = voice_gate
        self.start_rel = start_rel
        self.cont_rel = cont_rel
        # Learned loudness (int16 RMS) of the user's own speech. Kept across dictation sessions
        # within one run, but deliberately NOT saved to disk: a stale level (different mic,
        # different distance) would make the gate reject the user themselves.
        self.voice_level: Optional[float] = None
        self._level_time = 0.0
        self._preroll_frames = max(1, round(preroll_s / FRAME_S))
        self._tail_keep_frames = max(1, round(tail_keep_s / FRAME_S))
        self._max_frames = round(max_utterance_s / FRAME_S)
        self._decay_frames = round(0.13 / FRAME_S)
        self.set_pause_ms(pause_ms)

        self.noise_floor = 60.0
        self.reset()

    def set_voice_level(self, level: Optional[float]):
        self.voice_level = level
        self._level_time = time.monotonic()

    def forget_voice_level(self):
        self.voice_level = None

    def set_pause_ms(self, pause_ms: int):
        pause_ms = max(300, min(2000, int(pause_ms)))
        self._end_silence_frames = max(1, round(pause_ms / 1000.0 / FRAME_S))

    def reset(self):
        """Clears all in-flight state (call when a dictation session starts)."""
        if self.voice_level and time.monotonic() - self._level_time > VOICE_LEVEL_TTL_S:
            self.voice_level = None
        self.vad.reset()
        self._buf = b""
        self._frame_idx = 0                      # frames processed so far == stream clock
        self._in_speech = False
        self._run = 0                            # consecutive voiced frames while idle
        self._preroll: Deque[_Frame] = deque(maxlen=self._preroll_frames + self.start_frames)
        self._frames: List[_Frame] = []
        self._start_frame = 0
        self._silence = 0
        self._ema = 0.0                          # smoothed frame RMS inside the current utterance
        self._last_voice = 0                     # frames[] length at the last VAD-speech frame
        self._last_loud = 0                      # ... at the last speech frame that was also loud enough
        self._peak = 0.0                         # loudest smoothed RMS inside the current utterance

    # ── Input ──

    def feed(self, data: bytes):
        """Accepts arbitrary-length int16 PCM bytes."""
        self._buf += data
        n = len(self._buf) // _FRAME_BYTES
        if not n:
            return
        view = memoryview(self._buf)
        for i in range(n):
            fb = bytes(view[i * _FRAME_BYTES:(i + 1) * _FRAME_BYTES])
            self._process(fb)
        self._buf = bytes(view[n * _FRAME_BYTES:])

    def flush(self):
        """Ends the session: emits whatever speech is in flight (marked forced)."""
        if self._in_speech:
            self._finalize(forced=True)
        self._preroll.clear()
        self._run = 0

    # ── Core state machine ──

    def _process(self, fb: bytes):
        arr = np.frombuffer(fb, dtype=np.int16)
        p = self.vad.probability(arr)
        rms = float(np.sqrt(np.mean(arr.astype(np.float32) ** 2)))
        frame: _Frame = (fb, p, rms)
        self._frame_idx += 1

        if not self._in_speech:
            self._preroll.append(frame)
            if p < 0.2:
                # Asymmetric tracker: falls quickly to quiet, rises slowly, so a burst of
                # keyboard clicks cannot drag the "ambient" estimate up to speech level.
                rate = 0.05 if rms < self.noise_floor else 0.003
                self.noise_floor += (rms - self.noise_floor) * rate
            loud_enough = True
            if self.voice_gate and self.voice_level:
                # Judge the last ~190 ms rather than one frame, so a single loud syllable of
                # background speech cannot open an utterance on its own.
                recent = [f[2] for f in list(self._preroll)[-6:]]
                loud_enough = (sum(recent) / len(recent)) >= self.start_rel * self.voice_level
            self._run = self._run + 1 if (p >= self.start_threshold and loud_enough) else 0
            if self._run >= self.start_frames:
                self._in_speech = True
                self._ema = self._peak = max(f[2] for f in list(self._preroll)[-self.start_frames:])
                self._frames = list(self._preroll)
                self._start_frame = self._frame_idx - len(self._frames)
                self._silence = 0
                self._last_voice = self._last_loud = len(self._frames)
                self._preroll.clear()
                self._run = 0
                if self.on_activity:
                    self.on_activity(True)
            return

        self._frames.append(frame)
        self._ema = self._ema * 0.8 + rms * 0.2
        self._peak = max(self._peak, self._ema)
        # A frame only counts as "still speaking" if it is both speech-like AND not far quieter
        # than this utterance's own loudest passage (background audio is, you are not).
        quiet_vs_speaker = self.voice_gate and rms < self.cont_rel * self._peak
        if p >= self.end_threshold:
            self._last_voice = len(self._frames)
            if not quiet_vs_speaker:
                self._last_loud = len(self._frames)
        if p >= self.end_threshold and not quiet_vs_speaker:
            self._silence = 0
        else:
            self._silence += 1

        if self._silence >= self._end_silence_frames:
            self._finalize(forced=False)
        elif len(self._frames) >= self._max_frames:
            self._split_at_quietest()

    def _split_at_quietest(self):
        """Over-long speech: cut at the quietest frame in the last ~4 s instead of mid-word."""
        window = min(len(self._frames), round(4.0 / FRAME_S))
        base = len(self._frames) - window
        quietest = min(range(base, len(self._frames)), key=lambda i: self._frames[i][2])
        head, rest = self._frames[:quietest + 1], self._frames[quietest + 1:]
        self._emit(head, self._start_frame, forced=True)
        self._start_frame += len(head)
        self._frames = rest
        self._last_voice = max(0, self._last_voice - len(head))
        self._last_loud = max(0, self._last_loud - len(head))
        self._silence = 0

    def _finalize(self, forced: bool):
        # Keep a short natural tail. It is measured from the last speech frame, so a word's quiet
        # decay is never clipped (the model reads that tail to choose '.' vs ','), but capped to
        # a short decay after the last LOUD frame so quiet background speech that kept the VAD
        # busy cannot leak its words onto the end of the sentence.
        end = min(self._last_voice, self._last_loud + self._decay_frames)
        frames = self._frames[:end + self._tail_keep_frames]
        start = self._start_frame
        self._in_speech = False
        self._frames = []
        self._silence = 0
        self._run = 0
        self._preroll.clear()
        self._emit(frames, start, forced)
        if self.on_activity:
            self.on_activity(False)

    def _emit(self, frames: List[_Frame], start_frame: int, forced: bool):
        voiced = [f for f in frames if f[1] >= self.start_threshold]
        speech_s = len(voiced) * FRAME_S
        if speech_s < self.min_speech_s:
            logger.debug(f"Discarded blip: {speech_s:.2f}s voiced")
            return
        speech_rms = float(np.sqrt(np.mean([f[2] ** 2 for f in voiced])))
        ratio = None
        if self.voice_gate:
            self._level_time = time.monotonic()
            if self.voice_level:
                ratio = speech_rms / self.voice_level
                if ratio > 1.0:
                    self.voice_level += 0.5 * (speech_rms - self.voice_level)     # you got louder: adapt fast
                elif ratio >= 0.5:
                    self.voice_level += 0.1 * (speech_rms - self.voice_level)     # drifting quieter: adapt slowly
                # ratio < 0.5: background / very distant voice — never lets it drag the level down
            else:
                self.voice_level = speech_rms
        utt = Utterance(
            pcm=b"".join(f[0] for f in frames),
            start_s=start_frame * FRAME_S,
            end_s=(start_frame + len(frames)) * FRAME_S,
            speech_s=speech_s,
            mean_prob=float(np.mean([f[1] for f in voiced])),
            speech_rms=speech_rms,
            noise_floor=self.noise_floor,
            forced=forced,
            level_ratio=ratio,
        )
        try:
            self.on_utterance(utt)
        except Exception as e:
            logger.error(f"Error dispatching utterance: {e}", exc_info=True)
