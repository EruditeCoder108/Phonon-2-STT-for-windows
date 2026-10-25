# Compact speech models for this Windows app

Research checked 6 October 2026. Recommendation: evaluate **Cactus Whistle first for minimum model size**, with **Moonshine Tiny Streaming and Small Streaming** as comparison candidates. Do not replace the default based on a single demonstration or parameter count.

## Shortlist

| Candidate | Size measure | Evidence and fit |
| --- | --- | --- |
| **Cactus Whistle** | **16.9 MB compressed weights**; downloaded Windows CLI is 1.56 MB | Seven languages, 16 kHz input, up to 30 seconds per pass, word timestamps, keyword biasing, Apache-2.0. Actual Windows CLI smoke test succeeded. [Model card](https://huggingface.co/Cactus-Compute/whistle), [Windows binary](https://huggingface.co/Cactus-Compute/needle3/tree/main/windows-x86_64). |
| **Moonshine Tiny Streaming** | **34 million parameters**; shipping quantized artifact size varies by package | Official English Open ASR average: 12.00% WER for the float reference. Shipped quantized LibriSpeech test-clean: 4.83%. Windows library/example available. A small streaming alternative worth testing. [Available models](https://moonshine-voice.readthedocs.io/en/latest/models/available-models/), [quantized accuracy](https://moonshine-voice.readthedocs.io/en/latest/models/accuracy/), [Windows quickstart](https://moonshine-voice.readthedocs.io/en/latest/quickstart/). |
| **Moonshine Small Streaming** | **123 million parameters** | Official English Open ASR average: 7.84% WER for the float reference; shipped quantized LibriSpeech test-clean: 2.61%. My candidate for more accuracy when a larger footprint is acceptable. Same primary sources as Tiny. |
| **Whisper tiny.en / base.en via whisper.cpp** | Tiny: **75 MiB disk / ~273 MB runtime memory**; Base: **142 MiB / ~388 MB**, before further quantization | Mature CPU/Windows implementation and a useful baseline. Integer quantization reduces size. English-only checkpoints should be included for English dictation. [Runtime and memory table](https://github.com/ggml-org/whisper.cpp), [Whisper checkpoints](https://github.com/openai/whisper). |
| **Vosk small English** | **40 MB download** for `small-en-us-0.15`; small models typically use ~300 MB RAM | Offline and Windows-capable; official test-clean WER 9.85%. Punctuation restoration needs separate handling, making it less attractive for this app's formatted prose. [Official models](https://alphacephei.com/vosk/models), [installation](https://alphacephei.com/vosk/install). |
| **NVIDIA Parakeet TDT 0.6B v3** | **600 million parameters**; official Q8 GGUF file **714 MB** | Accuracy-oriented comparison, not the smallest choice. Official average Open ASR WER 6.34%; punctuated text and timestamps. A compatible Windows inference runtime would still need validation. [Model card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3), [official files](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3/tree/main). |

Size measures in this table differ deliberately: parameters, downloaded weights and runtime memory are not interchangeable. WER figures from different datasets or deployment precisions cannot be ranked directly.

There are smaller command-recognition systems: [Moonshine Micro](https://github.com/moonshine-ai/moonshine/tree/main/micro) documents a ~1.3 MiB speech component for its microcontroller demo. Its command-recognition focus makes it a different candidate from unrestricted Windows dictation.

## Whistle: independently checked here

Official model revision: `b358ddadd89b7a713b5aa131f23032d3cca1b251`.
Official runtime repository revision: `2ae11323dc000f5e70c49f7403efa6af12ba9e67`.

Model file: **16,919,407 bytes**, SHA-256 `b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb`.
Runtime: **1,563,136 bytes**, SHA-256 `e8863ca0c06a47d406777077f1ba728b58e57d77fedff252dbe6827d588acc8c`.

Machine: Windows 11, Intel Core i5-1135G7. Tested with the standalone CLI, no Python package installation and no app engine change.

The [public whisper.cpp JFK sample](https://github.com/ggml-org/whisper.cpp/blob/master/samples/jfk.wav) is 11 seconds long. Three fresh processes returned the same 22-word transcript and word timestamps. Case/punctuation-insensitive edit scoring against the known reference was **0 word errors in each run**. This is one familiar public recording, not an independent accuracy benchmark or evidence about accent handling.

| Run | Total process wall time | Runtime-reported time to first token |
| --- | ---: | ---: |
| 1 | 2,002.11 ms | 1,732.3 ms |
| 2 | 1,137.89 ms | 945.8 ms |
| 3 | 1,169.89 ms | 982.4 ms |

Median total wall time: **1,169.89 ms**. Total includes startup and loading; it is not live dictation latency. A separate earlier probe returned empty transcripts for two seconds of silence and seeded low-level noise. Peak runtime memory was not measured.

The vendor's 11.1 ms first-token figure uses an Apple M4 Pro, different from this PC. Its comparison uses Whisper's Python runtime, not optimized whisper.cpp. The [release notes](https://cactuscompute.com/blog/whistle) describe those conditions; do not carry that speed claim into Windows marketing.

## Reproduce the local probe

Downloaded assets and raw results are under the ignored `.model-research/` directory. The probe performs local transcription only, never microphone capture or keyboard injection.

```powershell
.\.venv\Scripts\python.exe tools\benchmark_whistle.py .model-research\jfk.wav `
  --reference "And so my fellow Americans ask not what your country can do for you ask what you can do for your country" `
  --out .model-research\whistle-benchmark.json
```

The script sets `NEEDLE_TELEMETRY=0` and `DO_NOT_TRACK=1`. The local model runs were performed with network access restricted. Before integration, verify the exact runtime's privacy behavior: the [GitHub README](https://github.com/cactus-compute/needle) says telemetry defaults on, while the [device guide](https://cactuscompute.com/blog/needle-supported-devices) describes inference as offline. The published statements are inconsistent, so setting environment variables alone should not be treated as proof.

## What a model swap needs

Update: [Phase two](phase-two.md) implements Whistle selection using a bounded CLI
adapter, checksum-verified assets and the documented telemetry opt-outs. A
persistent runtime and independent network-behavior audit remain future work.

Keep the common `transcribe(16 kHz PCM) -> text + word timestamps + explicit error` contract. Whistle's CLI fields fit that contract, but its process lifecycle and result validation need an adapter. Use a persistent isolated runtime for a fair comparison; spawning an executable per pause pays repeated startup costs. True streaming Moonshine integration needs a separate streaming path rather than assuming every model is a drop-in HTTP server.

For selection, collect roughly 30–50 short reference clips covering the user's normal accent, names, numbers, technical words, soft speech, background voices, short replies and mid-sentence pauses. Compare word errors, punctuation, p50/p95 time from end of speech to committed text, cold-start time, peak process RAM and false positives on noise. Select the smallest candidate that meets the quality target on that panel. No personal audio has been uploaded and no universal winner is established by this research.
