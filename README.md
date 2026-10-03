<p align="center">
  <img src="assets/icon.png" width="90" alt="Phonon-2 icon"/>
</p>

<h1 align="center">Phonon-2 STT for Windows</h1>

<p align="center">
  A local, offline, privacy-first speech-to-text dictation app for Windows.<br/>
  Speaks into any app — Word, browser, VS Code, anything — without the cloud.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11-blue?logo=windows" />
  <img src="https://img.shields.io/badge/python-3.14-blue?logo=python" />
  <img src="https://img.shields.io/badge/engine-Phonon--2%20%28Fermion%29-purple" />
  <img src="https://img.shields.io/badge/ui-PySide6%20%2B%20WebEngine-green" />
  <img src="https://img.shields.io/badge/license-MIT-orange" />
</p>

---

## What it is

Phonon-2 STT is a Windows dictation tool that runs **entirely on your machine** — no internet required, no API keys, no data sent anywhere. You press a hotkey, speak, and your words appear at the cursor in whatever app you have focused.

The front-end is a floating animated orb HUD that sits in the corner of your screen. It pulses and reacts to your voice in real time, changes color as it cycles through themes, and stays completely out of the way of your keyboard focus.

---

## How it works

```
Microphone (WASAPI 16 kHz)
       │
       ▼
AudioCaptureEngine  ──── hot mic, always open, zero start latency
       │
       ▼
UtteranceSegmenter  ──── Silero VAD: only real speech becomes an utterance
       │                  (pre-roll, trimmed silence, SNR / confidence evidence)
       ▼
PhononEngine (local HTTP server on 127.0.0.1:8010, supervised + auto-restart)
       │    POST /v1/audio/transcriptions  (WAV → text + word timestamps)
       ▼
DictationPipeline ─ filters.judge()  ──── evidence-based noise rejection
       │          ─ phrase stitching ──── previous phrase re-fed as audio context
       │          ─ deferred punctuation  closing mark typed once the join is known
       │          ─ VocabularyEngine ──── optional custom word replacements
       ▼
TextInjector  ──── Unicode SendInput (clipboard untouched); paste only for very long text
       │
       ▼
Whatever app has keyboard focus
```

The local speech server (`phonon-2.exe` / `fermion`) is started automatically when the app launches and is killed when the app closes.

---

## What we actually built

- **`src/main.py`** — App orchestrator. Connects all components, manages the dictation session lifecycle (start → capture → transcribe → inject → stop).
- **`src/ui/hud.py`** — Floating circular animated orb. Built with PySide6 + QWebEngineView. The animation is a pure CSS/SVG liquid blob effect from Uiverse.io (by andrew-manzyk), adapted with JS-driven `filter: hue-rotate()` for live color cycling. The orb boots grey and blooms into color when the engine is ready.
- **`src/ui/orb_settings_dialog.py`** — Long-press the orb (500ms hold) to open the Quick Customizer: choose color theme, animation style, voice reactivity. Frameless dark acrylic dialog.
- **`src/ui/tray.py`** — System tray icon with status, quick actions, quit.
- **`src/ui/dashboard.py`** — Full settings window with history, mic selection, vocabulary editor.
- **`src/core/audio.py`** — Hot-mic WASAPI capture via `sounddevice`. The stream is opened once at startup and stays open so start/stop is instant; frames feed the segmenter.
- **`src/core/vad.py`** / **`src/core/segmenter.py`** — Silero VAD (ONNX, one CPU thread, no torch in the UI process) and the state machine that cuts the stream into speech-only utterances with pre-roll and measured evidence. Falls back to a weaker energy VAD if `silero-vad`/`onnxruntime` are missing.
- **`src/core/filters.py`** — Decides whether a decoded utterance is real speech, using the VAD/SNR evidence measured on the voiced frames only.
- **`src/core/pipeline.py`** — Turns utterances into correctly punctuated text: phrase stitching and deferred sentence-final punctuation.
- **`src/core/engine.py`** — Starts and supervises the local `phonon-2` server (output drained, Job Object so it dies with the app, health watchdog with auto-restart) and provides `transcribe()` returning text + word timestamps.
- **`src/core/injector.py`** — Win32 `SendInput` Unicode typing (emoji-safe, clipboard untouched), clipboard paste fallback for very long text, and detection of elevated target windows that Windows would silently block.
- **`src/core/hotkey.py`** — Global keyboard hook for `Ctrl+Space` (or any configurable combo). Non-blocking, runs in a background thread.
- **`src/core/vocabulary.py`** — Simple phrase replacement engine (e.g. "my email" → actual address).
- **`src/core/history.py`** — Persists per-session dictation history to disk (optional).
- **`src/core/sound_effects.py`** — Three cues (ready / start / stop) in three selectable styles (glass, wood, air), synthesized at startup and calibrated against Windows' own UI sounds: quiet, warm, no treble. Played through one persistent WASAPI stream so cues never clip or cut each other off. Style and volume are in Dashboard → Audio; audition with `python tools/preview_sounds.py [style] [--volume N] [--save]`.

