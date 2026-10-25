# Phase two: Whistle selection and USB microphone recovery

Implemented on 6 October 2026. Existing installations keep Phonon-2 selected.

## Speech engine selection

Settings → Audio & Voice → Speech Engine offers Phonon-2 and Whistle. Switching
ends the active dictation session, drains its final speech and punctuation, then
loads the selected engine on the transcription worker. Rapid selections load the
latest queued choice; delayed messages from replaced engines are ignored.

Whistle uses the official Windows Needle executable and model, pinned to the
revisions recorded in `src/core/whistle.py`. First use installs approximately
18.5 MB into `.models/whistle`, reusing the verified research downloads when
available. Both files require matching SHA-256 hashes before use; downloads are
atomic, bounded and cancellable. “Retry loading engine” retries failed startup.
The model is exercised on silence before readiness is published.

The adapter preserves text, word timestamps and explicit failures for the existing
pipeline. It runs one hidden, bounded child per decode, removes temporary WAVs,
kills timed-out children, and allows final queued speech to drain during shutdown.
Sentence stitching falls back to an independent phrase when combined audio would
exceed Whistle's 30-second limit. Whistle currently uses English dictation.

The child receives `NEEDLE_TELEMETRY=0` and `DO_NOT_TRACK=1`, the opt-outs
documented in the [official README](https://github.com/cactus-compute/needle).
This is not an independent audit of the binary's network behavior. Initial asset
downloads require internet access. Decoder processes reload weights per request;
a persistent runtime and broader accent/accuracy evaluation remain future work.
The subsequent [live dictation improvement](live-typing.md) adds rolling Whistle
recognition and Phonon's native live connection, with updated measurements and tests.

## Microphones

- A three-second background check compares a fresh isolated device scan with the
  cached list and detects inactive streams or callbacks stalled for over three seconds.
- Settings switches microphones through the background refresh operation. Driver
  errors produce notices and release the session guard rather than escaping the UI.
- Refresh closes all registered input and output streams before reinitializing
  PortAudio; stream start/open/close use the same lock. Old callbacks cannot feed a
  new session. Failed initialization is retried even when device names are unchanged.
- Unplugging during dictation stops and flushes that session before reopening a
  working default mic. Start a new session when it is ready. Reconnecting the saved
  USB device automatically makes it preferred again; dictation does not resume by itself.
- Settings displays the actual opened device and an explicit fallback message.
  A disconnected saved choice is labeled and disabled, preserving the preference
  for reconnection. “Test Mic” meters the application's actual capture stream.
- Capture endpoints on legacy APIs remain visible. Names such as USB Mixer and
  USB Speakerphone are accepted. Stereo inputs are downmixed to mono, including
  USB interfaces whose microphone is plugged into their second channel. Duplicate
  names are rejected with guidance to rename the devices in Windows Sound settings.

Selection is persisted by full device name, never a PortAudio index. It does not
yet use Windows endpoint GUIDs. Identically named endpoints must be renamed;
hardware requiring unsupported channel layouts or rates can still fall back,
with the actual device shown. Phonon cleanup now terminates only owned processes.

## Verification

The complete regression suite contains **144 tests**, including simulated USB
removal, stale enumeration, failed opens, stereo-only capture, late callbacks,
engine switching, failed downloads, invalid decoder output and timeouts.

The actual Whistle adapter was checked on the public 11-second JFK clip:
three runs produced all 22 reference words correctly (simple normalized WER 0),
with a median wall time of **1,232 ms** in the final standalone run. A run
concurrent with the regression suite and microphone checks took **2,534 ms**,
showing the effect of CPU contention. Silence and steady noise produced empty
transcripts. The real pipeline delivered speech and final punctuation to an output
collector, and a final decode succeeded after a shutdown request. One clip does
not establish accuracy across accents, microphones or everyday dictation.

The attached machine's Realtek microphone passed a real open → callbacks →
refresh → reopen → callbacks → close check, with no audio saved or transcribed.
Only the built-in microphone was connected. Physical USB unplug/replug remains
unverified; its state transitions are covered with simulated hardware. Settings
was rendered with the Windows Qt platform and inspected at 920×800 logical pixels.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe tools\verify_phase_two.py
.\.venv\Scripts\python.exe tools\verify_audio_devices.py --open
# With your USB mic attached:
.\.venv\Scripts\python.exe tools\verify_audio_devices.py --open --mic "Full device name"
.\.venv\Scripts\python.exe -m compileall -q src tests tools
git diff --check
```

Real model verification requires the fixture WAVs documented in
[model research](model-research.md). Regression tests neither record audio nor
inject text. HTTP fixtures and native decoder execution need permission beyond
the Codex restricted sandbox; those checks were run with that restriction lifted.
