# Phase one: Windows dictation reliability

Implemented on 6 October 2026, preserving the existing microphone and speculative-decoding work.

## Scope and behavior

- **Stop and pause:** capture flushes its final utterance while callbacks are still accepted. Pause stops capture; resume starts fresh audio and sentence context. The final punctuation follows speech in the FIFO queue.
- **Session ordering:** pipeline resets run on the transcription worker. Hotkey start/stop/toggle actions are queued onto the Qt thread. Rapid stop/start no longer resets context concurrently with a decode.
- **Focus:** each utterance remembers the foreground window at speech onset. Queued or in-flight recognition is discarded with a notice if that window is no longer foreground. A new window receives no previous-window punctuation or audio context. The injector checks the target again before paste and each keyboard batch.
- **Hotkeys:** modifier combinations with letters, digits, function keys, and supported navigation keys work in toggle and push-to-talk modes. Release of the trigger or a required modifier ends push-to-talk. Repeated keydown events and injected keys do not retrigger. Invalid recorded keys are rejected; invalid saved bindings restore Ctrl+Space rather than silently becoming Caps Lock.
- **Recovery and shutdown:** microphone start failure rolls session state back. Failed input streams close before fallback. Device refresh prevents a simultaneous session start and uses console Python for captured output when the app launches through `pythonw`. The watchdog retries failed restarts. Shutdown drains queued speech before terminating the owned server and blocks late microphone/server startup.
- **Sentence joins:** a mismatched context word falls back to independent recognition. Speculative results are invalidated after context changes, and multiple cancellations remain effective.
- **Failed injection:** failed text does not create subsequent sentence context or a successful history entry. Clipboard restoration also runs if paste raises.

## Validation

The suite contains 111 tests: the original 59 plus 52 Windows reliability and probe regressions. Tests include a real offscreen Qt event loop, real orchestrator construction with hardware services substituted, the actual audio segmenter and dictation pipeline, thread-controlled cancellation and startup races, and a real loopback HTTP server.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m compileall -q src tests tools
git diff --check
```

The HTTP integration tests require permission to open loopback sockets. The Codex restricted execution environment denied those sockets; the full suite was run with that restriction lifted. Simulated HTTP 503, failed restart, blocked SendInput, and missing-microphone log lines are expected test scenarios.

The new tests do not install a global keyboard hook, access the microphone, or type into another application. They verify the relevant Windows API calls with substitutes, including keyboard batch sizes and 64-bit INPUT structure layout.

## Limits and next work

- Real Word/browser/terminal dictation, elevated windows, Bluetooth/USB hardware changes, and sleep/wake still need interactive device testing.
- A window handle cannot detect caret movement or a change of text control within the same window. Foreground checks also cannot make Windows SendInput and focus changes atomic; a batch already submitted may finish.
- Normal shutdown waits for accepted recognition requests. A stuck request can delay exit until its existing timeout/retry completes.
- Model quality has not been benchmarked on the user's voice. Phonon-2 remains the app's default; model alternatives and a separate Whistle probe are described in [the research note](model-research.md).