---

## Requirements

- Windows 10 or 11 (64-bit)
- Python 3.14
- A working microphone
- **RAM**: Recommended 8 GB total system RAM (Phonon-2 itself uses **~1.4 GB** for the speech model weights + **~200 MB** for the UI, total ~1.6 GB).

The `phonon-2` / `fermion` speech engine binary is installed as a Python package (`fermion-research`) and runs as a local HTTP server. The model weights are downloaded on first run.

---

## Setup

```powershell
# 1. Clone
git clone https://github.com/EruditeCoder108/Phonon-2-STT-for-windows.git
cd Phonon-2-STT-for-windows

# 2. Create a virtual environment
python -m venv .venv
.venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run
pythonw run.py
```

> `pythonw` instead of `python` keeps the terminal window hidden. Use `python run.py` if you want to see log output.

On first launch the speech model will be downloaded (this takes a minute). After that it's instant.

---

## Usage

| Action | What it does |
|---|---|
| App starts | Orb appears grey in the bottom-right corner |
| Engine ready | Orb blooms from grey into full color |
| **Click orb** | Start / stop dictation |
| **Right-click orb** | Context menu (start, pause, settings, quit) |
| **Long-press orb** (0.5s) | Open Quick Orb Customizer |
| **Ctrl+Space** | Start / stop dictation (global hotkey, works anywhere) |
| Speak | Words are transcribed and typed at your cursor |
| Orb pulses | Volume-reactive — bigger pulse = louder voice |

---

## Orb Customizer

Long-press the orb (or right-click → **🔮 Orb Appearance & Settings...**) to customize:

- **Color Theme** — Rainbow Chroma Flow (auto-cycling), Electric Cyan & Ice Blue, Solar Amber & Gold, Emerald Neon Green, Royal Violet, or Custom 360° Hue.
- **Voice Color Shimmer** — Speaking dynamically shimmers the orb's color with your voice volume in real time (accelerating rainbow flow or shifting theme tones).
- **Animation Style** — Dynamic Liquid (organic morphing liquid blobs) or Calm Solid Glow.
- **Animation Pace** — 🐢 Gentle, ⚡ Balanced, or 🔥 Brisk motion speeds. Idle animation stays serene and calm, picking up smoothly when speaking.
- **Base Orb Size** — Adjustable slider (45% to 100%) with dynamic circular hit-test scaling.
- **Orb Opacity** — Adjustable slider (25% to 100%) for translucent or solid acrylic desktop presence.
- **Voice Size Reactivity** — Off, Subtle (~10%), Normal (~20%), or High (~35%).
- **Windows Autostart** — Start silently with Windows on login.

---

## Smart Noise Filtering & Sentence Joining

**Only speech reaches the model.** A Silero VAD scores every 32 ms frame. An utterance opens only after several *consecutive* voiced frames, so keyboard clicks, coughs and breaths never start one. Each utterance keeps a short pre-roll (soft word onsets), has its trailing silence trimmed, and carries evidence measured on the **voiced frames only** (RMS, mean VAD confidence, voiced duration, ambient noise floor).

**Evidence-based filter** (`filters.py`):
- Pure fillers (`um`, `uh`, `hmm`, …) are always dropped; degenerate repetition loops are dropped.
- Speech must stand clearly above the ambient noise floor (SNR) — independent of how long you paused beforehand.
- A *lone* common word (`yes`, `okay`, `the`, …) is kept only if the VAD was confident and it lasted long enough to be a word. Say "yes" clearly and it goes through, at any pause length.

