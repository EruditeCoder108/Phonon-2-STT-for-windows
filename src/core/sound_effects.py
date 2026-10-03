"""
Synthesized Audio Cues (Earcons)

Three short cues — ready / start / stop — in three selectable palettes, rendered once at startup
(no audio files to ship). The design follows how Windows' own modern cues are built, measured
from the files in C:\\Windows\\Media (Speech On/Off, Show/Hide/Invoke):

  * quiet: peaks around -22 dBFS, nothing shrill (almost no energy above 6 kHz)
  * low and warm: fundamentals 350-1150 Hz, never ringtone-high
  * consonant intervals (perfect fourth / fifth / octave), rising for "on", falling for "off"
  * a faint mallet "tick" for tactile realism, then a long smooth decay
  * a small, dark, dense room tail instead of an obvious reverb

Palettes:
  glass — soft sine-based tones with a gentle sub-octave body (closest to Windows' own)
  wood  — warm marimba-like bars: hollow, rounded, short
  air   — no strikes at all: smooth pitch glides and a breathy pad swell

Playback uses one persistent WASAPI shared-mode stream at the device's own mix rate (the legacy
MME path resamples and adds latency), mixing cues in a callback, so cues never clip at the start
when an output device wakes up and never cut each other off.
"""

import logging
import threading
from typing import Dict, List, Optional

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

THEMES = ("glass", "wood", "air")
CUES = ("ready", "start", "stop")
DEFAULT_THEME = "glass"
DEFAULT_VOLUME = 60          # percent; 60 lands at about the level of Windows' own Speech On cue

# Peak amplitude of each cue at 100% volume (the volume setting scales from here).
_PEAK = {"ready": 0.22, "start": 0.18, "stop": 0.15}

_NOTE = {"C4": 261.63, "G4": 392.00, "C5": 523.25, "F5": 698.46, "G5": 783.99, "D5": 587.33, "C6": 1046.50}


# ───────────────────────── synthesis building blocks ─────────────────────────

def _filt(x, sr, lo=None, hi=None, order=2):
    """Zero-phase Butterworth-shaped band/high/low-pass in the frequency domain (numpy only).

    scipy.signal is deliberately not used: some Windows Application Control policies block
    one of its DLLs, which would stop the whole app from starting.
    """
    n = x.shape[0]
    size = 1 << (2 * n - 1).bit_length()
    f = np.fft.rfftfreq(size, 1.0 / sr)
    h = np.ones_like(f)
    if hi:
        h = h / np.sqrt(1.0 + (f / hi) ** (2 * order))
    if lo:
        h = h / np.sqrt(1.0 + (lo / np.maximum(f, 1e-6)) ** (2 * order))
    spec = np.fft.rfft(x, size, axis=0)
    spec = spec * (h[:, None] if x.ndim == 2 else h)
    return np.fft.irfft(spec, size, axis=0)[:n]


def _modal(freq, dur, sr, modes, decay, attack_ms=8.0):
    """Sum of exponentially damped sine modes (ratio, amplitude, decay scale) with a soft attack."""
    n = int(sr * dur)
    t = np.arange(n) / sr
    out = np.zeros(n)
    for ratio, amp, dscale in modes:
        f = freq * ratio
        if f < 20000:
            out += amp * np.sin(2 * np.pi * f * t) * np.exp(-t / (decay * dscale))
    a = max(2, int(attack_ms * 1e-3 * sr))
    out[:a] *= 0.5 * (1 - np.cos(np.pi * np.arange(a) / a))
    r = int(0.03 * sr)
    out[-r:] *= 0.5 * (1 + np.cos(np.pi * np.arange(r) / r))
    return out


def _tick(sr, fc=3000.0, dur=0.007, seed=0):
    """A very short band-passed noise burst: the 'mallet contact' that makes a tone feel physical."""
    rng = np.random.default_rng(seed)
    n = int(sr * dur)
    x = rng.standard_normal(n) * np.exp(-np.arange(n) / (0.0016 * sr))
    x = _filt(x, sr, lo=fc * 0.6, hi=min(fc * 1.6, sr * 0.45))
    return x / (np.max(np.abs(x)) + 1e-9)


