"""
Low-Latency Hot-Mic Audio Capture

Captures microphone audio at 16,000 Hz mono 16-bit PCM. The stream is opened once at startup
(hot mic) so starting dictation is instant. While a session is recording, every 32 ms frame goes
through a VAD-driven segmenter (see segmenter.py) that emits one `Utterance` per spoken phrase,
containing only speech plus a little padding, together with the evidence needed to judge it.
"""

import numpy as np
import sounddevice as sd
import threading
import logging
from typing import Callable, Optional, List, Dict

from src.core.segmenter import UtteranceSegmenter, Utterance
from src.core.vad import create_vad, FRAME_SAMPLES

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"
BLOCK_SIZE = FRAME_SAMPLES   # one VAD frame (32 ms) per callback


class AudioCaptureEngine:
    def __init__(
        self,
        device_index: Optional[int] = None,
        on_utterance: Optional[Callable[[Utterance], None]] = None,
        on_level_update: Optional[Callable[[float], None]] = None,
        on_activity: Optional[Callable[[bool], None]] = None,
        pause_ms: int = 700,
        voice_gate: bool = True,
    ):
        self.device_index = device_index
        self.on_level_update = on_level_update

        self._stream: Optional[sd.RawInputStream] = None
        self._is_recording = False
        self._is_stream_open = False
        self._lock = threading.Lock()
        self._segmenter = UtteranceSegmenter(
            create_vad(),
            on_utterance=lambda u: on_utterance(u) if on_utterance else None,
            on_activity=on_activity,
            pause_ms=pause_ms,
            voice_gate=voice_gate,
        )

        self._ambient_noise = 60.0   # level-meter noise floor (orb animation only)

    @staticmethod
    def list_input_devices() -> List[Dict]:
        """Lists all available audio input devices."""
        devices = []
        try:
            for idx, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) > 0:
                    devices.append({
                        "index": idx,
                        "name": dev["name"],
                        "hostapi": dev["hostapi"],
                        "channels": dev["max_input_channels"],
                    })
        except Exception as e:
            logger.error(f"Error querying audio devices: {e}")
        return devices

    def set_device(self, device_index: Optional[int]):
        """Switches the input device."""
        self.device_index = device_index
        self._segmenter.forget_voice_level()     # a different mic has a different loudness scale
        if self._is_stream_open:
            self.close_stream()
            self.warm_up()

    def set_pause_ms(self, pause_ms: int):
        self._segmenter.set_pause_ms(pause_ms)

    def set_voice_gate(self, enabled: bool):
        self._segmenter.voice_gate = enabled

    @property
    def voice_level(self) -> Optional[float]:
        return self._segmenter.voice_level

    def warm_up(self):
        """Opens the mic stream and keeps it running (hot mic)."""
        if self._is_stream_open:
            return

        def _callback(indata, frames, time_info, status):
            if status:
                logger.debug(f"Audio status warning: {status}")

            raw_bytes = bytes(indata)

            # Orb level meter: energy above an adaptive ambient floor, power-law shaped
            audio_array = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(audio_array ** 2))) if len(audio_array) > 0 else 0.0
            if rms < self._ambient_noise * 1.5:
                self._ambient_noise = self._ambient_noise * 0.96 + rms * 0.04
            else:
                self._ambient_noise = min(self._ambient_noise * 1.002, 140.0)
            if self.on_level_update:
                voice_energy = max(0.0, rms - self._ambient_noise)
                self.on_level_update(min(1.0, (voice_energy / 850.0) ** 0.62))

            if not self._is_recording:
                return
            try:
                with self._lock:
                    if self._is_recording:
                        self._segmenter.feed(raw_bytes)
            except Exception as e:
                logger.error(f"Segmenter error: {e}", exc_info=True)

        try:
            self._stream = sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=BLOCK_SIZE,
                device=self.device_index,
                callback=_callback,
            )
            self._stream.start()
            self._is_stream_open = True
            logger.info("Microphone stream opened (hot mic ready).")
        except Exception as e:
            logger.error(f"Failed to open audio stream: {e}", exc_info=True)
            raise

    def close_stream(self):
        """Closes the mic stream entirely (app shutdown)."""
        self._is_recording = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                logger.error(f"Error closing audio stream: {e}")
            self._stream = None
        self._is_stream_open = False
        if self.on_level_update:
            self.on_level_update(0.0)

    def start(self):
        """Begin segmenting speech for a dictation session."""
        if self._is_recording:
            return

        if not self._is_stream_open:
            self.warm_up()

        with self._lock:
            self._segmenter.reset()
            self._is_recording = True
        logger.info("Speech recording started.")

    def stop(self):
        """Stop recording. Any speech still in flight is emitted through on_utterance (marked forced)."""
        if not self._is_recording:
            return

        with self._lock:
            self._is_recording = False
            self._segmenter.flush()

        if self.on_level_update:
            self.on_level_update(0.0)
        logger.info("Speech recording stopped.")