**No more "Period. Capital." at every pause** (`pipeline.py`). The model decodes each clip as a standalone sentence, so a mid-sentence pause used to produce `I was thinking. About going…`. Now:
1. A phrase that follows closely is decoded together with the previous phrase's audio, and word timestamps select only the new words — the model hears one continuous sentence.
2. Sentence-final punctuation is held back until the join is known (typed with the next phrase, or after a short hold if nothing follows, or when you stop).

**Background voices (video, TV, people nearby).** The app learns how loud *you* are (in memory only — never saved, so a stale value can't lock you out). Speech much quieter than that neither opens an utterance nor keeps one open, so a video playing behind you can't glue itself to your sentence or get typed. Needs one phrase of you speaking to learn. Speak at your normal volume; whispering will be treated as background (turn the option off in Dashboard → Audio if you whisper).

**Lone sounds.** A single "word" decoded from a long or low-confidence stretch (a hum, sigh or breath that the model snapped onto a word, e.g. "hmmm" → "True") is dropped. "Uh"/"um" that the model writes into sentences are removed (option).

**Diagnosing.** `python tools/diagnose.py` listens on your mic and prints, for every sound, what was measured and whether it would be typed or dropped (and why) — without typing anything. The app also writes `~/.phonon2.log` (decision lines never contain your text when history is off).

Tunables (Dashboard → Audio): pause length that ends a phrase (500–1400 ms), smart sentence joining on/off, history on/off.

---

## Known limitations

- **Windows only.** Uses Win32 APIs (`SendInput`, clipboard), WASAPI audio, PySide6 WebEngine.
- **Elevated windows.** Windows blocks injected input into windows running as administrator unless Phonon-2 is also elevated; the app shows a notice instead of failing silently.
- **English only**, and no vocabulary biasing inside the model on CPU yet — custom vocabulary is applied as text replacement after transcription.
- **Dictation history** is stored in plain text at `~/.phonon2_history.json`; turn it off in Dashboard → Audio if you dictate sensitive text.
- **Requires a capable CPU/GPU.** The local speech model loads into system memory. Tested on modern Intel/AMD processors with fast local inference.

---

## Stack

| Component | Library / Tool |
|---|---|
| UI framework | PySide6 (Qt 6) |
| Orb animation | CSS/SVG via QWebEngineView (Uiverse.io base by andrew-manzyk) |
| Speech engine | fermion-research (`phonon-2`) |
| Deep learning runtime | PyTorch |
| Audio capture | sounddevice (PortAudio) |
| Voice activity detection | Silero VAD via onnxruntime |
| Text injection | Win32 `SendInput` (Unicode) |
| Global hotkey | Win32 keyboard hook |
| Tray icon | PySide6 QSystemTrayIcon |

---

## Project structure

```
.
├── run.py                   # Entry point
├── requirements.txt
├── tools/
│   ├── diagnose.py          # live mic diagnostic (no typing)
│   └── preview_sounds.py    # audition / export the three cues
├── tests/
│   └── test_core.py         # python -m unittest discover -s tests
├── start_silent.vbs         # Launch without any terminal window
├── start.bat
├── assets/
│   ├── icon.png
│   └── icon.ico
└── src/
    ├── main.py              # App orchestrator
    ├── config.py            # Load/save JSON config
    ├── core/
    │   ├── audio.py         # Hot-mic WASAPI capture
    │   ├── vad.py           # Silero VAD (ONNX) + energy fallback
    │   ├── segmenter.py     # VAD state machine → utterances + evidence
    │   ├── filters.py       # Evidence-based speech/noise decision
    │   ├── pipeline.py      # Stitching, deferred punctuation, injection
    │   ├── engine.py        # Local speech server manager
    │   ├── hotkey.py        # Global Ctrl+Space hook
    │   ├── injector.py      # Win32 text injection
    │   ├── vocabulary.py    # Custom word replacements
    │   ├── history.py       # Session history
    │   └── sound_effects.py # Optional audio feedback
    └── ui/
        ├── hud.py               # Floating orb HUD
        ├── orb_settings_dialog.py  # Quick customizer
        ├── tray.py              # System tray
        ├── dashboard.py         # Full settings window
```

---

## License

MIT — do whatever you want with it.

---

<p align="center">Built with Python, PySide6, and a local speech model that never phones home.</p>