def _glide(f0, f1, dur, sr, harmonics=((1, 1.0), (2, 0.14)), fade=0.5):
    """A smooth pitch glide inside a Hann-shaped envelope (no strike, no click)."""
    n = int(sr * dur)
    u = np.linspace(0, 1, n)
    s = u * u * (3 - 2 * u)                              # smoothstep pitch curve
    phase = 2 * np.pi * np.cumsum(f0 + (f1 - f0) * s) / sr
    env = np.sin(np.pi * u) ** (2 * fade + 0.5)
    return sum(a * np.sin(k * phase) for k, a in harmonics) * env


def _pad(freqs, dur, sr, attack, release, gains=None):
    """A soft swell of sine partials with slow attack and release."""
    n = int(sr * dur)
    t = np.arange(n) / sr
    gains = gains or [1.0] * len(freqs)
    x = sum(g * np.sin(2 * np.pi * f * t + i) for i, (f, g) in enumerate(zip(freqs, gains)))
    env = np.minimum(1.0, t / attack) ** 2 * np.minimum(1.0, (dur - t) / release) ** 2
    return x * env


def _breath(dur, sr, fc=2200.0, seed=3):
    """Soft band-passed noise, Hann-shaped: the 'air' in the air palette."""
    rng = np.random.default_rng(seed)
    n = int(sr * dur)
    x = _filt(rng.standard_normal(n), sr, lo=fc * 0.5, hi=fc * 1.5)
    return x / (np.max(np.abs(x)) + 1e-9) * np.hanning(n)


def _room(sr, length_s, seed):
    """Dark, dense, short synthetic room: noise split in bands whose decay gets faster upward."""
    rng = np.random.default_rng(seed)
    n = int(sr * length_s)
    t = np.arange(n) / sr
    ir = np.zeros(n)
    for lo, hi, tau in ((80, 400, 0.16), (400, 1200, 0.11), (1200, 3000, 0.065), (3000, 5000, 0.03)):
        ir += _filt(rng.standard_normal(n), sr, lo=lo, hi=hi) * np.exp(-t / tau)
    pre = int(0.006 * sr)                                  # pre-delay keeps the dry strike crisp
    ir = np.concatenate([np.zeros(pre), ir])[:n]
    return ir / np.sqrt(np.sum(ir ** 2))


def _fft_convolve(x, h):
    n = len(x) + len(h) - 1
    size = 1 << (n - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(h, size), size)[:len(x)]


def _mix(events, sr, tail_s):
    total = max(s + len(a) / sr for s, a, _ in events) + tail_s
    out = np.zeros(int(sr * total))
    for start, arr, gain in events:
        i = int(sr * start)
        out[i:i + len(arr)] += arr[:len(out) - i] * gain
    return out


def _master(dry, sr, cue, wet, lp_hz):
    """Room tail (stereo-decorrelated), band-limit, fade, and scale to this cue's peak."""
    left = dry + wet * _fft_convolve(dry, _room(sr, 0.30, 11))
    right = dry + wet * _fft_convolve(dry, _room(sr, 0.30, 12))
    st = np.stack([left, right], axis=1)
    st = _filt(_filt(st, sr, lo=80, order=2), sr, hi=lp_hz, order=4)
    fade = int(0.03 * sr)
    st[-fade:] *= np.linspace(1, 0, fade)[:, None]
    head = int(0.003 * sr)                                   # guaranteed click-free start
    st[:head] *= (0.5 * (1 - np.cos(np.pi * np.arange(head) / head)))[:, None]
    st *= _PEAK[cue] / (np.max(np.abs(st)) + 1e-9)
    return st.astype(np.float32)


# ───────────────────────────── palettes ─────────────────────────────

_GLASS = ((0.5, 0.16, 0.9), (1.0, 1.0, 1.0), (2.0, 0.13, 0.55), (3.0, 0.045, 0.35))
_WOOD = ((1.0, 1.0, 1.0), (3.96, 0.30, 0.30), (9.0, 0.07, 0.12))


