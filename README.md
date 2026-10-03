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
PhononEngine (local HTTP server on 127.0.0.1:8010)
       │    runs fermion-research under the hood
       │    POST /v1/audio/transcriptions  (WAV → text)
       ▼
VocabularyEngine  ──── optional custom word replacements
       │
       ▼
TextInjector  ──── clipboard + Ctrl+V  (or SendInput for terminals)
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
- **`src/core/audio.py`** — Hot-mic WASAPI audio capture via `sounddevice`. The stream is opened once at startup and stays open so start/stop is instant.
- **`src/core/engine.py`** — Starts the local `phonon-2` server, polls `/health`, provides `transcribe_wav_bytes()` (HTTP POST) and streaming WebSocket session support.
- **`src/core/injector.py`** — Win32 clipboard + `SendInput` text injection. Saves and restores clipboard. Falls back to Unicode `SendInput` for terminals.
- **`src/core/hotkey.py`** — Global keyboard hook for `Ctrl+Space` (or any configurable combo). Non-blocking, runs in a background thread.
- **`src/core/vocabulary.py`** — Simple phrase replacement engine (e.g. "my email" → actual address).
- **`src/core/history.py`** — Persists per-session dictation history to disk.
- **`src/core/sound_effects.py`** — Optional sound feedback on dictation start/stop.

---

## Requirements

- Windows 10 or 11 (64-bit)
- Python 3.14
- A working microphone
- ~4–8 GB of RAM (the speech model loads into memory)

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

Long-press the orb (or right-click → Orb Appearance) to change:

- **Color Theme** — Rainbow (cycling), Cyan/Ice Blue, Solar Gold, Emerald, Violet, or Custom hue
- **Animation Style** — Dynamic Liquid (the default morphing blobs) or Calm Solid Glow
- **Voice Reactivity** — How much the orb scales with your voice volume

---

## Known limitations

- **Windows only.** Uses Win32 APIs (`SendInput`, clipboard), WASAPI audio, PySide6 WebEngine.
- **Hallucination on noise.** Background sounds (coughs, keyboard clicks) can get transcribed as short filler words ("yeah", "okay"). This is a model-level limitation — plan to add a noise gate and filler-word filter.
- **Clipboard is temporarily overwritten** during text injection and then restored.
- **Requires a beefy machine.** The local model is not small. Tested on a machine with a modern Intel/AMD CPU. GPU acceleration is used if available via torch.

---

## Stack

| Component | Library / Tool |
|---|---|
| UI framework | PySide6 (Qt 6) |
| Orb animation | CSS/SVG via QWebEngineView (Uiverse.io base by andrew-manzyk) |
| Speech engine | fermion-research (`phonon-2`) |
| Deep learning runtime | PyTorch |
| Audio capture | sounddevice (PortAudio) |
| Text injection | Win32 `SendInput` + clipboard |
| Global hotkey | Win32 keyboard hook |
| Tray icon | PySide6 QSystemTrayIcon |

---

## Project structure

```
.
├── run.py                   # Entry point
├── requirements.txt
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
        └── settings_dialog.py   # Settings form
```

---

## License

MIT — do whatever you want with it.

---

<p align="center">Built with Python, PySide6, and a local speech model that never phones home.</p>
