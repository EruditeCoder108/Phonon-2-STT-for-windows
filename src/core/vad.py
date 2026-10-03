"""
Voice Activity Detection

SileroVAD is the primary detector: a ~2 MB ONNX model run through onnxruntime on one CPU
thread (no torch import in the UI process, and no competition with the speech server for cores).
It scores 32 ms frames with a speech probability, which is far more robust against keyboard
clicks, breathing and fan noise than an RMS threshold.

EnergyVAD is a degraded fallback used only if silero-vad / onnxruntime are not installed, so the
app keeps working (with the old class of false positives) instead of failing to start.
"""

import importlib.util
import logging
import os
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512          # Silero's required chunk size at 16 kHz (32 ms)
_CONTEXT_SAMPLES = 64        # Silero expects the previous 64 samples prepended to each chunk


def _find_silero_model() -> Optional[str]:
    spec = importlib.util.find_spec("silero_vad")
    if spec is None or not spec.origin:
        return None
    path = os.path.join(os.path.dirname(spec.origin), "data", "silero_vad.onnx")
    return path if os.path.exists(path) else None


class SileroVAD:
    """Stateful per-stream speech-probability estimator. Not thread-safe; use from one thread."""

    name = "silero"

    def __init__(self, model_path: str):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        opts.log_severity_level = 3
        self._session = ort.InferenceSession(model_path, sess_options=opts, providers=["CPUExecutionProvider"])
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self):
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, _CONTEXT_SAMPLES), dtype=np.float32)

    def probability(self, frame_i16: np.ndarray) -> float:
        """Speech probability (0..1) for exactly FRAME_SAMPLES int16 samples."""
        x = frame_i16.astype(np.float32)[None, :] / 32768.0
        inp = np.concatenate([self._context, x], axis=1)
        out, self._state = self._session.run(None, {"input": inp, "state": self._state, "sr": self._sr})
        self._context = inp[:, -_CONTEXT_SAMPLES:]
        return float(out[0][0])


class EnergyVAD:
    """Fallback: maps frame energy above an adaptive noise floor onto a pseudo-probability."""

    name = "energy"

    def __init__(self):
        self.reset()

    def reset(self):
        self._floor = 60.0

    def probability(self, frame_i16: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(frame_i16.astype(np.float32) ** 2)))
        if rms < self._floor * 1.5:
            self._floor = self._floor * 0.95 + rms * 0.05
        ratio = rms / max(self._floor, 30.0)
        # ratio 2.5x floor -> ~0.5, 5x -> ~1.0
        return float(min(1.0, max(0.0, (ratio - 1.5) / 3.0)))


def create_vad():
    """Returns the best available VAD instance."""
    model = _find_silero_model()
    if model is not None and importlib.util.find_spec("onnxruntime") is not None:
        try:
            vad = SileroVAD(model)
            logger.info("VAD: Silero (onnxruntime)")
            return vad
        except Exception as e:
            logger.warning(f"Silero VAD failed to load ({e}); falling back to energy VAD.")
    else:
        logger.warning("silero-vad / onnxruntime not installed; using the weaker energy VAD. "
                       "Run: pip install silero-vad onnxruntime")
    return EnergyVAD()
