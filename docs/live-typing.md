# Live dictation and latency

**Historical implementation:** the behavior and measurements below describe the October 6 live-typing path. The October 25 [quality fixes](dictation-quality-2026-10-25.md) supersede its default delivery mode and native-stream scheduling. Complete phrases is now the default; Fast live words is optional, and final transcripts remain recoverable in memory.

Implemented and verified on 6 October 2026.

The wrapper previously waited for a pause to decode a preview below the orb, then
waited for phrase completion to type. Phonon already had a persistent server, but
the app did not use its live endpoint. Whistle reloaded the CLI decoder on each
request. Reducing the pause timeout alone would not deliver text during speech.

## Current behavior

- Live typing is enabled by default. Stable words are appended at the cursor
  while speech continues. The orb transcript is off by default and can be enabled
  separately in Settings → Audio & Voice → Speech Filtering & Cadence.
- Phonon uses the installed Fermion runtime's native WebSocket endpoint. Audio is
  sent incrementally through one connection per phrase. Its final result is reused
  without a second batch decode. Transport failures fall back to rolling recognition.
- Whistle uses rolling recognition through the pinned Windows CLI. Its partials
  were less stable in testing, so three successive hypotheses must agree and two
  trailing words remain provisional. Phonon uses two hypotheses and one trailing word.
  Whistle still reloads its decoder per request; persistent native integration remains
  a separate improvement. Its smaller weights do not imply lower end-to-end latency.
- Capture and recognition updates are coalesced: a slow worker receives the latest
  snapshot rather than building a queue of obsolete partials. Final phrases retain
  FIFO ordering. Late partials cannot type again after final completion.
- Whole-phrase double quotation marks are removed by default, including curly quotes.
  Quotes inside a phrase are preserved. The setting can be disabled for quoted dictation.
- The app appends text and does not backspace earlier input. If a final result revises
  an older word, a unique four-word ending and a nearly identical prefix can identify
  the untyped tail. Ambiguous alignment stops that phrase and displays a review notice.
  Earlier words can still need manual correction. Changing foreground windows blocks
  the remaining phrase, even if focus returns before its final result.

The live protocol was verified against installed `fermion-research 0.2.7` and the
[official speech streaming documentation](https://www.fermionresearch.com/docs/speech-streaming/).
The Windows Whistle adapter uses the CLI artifacts documented in
[Needle's official repository](https://github.com/cactus-compute/needle).

## Measured results

The public 11-second JFK WAV was paced to real time through the actual Silero VAD,
segmenter, transcription worker, local model and field-output collector. No microphone
was recorded and no text was injected into an external application in this benchmark.
The timing begins at the start of audio, after model startup.

| Engine | First text during speech | Final insertion after clip end | Normalized word errors |
|---|---:|---:|---:|
| Phonon-2 | 1.11 s (earlier run: 1.16 s) | 0.53 s | 0 / 22 |
| Whistle | 1.69 s | 1.48 s | 0 / 22 |

These are measurements on this machine and this clip, not latency guarantees or
an accuracy evaluation across accents. Punctuation and capitalization differed from
the reference. Native model startup took about 15 seconds in the final Phonon run
and is excluded above. Words arrive in small stable groups, rather than immediately
at every syllable. Short phrases may finish before a stable partial can be committed.

Whistle initially committed “Watch” before correcting it to “What”; the stronger
buffer prevents that observed failure. Another final decode changed “ask” to “as”;
safe tail alignment preserved the earlier correct word and completed the phrase.
Both cases now have regression coverage. This does not prove every possible model
revision will be caught.

Reports and the inspected Windows settings screenshot are local, ignored artifacts:
`.model-research/live-phonon.json`, `.model-research/live-whistle.json`, and
`.model-research/settings-live-windows.png`. Settings had no horizontal overflow
at 920×800, 860×580, or the minimum 760×480 logical pixel size.

## Verification and remaining limits

The full suite contains **172 passing tests**, covering microphone recovery and
previous Windows reliability work as well as live commits, duplicate prevention,
focus changes, quote handling, optional previews, late results, coalescing, native
final reuse, transport failure, and real local WebSocket partial/final/done/error
exchanges. Source compilation and Git whitespace checks also pass.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe tools\verify_live_typing.py --engine phonon
.\.venv\Scripts\python.exe tools\verify_live_typing.py --engine whistle
.\.venv\Scripts\python.exe -m compileall -q src tests tools
git diff --check
```

The real-model verification requires the downloaded public fixture documented in
[model research](model-research.md). Local server tests and native runtime execution
were run outside the restricted Codex sandbox. Win32 injection safeguards have
regression coverage, but this benchmark collected output rather than exercising a
real editor. The focus guard identifies the foreground window, not a specific text
control or caret within it. Physical USB unplug/replug remains unverified on this
machine; see [phase two](phase-two.md).

Restart the app to load these changes. Select Phonon-2 for the faster live path
measured here. Disable live typing to retain phrase completion and audio context
stitching; live commits prioritize delivery speed and cannot retroactively correct
punctuation or casing already typed at a phrase boundary.
