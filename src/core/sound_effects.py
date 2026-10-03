"""
Synthesized Low-Latency Audio Cues (Earcons)

Generates and plays pleasant, low-latency, click-free audio chimes
when dictation begins and ends, giving immediate sound feedback.
"""

import numpy as np
import sounddevice as sd
import threading
import logging

logger = logging.getLogger(__name__)

SAMPLE_RATE = 44100


def _generate_tone(freq: float, duration: float, volume: float = 0.18) -> np.ndarray:
    """Generates a pure sine wave tone with smooth Hann-window envelope to avoid clicks."""
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
    wave = np.sin(2 * np.pi * freq * t)
    # Apply fade-in and fade-out envelope
    window = np.hanning(len(t))
    return (wave * window * volume).astype(np.float32)


def _generate_start_chime() -> np.ndarray:
    """Two-tone ascending soft chime (C5 -> G5) indicating listening started."""
    tone1 = _generate_tone(523.25, 0.07, volume=0.14)  # C5
    silence = np.zeros(int(SAMPLE_RATE * 0.015), dtype=np.float32)
    tone2 = _generate_tone(783.99, 0.09, volume=0.18)  # G5
    return np.concatenate([tone1, silence, tone2])


def _generate_stop_chime() -> np.ndarray:
    """Two-tone descending soft chime (G5 -> C5) indicating dictation stopped."""
    tone1 = _generate_tone(783.99, 0.06, volume=0.16)  # G5
    silence = np.zeros(int(SAMPLE_RATE * 0.015), dtype=np.float32)
    tone2 = _generate_tone(523.25, 0.08, volume=0.13)  # C5
    return np.concatenate([tone1, silence, tone2])


class SoundEffects:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._start_chime = _generate_start_chime()
        self._stop_chime = _generate_stop_chime()

    def play_start(self):
        """Plays the 'Dictation Started' chime in a background daemon thread."""
        if not self.enabled:
            return
        threading.Thread(target=self._play, args=(self._start_chime,), daemon=True).start()

    def play_stop(self):
        """Plays the 'Dictation Stopped' chime in a background daemon thread."""
        if not self.enabled:
            return
        threading.Thread(target=self._play, args=(self._stop_chime,), daemon=True).start()

    def _play(self, data: np.ndarray):
        try:
            sd.play(data, samplerate=SAMPLE_RATE, blocking=False)
        except Exception as e:
            logger.debug(f"Audio cue playback error: {e}")
