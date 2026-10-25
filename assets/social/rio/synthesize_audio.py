"""Original low-key stereo sound bed: synthesized tones, air, and soft UI accents.

No recordings, samples, voices, or third-party music are used.
"""
from pathlib import Path
import wave
import numpy as np

RATE = 48000
DURATION = 24
audio = np.zeros((RATE * DURATION, 2), dtype=np.float64)
rng = np.random.default_rng(20261024)


def add(start, signal):
    first = max(0, round(start * RATE))
    length = min(len(signal), len(audio) - first)
    if length > 0:
        audio[first:first + length] += signal[:length]


def smooth_envelope(t, duration, attack=.7, release=1):
    return np.sin(np.minimum(t / attack, 1) * np.pi / 2) ** 2 * np.sin(
        np.minimum(np.maximum(duration - t, 0) / release, 1) * np.pi / 2) ** 2


# A restrained, suspended harmonic progression. Subtle width comes from tiny
# independent detuning; the bass remains centered for phone/speaker playback.
chords = [
    (0, 6.1, [146.8324, 184.9972, 220, 329.6276]),
    (4.9, 6.5, [123.4708, 184.9972, 220, 293.6648]),
    (10.1, 6.2, [97.9989, 146.8324, 184.9972, 246.9417]),
    (15.1, 6.0, [110, 164.8138, 220, 293.6648]),
    (19.9, 4.1, [146.8324, 184.9972, 220, 329.6276]),
]
for start, length, frequencies in chords:
    t = np.arange(round(length * RATE)) / RATE
    envelope = smooth_envelope(t, length, .95, 1.15)
    stereo = np.zeros((len(t), 2))
    for i, frequency in enumerate(frequencies):
        phase = rng.uniform(0, np.pi * 2)
        level = .014 if i else .019
        for channel, detune in enumerate([.9994, 1.0006]):
            phase_t = 2 * np.pi * frequency * detune * t + phase
            tone = np.sin(phase_t) + .1 * np.sin(phase_t * 2) + .025 * np.sin(phase_t * 3)
            stereo[:, channel] += tone * level * (1 + .06 * np.sin(t * .9 + i))
    add(start, stereo * envelope[:, None])


def chime(start, frequency, length=1.5, level=.035):
    t = np.arange(round(length * RATE)) / RATE
    envelope = (1 - np.exp(-t * 120)) * np.exp(-t * 4.3)
    tone = np.sin(2 * np.pi * frequency * t) + .17 * np.sin(2 * np.pi * frequency * 2.003 * t)
    direct = np.column_stack([tone, tone]) * envelope[:, None] * level
    add(start, direct)
    add(start + .16, direct * np.array([.09, .18]))
    add(start + .32, direct * np.array([.08, .045]))


chime(.66, 587.3295, level=.023)
for start in [4.8, 10.1, 15.1, 19.8]:
    length = 1.05
    t = np.arange(round(length * RATE)) / RATE
    raw = rng.normal(size=len(t))
    # Gentle band-limiting with simple FIR averages keeps this renderer
    # independent of optional native signal-processing libraries.
    noise = np.convolve(raw, np.ones(13) / 13, mode='same') - np.convolve(raw, np.ones(101) / 101, mode='same')
    envelope = np.sin(np.pi * t / length) ** 3
    add(start, np.column_stack([noise, np.roll(noise, 211)]) * envelope[:, None] * .012)

for i, start in enumerate([6.25, 6.55, 6.98, 7.27, 7.45, 7.78, 8.11, 8.26, 8.62]):
    t = np.arange(round(.045 * RATE)) / RATE
    tick = np.sin(2 * np.pi * (1100 + i * 53) * t) * (1 - np.exp(-t * 1800)) * np.exp(-t * 145)
    add(start, np.column_stack([tick, tick]) * .008)

# A small three-note signature, synced with the closing wordmark and tagline.
for start, frequency in [(20.45, 587.3295), (20.89, 880), (21.5, 1318.5102)]:
    chime(start, frequency, level=.027)

t = np.arange(len(audio)) / RATE
audio *= smooth_envelope(t, DURATION, .4, .8)[:, None]
peak = float(np.max(np.abs(audio)))
audio *= min(1, .7 / max(peak, 1e-6))
destination = Path(__file__).with_name('audio-original.wav')
with wave.open(str(destination), 'wb') as output:
    output.setparams((2, 2, RATE, 0, 'NONE', 'not compressed'))
    output.writeframes((np.clip(audio, -1, 1) * 32767).astype('<i2').tobytes())
print({'path': str(destination), 'seconds': DURATION, 'sample_rate': RATE, 'peak_before_mastering': peak})