def _render_glass(cue, sr):
    if cue == "start":     # rising perfect fourth
        ev = [(0.00, _modal(_NOTE["C5"], 0.28, sr, _GLASS, 0.070), 0.80),
              (0.065, _modal(_NOTE["F5"], 0.34, sr, _GLASS, 0.092), 1.00),
              (0.00, _tick(sr, 3000, seed=1), 0.10), (0.065, _tick(sr, 3400, seed=2), 0.12)]
        return _master(_mix(ev, sr, 0.08), sr, cue, wet=0.13, lp_hz=5500)
    if cue == "stop":      # falling perfect fourth, softer and longer on the landing note
        ev = [(0.00, _modal(_NOTE["F5"], 0.24, sr, _GLASS, 0.065), 0.70),
              (0.075, _modal(_NOTE["C5"], 0.38, sr, _GLASS, 0.110), 1.00),
              (0.00, _tick(sr, 2800, seed=3), 0.08), (0.075, _tick(sr, 2400, seed=4), 0.09)]
        return _master(_mix(ev, sr, 0.10), sr, cue, wet=0.15, lp_hz=4800)
    ev = [(0.00, _pad([_NOTE["C4"], _NOTE["G4"]], 1.0, sr, 0.30, 0.55, [0.45, 0.30]), 1.0),
          (0.06, _modal(_NOTE["C5"], 0.80, sr, _GLASS, 0.22), 0.80),
          (0.17, _modal(_NOTE["G5"], 0.80, sr, _GLASS, 0.24), 0.85),
          (0.29, _modal(_NOTE["C6"], 0.80, sr, _GLASS, 0.28), 0.55),
          (0.06, _tick(sr, 3000, seed=5), 0.10), (0.17, _tick(sr, 3300, seed=6), 0.10)]
    return _master(_mix(ev, sr, 0.35), sr, cue, wet=0.17, lp_hz=5200)


def _render_wood(cue, sr):
    if cue == "start":     # rising fifth, G4 -> D5
        ev = [(0.00, _modal(_NOTE["G4"], 0.26, sr, _WOOD, 0.075, 5), 0.85),
              (0.08, _modal(_NOTE["D5"], 0.30, sr, _WOOD, 0.095, 5), 1.00),
              (0.00, _tick(sr, 2200, seed=7), 0.30), (0.08, _tick(sr, 2600, seed=8), 0.32)]
        return _master(_mix(ev, sr, 0.08), sr, cue, wet=0.10, lp_hz=4500)
    if cue == "stop":      # falling fifth, D5 -> G4
        ev = [(0.00, _modal(_NOTE["D5"], 0.22, sr, _WOOD, 0.065, 5), 0.65),
              (0.09, _modal(_NOTE["G4"], 0.36, sr, _WOOD, 0.120, 5), 1.00),
              (0.00, _tick(sr, 2400, seed=9), 0.22), (0.09, _tick(sr, 2000, seed=10), 0.26)]
        return _master(_mix(ev, sr, 0.10), sr, cue, wet=0.12, lp_hz=4200)
    ev = [(0.00, _modal(_NOTE["G4"], 0.50, sr, _WOOD, 0.16, 5), 0.85),
          (0.12, _modal(_NOTE["D5"], 0.50, sr, _WOOD, 0.17, 5), 0.90),
          (0.24, _modal(_NOTE["G5"], 0.60, sr, _WOOD, 0.19, 5), 0.80),
          (0.00, _tick(sr, 2200, seed=11), 0.28), (0.12, _tick(sr, 2500, seed=12), 0.28), (0.24, _tick(sr, 2800, seed=13), 0.26)]
    return _master(_mix(ev, sr, 0.30), sr, cue, wet=0.14, lp_hz=4500)


def _render_air(cue, sr):
    if cue == "start":
        ev = [(0.00, _glide(_NOTE["G4"], _NOTE["D5"], 0.26, sr), 1.0), (0.00, _breath(0.26, sr, 2200, 3), 0.06)]
        return _master(_mix(ev, sr, 0.10), sr, cue, wet=0.16, lp_hz=4200)
    if cue == "stop":
        ev = [(0.00, _glide(_NOTE["D5"], _NOTE["G4"], 0.32, sr, fade=0.7), 1.0), (0.00, _breath(0.32, sr, 1800, 4), 0.05)]
        return _master(_mix(ev, sr, 0.12), sr, cue, wet=0.18, lp_hz=3800)
    ev = [(0.00, _pad([_NOTE["C4"], _NOTE["G4"], _NOTE["C5"]], 1.0, sr, 0.30, 0.50, [0.55, 0.40, 0.30]), 1.0),
          (0.12, _glide(_NOTE["C5"], _NOTE["C6"], 0.60, sr, fade=0.8), 0.26), (0.05, _breath(0.7, sr, 2000, 5), 0.04)]
    return _master(_mix(ev, sr, 0.30), sr, cue, wet=0.20, lp_hz=4200)


