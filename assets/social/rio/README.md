# Rio media and orb studio

- `rio-launch-4k60.mp4`: 24-second, 3840×2160, 60 fps launch film.
- `rio-launch-1080p60.mp4`: smaller 1920×1080 version for posting.
- `rio-phonon25-poster.png`: revised portrait poster.
- `rio-orb-liquid.png`, `rio-orb-pearl.png`, `rio-orb-aurora.png`: three fresh material studies, generated from the screenshot's appearance rather than enlarged screenshot pixels.
- `studio.html`: live orb with material selection, idle/listening/speaking/paused states, local audio demo and optional microphone input.
- `index.html`: editable film preview. `studies.html`: concept gallery.

Run `npm ci` then `npm start` in this folder. Open `http://127.0.0.1:4176/studio.html`. The microphone is requested only after clicking Use microphone; the analyser neither records nor uploads audio.

The film's orb is a new procedural WebGL material, with ray-marched liquid geometry, glass shading, reflections and color changes. It draws no screenshot/image texture. Its native 1600×1600 canvas exceeds the orb's displayed pixel size in the 4K film. The generated still concepts are separate material studies, not pixel-identical frames of this renderer.

The film uses Motion 14.6.0 springs and 1,440 exact frames. The locally synthesized Microsoft Zira phrase starts at 6.05 seconds; its measured RMS and word markers drive the speaking animation. This is an illustrative motion sequence, not a recognition benchmark. The sound bed uses newly synthesized tones/noise, with no third-party music samples.

Reproduce voice with `make_voice.ps1` and `make_envelope.py`; audio with `synthesize_audio.py` and `master_audio.py`; video with `node render.cjs --4k`. The renderer needs Edge, Playwright and FFmpeg; `CODEX_NODE_MODULES` and `FFMPEG` can override their paths. `node verify-studio.cjs` checks material selection, paused motion, real-audio reactivity, return to idle and page errors. It does not request a real microphone. The app's 191 regression tests pass after the visible Rio rename.

The app's existing HUD is unchanged; the new renderer is the interactive media prototype. Profile/startup identifiers remain compatible. Earlier Syllink media are retained as prior versions.

Still artwork used built-in image generation. Sources: [Motion springs](https://motion.dev/docs/spring), [Motion text animation](https://motion.dev/docs/text-animation), and the behavior in `src/ui/hud.py`.
