"""
Audition the cues through your speakers.

    python tools/preview_sounds.py                 # every style, in order: glass, wood, air
    python tools/preview_sounds.py wood            # one style
    python tools/preview_sounds.py glass --volume 80
    python tools/preview_sounds.py --save          # also write <style>_<cue>.wav files next to this script

Each style plays: ready, then start, then stop. Tweak notes, decays and levels in
src/core/sound_effects.py. The volume here is the same 0-100 setting as in the dashboard.
"""

import os
import sys
import time
import wave

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from src.core.sound_effects import CUES, DEFAULT_VOLUME, THEMES, SoundEffects, render


def main():
    args = sys.argv[1:]
    volume = int(args[args.index("--volume") + 1]) if "--volume" in args else DEFAULT_VOLUME
    themes = [a for a in args if a in THEMES] or list(THEMES)

    fx = SoundEffects(enabled=True, volume=volume)
    for theme in themes:
        print(f"== {theme} ==")
        for cue in CUES:
            print(f"   {cue}")
            fx.play(cue, theme)
            time.sleep({"ready": 1.9, "start": 1.1, "stop": 1.1}[cue])
        time.sleep(0.8)
    fx.close()

    if "--save" in args:
        here = os.path.dirname(os.path.abspath(__file__))
        for theme in themes:
            for cue in CUES:
                data = render(theme, cue, 48000) * (volume / 100.0) ** 1.6
                path = os.path.join(here, f"{theme}_{cue}.wav")
                with wave.open(path, "wb") as w:
                    w.setnchannels(2)
                    w.setsampwidth(2)
                    w.setframerate(48000)
                    w.writeframes((data * 32767).astype(np.int16).tobytes())
                print("saved", path)


if __name__ == "__main__":
    main()
