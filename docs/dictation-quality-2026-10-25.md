# Dictation quality fixes — October 25, 2026

The audit reproduced severe loss of correctly recognized text when early words changed in the final transcript. The app now defaults to **Complete phrases**, which previews partial recognition and types only completed phrases. The model choice remains Phonon 2.5 English.

## Changes

- Complete phrases delivers final text through the existing filtering, audio-context joining, vocabulary and punctuation pipeline. It does not commit provisional spellings. Existing profiles without a delivery-mode preference inherit this mode and a visible preview; subsequent explicit preview choices are retained. The profile file is not rewritten merely by loading it.
- Recognition previews use paced HTTP requests with an adaptive cooldown. Only the latest pending snapshot is retained. Complete-phrase previews use at most eight seconds of recent audio; final recognition always receives the complete phrase. The app no longer starts the runtime's independent rolling WebSocket decoder, which could accumulate work behind real-time audio.
- Long complete phrases split at a quiet point, with distinct utterance IDs. Fast live words retains its existing hard-cut behavior because already-committed words cannot be moved into another phrase.
- Tray → Recent transcripts retains up to 20 final transcripts, capped at 20,000 characters per entry, in RAM. Delivered, filtered and blocked finals remain available independently of optional persistent history. Copying is explicit; opening recovery does not alter the target field or clipboard. Clear removes the in-memory entries; they also disappear when the process exits.
- Fast live words handles contraction boundaries such as `I'm` / `I am`, and can append a uniquely aligned continuation after an older spelling changes. Ambiguous revisions preserve the existing field and retain the full final for review. This optional mode still cannot guarantee that earlier provisional words or punctuation match the final result.
- Single-word duration is no longer an unconditional rejection rule. Timestamp mismatch and weak-speech/noise checks remain. Deliberate repetition is accepted when speech evidence and word rate support it; low-confidence or implausibly rapid loops remain rejected.
- Recognition terms are separate from text replacements. Up to 25 optional terms are forwarded to supported Phonon HTTP requests, including previews and finals. They default to empty because bias can introduce new errors. Whistle does not receive these terms.
- Native focused-control identity and Windows focus-event generations guard delivery. Event tracking runs on a dedicated message thread and records no text or keys. A focus change blocks the phrase and retains its final. This does not prove ownership of a browser text range or unchanged caret; the app does not attempt arbitrary backspacing or replacement.

## Verification and evidence

The frozen audit inputs are in `.model-research/dictation-audit-2026-10-24/`. Fix snapshots, scripts and retest evidence are in `.model-research/dictation-fix-2026-10-25/`. These ignored research files include personal audio/transcripts and are not release assets.

`tests-final.log` records the regression suite. `retest.json` records real-time-paced playback through the actual VAD, worker and pipeline with an output collector, using the same three personal recordings and reference text. Phonon 2.5 English additionally exercises the public/noise/long-speech corpus and a repeated quiet recording. Fast mode is measured separately. Each completed accurate-mode case asserts zero word difference between accepted final text and delivered output.

Reproduce a small public check with:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe tools/verify_live_typing.py --model phonon-2.5-en --mode accurate
```

The accuracy-mode tradeoff is explicit: insertion waits for phrase completion, while the preview remains revisable. Word error against a human reference includes recognition mistakes that remain in the model output. Zero delivery difference is not zero model error, and collector results are not proof of behavior in every Windows editor.
