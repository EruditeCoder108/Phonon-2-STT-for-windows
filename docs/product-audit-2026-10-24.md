# Phonon 2.5 migration and product audit

Date: October 24, 2026. Scope: the current Windows Python/PySide6 checkout, including its pre-existing uncommitted reliability and live-typing work.

## Result

The default Phonon model is now **Phonon-2.5 English** (`phonon-2.5-en`), using the installed and pinned `fermion-research==0.2.14` runtime. The optional **Phonon-2.5** model (`phonon-2.5`) supports 25 languages. Both were downloaded, verified by the publisher's pinned checksums, and exercised with downloads disabled. Phonon-2 remains an explicit legacy option, and Whistle remains available.

The architecture is worth keeping: capture, segmentation, recognition, text processing and injection already have useful separation. The next work should improve field targeting, live text quality, and deployment evidence. A model upgrade alone does not establish reliability in every Windows editor.

The saved profile was found to be using Whistle. For this requested switch, only its `speech_engine` and `phonon_model` fields were changed to `phonon` and `phonon-2.5-en`; all other saved values were verified unchanged. The original profile was backed up to `C:\Users\SAMBHAV\.phonon2_config.before-phonon25-20261024.json`.

Restart the app to load the new code and selected model. No repository commits, pushes, installer builds or deployment were performed.

## Changes made

- Replaced the fixed `phonon-2` launcher with an explicit `fermion` CLI call through the project's console Python, passing the model, loopback host and port. This also retains captured diagnostics when the app starts through `pythonw`.
- Added `phonon_model` to the existing profile. Old Phonon profiles inherit 2.5 English while retaining their other preferences. Explicit legacy/multilingual preferences and Whistle selection are preserved.
- Added model selection to Audio & Voice. Changing the model, port or thread count drains the current session and replaces the engine through the existing worker queue.
- Verify the resident server's speech kind and model repository before accepting readiness. A healthy Phonon-2 server can no longer silently satisfy a request for 2.5 English. A conflicting service receives an actionable error and is not killed.
- Include the requested model in phrase-transcription requests. Local health checks bypass system HTTP proxies. Child processes receive Hugging Face telemetry opt-outs.
- Preserve process ownership on a repeated start. Startup failure/timeout reaps the owned process. Closing the Job Object on restart also kills descendant interpreters created by Windows virtual-environment redirectors.
- Make word filtering Unicode-aware so Greek, Cyrillic and accented text survive the multilingual path. English hesitation rules still apply; this is not a claim that every language's punctuation rules have been tuned.
- Fix clipboard UTF-16 sizing for emoji and other non-BMP characters, and allocate/lock the new text before clearing the old clipboard. Rich/custom clipboard formats now cause a fallback to typing, preserving those formats.
- Remove decoded words from filter rejection reasons, preventing that route from leaking words into logs when history is disabled.
- Render history as plain text, preventing dictated markup from becoming rich text in a QLabel.
- Update the settings branding, model information, README, upgrade instructions, model attribution and verification tools.

The existing profile/log/history filenames, autostart registry name, single-instance mutex and taskbar identity remain compatible with the previous app.

## Verification performed

