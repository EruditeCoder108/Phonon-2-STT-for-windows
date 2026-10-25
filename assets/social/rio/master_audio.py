"""Mix the original sound bed with the local speaking demo at film timing."""
from pathlib import Path
import wave
import numpy as np

root = Path(__file__).parent
rate = 48000

def read(path):
    with wave.open(str(path)) as source:
        sr, channels = source.getframerate(), source.getnchannels()
        audio = np.frombuffer(source.readframes(source.getnframes()), dtype='<i2').astype(np.float32) / 32768
        audio = audio.reshape(-1, channels)
    if channels == 1:
        audio = np.repeat(audio, 2, axis=1)
    if sr != rate:
        target = np.arange(round(len(audio) * rate / sr)) * sr / rate
        audio = np.column_stack([np.interp(target, np.arange(len(audio)), audio[:, c]) for c in range(2)])
    return audio

bed = read(root / 'audio-original.wav')[:24 * rate]
voice = read(root / 'voice-demo.wav')
t = np.arange(len(bed)) / rate
duck = 1 - .6 * np.minimum(np.clip((t - 5.7) / .35, 0, 1), np.clip((9.25 - t) / .35, 0, 1))
mixed = bed * 1.5 * duck[:, None]
start = round(6.05 * rate)
mixed[start:start + len(voice)] += voice * 1.05
peak = float(np.max(np.abs(mixed)))
mixed *= min(1, .8 / max(peak, 1e-6))
with wave.open(str(root / 'rio-final-audio.wav'), 'wb') as output:
    output.setparams((2, 2, rate, 0, 'NONE', 'not compressed'))
    output.writeframes((np.clip(mixed, -1, 1) * 32767).astype('<i2').tobytes())
print({'peak': peak, 'seconds': len(mixed) / rate, 'voice_starts_at': 6.05, 'voice_seconds': len(voice) / rate})