_RENDERERS = {"glass": _render_glass, "wood": _render_wood, "air": _render_air}


def render(theme: str, cue: str, sr: int = 48000) -> np.ndarray:
    """Float32 (n, 2) samples for one cue at 100% volume."""
    return _RENDERERS.get(theme, _render_glass)(cue, sr)


# ───────────────────────────── playback ─────────────────────────────

def _pick_output():
    """(device, samplerate, extra_settings): WASAPI shared mode at the device's mix rate if possible."""
    try:
        for h in sd.query_hostapis():
            if "WASAPI" in h["name"] and h["default_output_device"] >= 0:
                dev = h["default_output_device"]
                rate = int(sd.query_devices(dev)["default_samplerate"])
                return dev, rate, sd.WasapiSettings(auto_convert=True)
    except Exception as e:
        logger.debug(f"WASAPI output unavailable: {e}")
    try:
        dev = sd.default.device[1]
        return None, int(sd.query_devices(dev)["default_samplerate"]), None
    except Exception:
        return None, 48000, None


class SoundEffects:
    def __init__(self, enabled: bool = True, theme: str = DEFAULT_THEME, volume: int = DEFAULT_VOLUME):
        self._enabled = enabled
        self.theme = theme if theme in THEMES else DEFAULT_THEME
        self.volume = volume
        self._device, self._rate, self._extra = _pick_output()
        self._bank: Dict[str, Dict[str, np.ndarray]] = {}
        self._lock = threading.Lock()
        self._voices: List[list] = []        # [samples, position, gain]
        self._stream: Optional[sd.OutputStream] = None
        self._bank_for(self.theme)

    # settings

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = bool(value)
        if not self._enabled:
            self.close()

    def configure(self, theme: Optional[str] = None, volume: Optional[int] = None):
        if theme in THEMES:
            self.theme = theme
            self._bank_for(theme)
        if volume is not None:
            self.volume = max(0, min(100, int(volume)))

    def _bank_for(self, theme: str) -> Dict[str, np.ndarray]:
        if theme not in self._bank:
            self._bank[theme] = {cue: render(theme, cue, self._rate) for cue in CUES}
        return self._bank[theme]

    # cues

    def play_ready(self):
        """The orb has just bloomed from grey to colour: the engine is ready to listen."""
        self.play("ready")

    def play_start(self):
        self.play("start")

    def play_stop(self):
        self.play("stop")

    def play(self, cue: str, theme: Optional[str] = None):
        if not self._enabled or self.volume <= 0:
            return
        data = self._bank_for(theme or self.theme)[cue]
        gain = (self.volume / 100.0) ** 1.6
        if not self._ensure_stream():
            try:
                sd.play(data * gain, samplerate=self._rate, blocking=False)    # last-resort fallback
            except Exception as e:
                logger.debug(f"Audio cue playback error: {e}")
            return
        with self._lock:
            self._voices.append([data, 0, gain])

    # stream

    def _ensure_stream(self) -> bool:
        if self._stream is not None and self._stream.active:
            return True
        self.close()
        try:
            self._stream = sd.OutputStream(
                samplerate=self._rate, channels=2, dtype="float32", device=self._device,
                extra_settings=self._extra, callback=self._callback, latency="low")
            self._stream.start()
            return True
        except Exception as e:
            logger.debug(f"Could not open persistent output stream: {e}")
            self._stream = None
            return False

    def _callback(self, outdata, frames, time_info, status):
        outdata.fill(0)
        with self._lock:
            alive = []
            for voice in self._voices:
                data, pos, gain = voice
                chunk = data[pos:pos + frames]
                outdata[:len(chunk)] += chunk * gain
                voice[1] = pos + frames
                if voice[1] < len(data):
                    alive.append(voice)
            self._voices = alive
        np.clip(outdata, -1.0, 1.0, out=outdata)

    def close(self):
        stream, self._stream = self._stream, None
        with self._lock:
            self._voices = []
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