| Check | Result |
|---|---|
| Baseline regression suite | 172 tests passed before edits, with required local socket/temp-file access |
| Final regression suite | **191 tests passed** |
| Python compilation | `compileall` passed for `src`, `tools`, and `tests` |
| Dependency consistency | `pip check`: no broken requirements |
| Whitespace | `git diff --check` passed; Git only warned about normal LF/CRLF conversion |
| Cached offline recognition | Both 2.5 variants passed with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` |
| Word timestamps | All three model choices returned 22 word records within the fixture's time bounds |
| Concurrent recognition | Two concurrent requests per model returned correct reference words without a runtime crash |
| Negative audio | Both 2.5 variants returned empty transcripts for the silence and noise fixtures |
| Watchdog recovery | 2.5 English recovered after owned-server termination, then transcribed correctly |
| Process cleanup | Owned launchers and descendants were gone after stopping all three model choices |
| Live typing | 2.5 English used the real WebSocket transport, VAD, worker, and output collector |
| Microphone | Built-in Realtek microphone delivered callbacks, reopened, and closed successfully; no audio saved or transcribed |
| Settings layout | Native Windows-font renders at 920×800 and 760×480 logical pixels; no horizontal overflow |

The first sandboxed baseline run hit denied socket/temp-file access. The unrestricted baseline passed; those sandbox denials were not classified as product regressions. The first sandboxed UI render lacked working fonts; native Windows-font renders were used for the layout result.

### Local measurements

All comparisons used the same 0.2.14 runtime and public 11-second JFK WAV. Request figures are the median of three whole-clip HTTP requests in the final run. Each model was measured sequentially. These figures describe this small fixture on this computer, not general accuracy or speed guarantees.

| Model | Cached startup | Whole-clip request median | Normalized reference WER | Noise fixture | Server tree working set |
|---|---:|---:|---:|---|---:|
| Phonon-2 | 4.569 s | 0.346 s | 0/22 words | Produced `Yeah.` | 907.0 MiB |
| Phonon-2.5 English | 4.561 s | 0.277 s | 0/22 words | Empty | 907.1 MiB |
| Phonon-2.5, 25 languages | 4.026 s | 0.299 s | 0/22 words | Empty | 907.3 MiB |

The memory column sums the owned launcher, interpreter and other descendants. Shared pages can be counted twice. It excludes the app UI and is neither private memory nor a machine-wide peak. Measuring only the small Windows launcher would misleadingly report about 4 MiB.

The old model's noise output is a **raw decoder result**; it does not prove that this app's VAD and evidence filter would type that noise. The comparison tool intentionally marks that old model's negative-audio check as failed.

In the final real-time-paced live run, first text reached the output collector at **1.106 s**, and the last insertion at **11.253 s**. All reference words were present, but the output included `Americans. Ask not What`: punctuation and capitalization were less consistent than the whole-phrase transcript. WER normalization ignores punctuation and case, so the zero word-error result must not be read as perfect formatted text.

### Evidence and reproduction

Local detailed outputs are ignored research artifacts:

- `.model-research/final-tests.log`
- `.model-research/phonon-model-verification.json`
- `.model-research/live-phonon-2.5-en.json`
- `.model-research/audio-audit.json`
- `.model-research/settings-phonon25-920x800.png`
- `.model-research/settings-phonon25-760x480.png`

From the repository root:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe -m compileall -q src tools tests
.venv\Scripts\python.exe -m pip check

# Optional: populate the publisher-verified caches before the offline checks.
.venv\Scripts\fermion.exe transcribe phonon-2.5-en --download-only
.venv\Scripts\fermion.exe transcribe phonon-2.5 --download-only

# Requires the local public WAV/silence/noise fixtures from the prior model research.
.venv\Scripts\python.exe tools/verify_phonon_models.py --models phonon-2.5-en phonon-2.5
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
.venv\Scripts\python.exe tools/verify_live_typing.py --engine phonon --model phonon-2.5-en

# Brief live microphone callback test; does not save or transcribe audio.
.venv\Scripts\python.exe tools/verify_audio_devices.py --open
```

Including `phonon-2` in the comparison also reproduces its noise-fixture failure and makes that command exit nonzero, while still saving all model results.

## Recommended next improvements

| Priority | Finding and user impact | Concrete next step |
|---|---|---|
| P1 | Target tracking compares the foreground **window**, not the actual focused edit control. Moving from one browser field to another in the same window may redirect delayed words or punctuation. | Track native focused-control identity where available, plus UI Automation identity for browser/editor controls. Cancel queued output when that identity changes. Test same-window field changes and password fields in disposable targets. |
| P1 | Live output can commit punctuation/case before later audio changes the model's interpretation. The JFK run demonstrates this despite zero normalized WER. | Add a conservative phrase mode preset and a live stability corpus with pauses, sentence boundaries and names. Keep uncertain endings/casing provisional; never backspace unrelated user edits. |
| P1 | The automated output collector does not exercise a real destination application, and microphone reopening is not physical USB removal recovery. | Run a release matrix in Notepad, a browser form, VS Code and an elevated target, covering stop/restart, push-to-talk, rapid focus changes, manual typing and USB unplug/replug. |
| P2 | The current accuracy evidence is one short English public recording. Personal names, Indian accents, technical vocabulary, far-field audio and non-English speech remain unmeasured. | Build a consented, local benchmark corpus with exact reference text; track word error, formatted text, first-word delay, stop delay and repeatability separately. |
| P2 | The runtime now supports recognition hotwords, but the vocabulary UI only rewrites already-recognized text. Misheard names may never match a replacement trigger. | Add a separate recognition-terms list, bounded to the runtime's 25-term limit, and pass it through the supported request fields. Validate HTTP and live transport separately before exposing a shared promise. |
| P2 | History is enabled by default and stored as plaintext. When enabled, pipeline logs can also contain text; clearing history does not clear those logs. | Make the retention choice explicit during setup, add a retention period and a clear-history-and-logs action, and consider opt-in history for new profiles while preserving current users' choices. |
| P2 | First download/engine loading has limited visible progress. Energy-VAD fallback is logged but is not clearly surfaced as a degraded recognition mode. | Show downloading/loading/ready/retrying states, a retry action and the actual VAD/backend. Keep progress updates out of the audio callback. |
| P2 | Broad dependency ranges and source launchers do not constitute a reproducible Windows release. | Produce a tested dependency lock and installer, include model/runtime/UI notices, and verify signing and clean-machine behavior before distribution. The README declares MIT but this checkout has no top-level LICENSE file. |
| P2 | Configuration and history accept only limited schema validation. A syntactically valid but malformed history shape can fail when the UI expects entry dictionaries. | Add field-level validation, preserve the original damaged file for recovery, and use safe in-memory defaults without silently rewriting unrelated preferences. |

## Validation limits

- No user speech was used for accuracy measurements. Only the callback test opened the built-in microphone.
- No external editor received injected text during this audit. Clipboard edge cases were tested with mocked Win32 calls and real allocated buffers, without modifying the user's clipboard.
- Physical USB unplug/replug, Bluetooth/headset changes, long-session thermal behavior, DPI/accessibility variations beyond the two renders, and clean-machine installation remain unverified.
- The multilingual model was exercised on English audio. Unicode handling has regression coverage; recognition accuracy in its other 24 languages was not benchmarked here.
- Offline checks disabled Hugging Face/Transformers downloads. They were not an OS firewall packet-capture audit.

## Upstream sources and attribution

- [Fermion release changelog](https://www.fermionresearch.com/changelog/): October 22 model release and October 23 runtime 0.2.14 fixes.
- [Phonon-2.5](https://www.fermionresearch.com/models/phonon-2-5/): supported language list, download/storage sizes, model comparisons and CC BY 4.0 release.
- [Phonon-2.5 English](https://www.fermionresearch.com/models/phonon-2-5-english/): English-specific model by Fermion Research.
- [Server reference](https://www.fermionresearch.com/docs/server-reference/): model identity, request fields and recognition hotwords.
- [Streaming reference](https://www.fermionresearch.com/docs/speech-streaming/): 2.5 model selection and WebSocket protocol.

The published English benchmark average is 4.82% WER, compared with 5.21% for Phonon-2. Those are the publisher's seven-test-set results, not this app's measured personal-dictation accuracy. Hindi is absent from the published 25-language list. The app changes the integration and leaves the downloaded weights unmodified; the model licenses are separate from the app's stated license.
