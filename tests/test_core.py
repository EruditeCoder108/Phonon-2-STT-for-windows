"""
Regression tests for the dictation layer. No microphone, model or GUI required.

Run:  python -m unittest discover -s tests -v
"""

import os
import subprocess
import sys
import threading
import unittest
from collections import deque

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.engine import PhononEngine, TranscribeResult
from src.core.filters import judge
from src.core.pipeline import (CONTEXT_GAP_S, CONTEXT_SILENCE_S, PUNCT_HOLD_S, TAIL_PAD_S, DictationPipeline,
                               split_trail, strip_fillers)
from src.core.segmenter import FRAME_S, Utterance, UtteranceSegmenter
from src.core import sound_effects as SFX
from src.core.vocabulary import VocabularyEngine

FRAME = 512
SR = 16000


# ── helpers ──

class ScriptedVAD:
    """Probability = 1.0 for loud frames, 0.0 for quiet ones (deterministic stand-in for Silero)."""
    name = "scripted"

    def reset(self):
        pass

    def probability(self, frame):
        return 1.0 if np.sqrt(np.mean(frame.astype(np.float32) ** 2)) > 500 else 0.0


def frames(n, rms):
    rng = np.random.default_rng(n * 31 + int(rms))
    return (rng.standard_normal(n * FRAME) * rms).clip(-32768, 32767).astype(np.int16).tobytes()


def make_utt(start=0.0, dur=2.0, speech_s=1.5, prob=0.98, rms=3000.0, floor=40.0, pcm=None):
    return Utterance(pcm=pcm if pcm is not None else b"\x01\x00" * int(dur * SR), start_s=start, end_s=start + dur,
                     speech_s=speech_s, mean_prob=prob, speech_rms=rms, noise_floor=floor)


def words(text, t0=0.0, per=0.3):
    out, t = [], t0
    for w in text.split():
        out.append({"word": w, "start": t, "end": t + per * 0.8})
        t += per
    return out


class FakeEngine:
    def __init__(self):
        self.calls = []
        self.queue = deque()

    def transcribe(self, pcm, sample_rate=16000):
        self.calls.append(len(pcm) / 32000.0)
        return self.queue.popleft()


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make_pipeline(engine, **kw):
    typed = []
    clock = FakeClock()
    cfg = {"context_stitch": True}
    hwnd = {"v": 1}
    pipe = DictationPipeline(
        engine, VocabularyEngine({}), inject=lambda t: typed.append(t) or True,
        get_config=lambda k, d=None: cfg.get(k, d), get_hwnd=lambda: hwnd["v"], clock=clock, **kw)
    return pipe, typed, clock, cfg, hwnd


# ── tests ──

class VocabularyTests(unittest.TestCase):
    def test_backslashes_are_literal(self):
        v = VocabularyEngine({"my path": r"C:\Users\me\Docs"})
        self.assertEqual(v.apply("open my path now"), r"open C:\Users\me\Docs now")

    def test_longest_trigger_wins(self):
        v = VocabularyEngine({"my email": "a@b.c", "my email address": "a@b.c (work)"})
        self.assertEqual(v.apply("send my email address"), "send a@b.c (work)")

    def test_symbol_edged_trigger(self):
        v = VocabularyEngine({"c++": "C plus plus"})
        self.assertEqual(v.apply("I like c++ a lot"), "I like C plus plus a lot")


class SplitTrailTests(unittest.TestCase):
    def test_cases(self):
        self.assertEqual(split_trail("store."), ("store", "."))
        self.assertEqual(split_trail("it"), ("it", ""))
        self.assertEqual(split_trail("really?!"), ("really", "?!"))
        self.assertEqual(split_trail("don't"), ("don't", ""))
        self.assertEqual(split_trail("."), (".", ""))


class FilterTests(unittest.TestCase):
    def test_filler_always_rejected(self):
        self.assertFalse(judge("Um.", make_utt())[0])

    def test_confident_yes_passes_unconfident_fails(self):
        self.assertTrue(judge("Yes.", make_utt(speech_s=0.4, prob=0.97))[0])
        self.assertFalse(judge("Yes.", make_utt(speech_s=0.4, prob=0.6))[0])
        self.assertFalse(judge("Yes.", make_utt(speech_s=0.12, prob=0.97))[0])

    def test_quiet_after_long_pause_is_not_penalised(self):
        # Old code judged the whole buffer (silence included) and dropped this.
        self.assertTrue(judge("send the report", make_utt(rms=550.0, floor=30.0))[0])

    def test_no_signal_over_noise_rejected(self):
        self.assertFalse(judge("hello there", make_utt(rms=150.0, floor=120.0))[0])

    def test_repetition_loop_rejected(self):
        self.assertFalse(judge("the the the the the", make_utt())[0])


class SegmenterTests(unittest.TestCase):
    def run_seg(self, chunks, **kw):
        out = []
        seg = UtteranceSegmenter(ScriptedVAD(), out.append, **kw)
        seg.reset()
        for c in chunks:
            seg.feed(c)
        seg.flush()
        return out

    def test_single_phrase_with_padding_and_trimmed_tail(self):
        out = self.run_seg([frames(30, 20), frames(40, 3000), frames(60, 20)])
        self.assertEqual(len(out), 1)
        u = out[0]
        self.assertAlmostEqual(u.speech_s, 40 * FRAME_S, delta=0.1)
        # speech + 0.3s pre-roll + 0.25s kept tail, NOT the whole 60-frame (1.9s) silence
        self.assertLess(u.duration_s, 40 * FRAME_S + 0.3 + 0.25 + 0.15)
        self.assertGreater(u.speech_rms, 2500)

    def test_clicks_never_open_an_utterance(self):
        chunks = []
        for _ in range(8):
            chunks += [frames(40, 20), frames(1, 9000)]
        self.assertEqual(self.run_seg(chunks), [])

    def test_short_blip_discarded_but_short_word_kept(self):
        self.assertEqual(self.run_seg([frames(30, 20), frames(3, 3000), frames(40, 20)]), [])
        self.assertEqual(len(self.run_seg([frames(30, 20), frames(9, 3000), frames(40, 20)])), 1)

    def test_two_phrases_split_on_pause(self):
        out = self.run_seg([frames(20, 20), frames(30, 3000), frames(40, 20), frames(30, 3000), frames(40, 20)])
        self.assertEqual(len(out), 2)
        self.assertGreater(out[1].start_s, out[0].end_s)

    def test_short_pause_does_not_split(self):
        out = self.run_seg([frames(20, 20), frames(30, 3000), frames(10, 20), frames(30, 3000), frames(40, 20)])
        self.assertEqual(len(out), 1)

    def test_flush_emits_in_flight_speech_as_forced(self):
        out = self.run_seg([frames(20, 20), frames(30, 3000)])
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].forced)

    def test_long_speech_is_split_at_a_quiet_frame(self):
        speech = frames(20, 3000) + frames(1, 100) + frames(20, 3000)
        out = self.run_seg([frames(10, 20)] + [speech] * 40, max_utterance_s=8.0)
        self.assertGreater(len(out), 1)
        for u in out[:-1]:
            self.assertTrue(u.forced)
            self.assertLessEqual(u.duration_s, 8.0 + 0.1)

    def test_noise_floor_not_dragged_up_by_clicks(self):
        out = []
        seg = UtteranceSegmenter(ScriptedVAD(), out.append)
        seg.reset()
        for _ in range(10):
            seg.feed(frames(40, 20))
            seg.feed(frames(1, 9000))
        self.assertLess(seg.noise_floor, 100)


class PipelineTests(unittest.TestCase):
    def test_single_utterance_defers_then_flushes_period(self):
        eng = FakeEngine()
        eng.queue.append(TranscribeResult(text="Send the report."))
        pipe, typed, clock, *_ = make_pipeline(eng)
        pipe.process(make_utt())
        self.assertEqual(typed, ["Send the report"])          # words immediately, mark held back
        pipe.tick()
        self.assertEqual(typed, ["Send the report"])          # hold not expired yet
        clock.t += PUNCT_HOLD_S + 0.1
        pipe.tick()
        self.assertEqual(typed, ["Send the report", ". "])

    def test_stitching_replaces_period_with_comma_and_lowercase(self):
        eng = FakeEngine()
        eng.queue.append(TranscribeResult(text="I was thinking about going to the store."))
        pipe, typed, *_ = make_pipeline(eng)
        prev_pcm = b"\x01\x00" * int(2.0 * SR)
        pipe.process(make_utt(start=0.0, dur=2.0, pcm=prev_pcm))

        ctx_dur = 2.0
        t_new = ctx_dur + CONTEXT_SILENCE_S + 0.2
        combined = words("I was thinking about going to the store,") + words("and picking up milk.", t0=t_new)
        combined[7]["word"] = "store,"
        # context words fall before the boundary (ctx_dur + gap/2); new words after it
        for i, w in enumerate(combined[:8]):
            w["start"], w["end"] = i * 0.2, i * 0.2 + 0.15
        eng.queue.append(TranscribeResult(text="x", words=combined))
        pipe.process(make_utt(start=3.0, dur=2.0))
        self.assertEqual(typed[0], "I was thinking about going to the store")
        self.assertEqual(typed[1], ", and picking up milk")
        self.assertAlmostEqual(eng.calls[1], 2.0 + CONTEXT_SILENCE_S + 2.0 + TAIL_PAD_S, places=2)

    def test_gap_too_long_does_not_stitch(self):
        eng = FakeEngine()
        eng.queue.append(TranscribeResult(text="First sentence."))
        eng.queue.append(TranscribeResult(text="Second sentence."))
        pipe, typed, *_ = make_pipeline(eng)
        pipe.process(make_utt(start=0.0, dur=2.0))
        pipe.process(make_utt(start=2.0 + CONTEXT_GAP_S + 1.0, dur=2.0))
        self.assertEqual("".join(typed), "First sentence. Second sentence")
        self.assertEqual(len(eng.calls), 2)
        self.assertAlmostEqual(eng.calls[1], 2.0 + TAIL_PAD_S, places=2)    # standalone decode, no context audio

    def test_stitch_falls_back_when_no_word_timestamps(self):
        eng = FakeEngine()
        eng.queue.append(TranscribeResult(text="First part."))
        eng.queue.append(TranscribeResult(text="combined text", words=[]))     # stitched attempt: unusable
        eng.queue.append(TranscribeResult(text="Second part."))                # standalone fallback
        pipe, typed, *_ = make_pipeline(eng)
        pipe.process(make_utt(start=0.0))
        pipe.process(make_utt(start=2.5))
        self.assertEqual("".join(typed), "First part. Second part")

    def test_stitching_can_be_disabled(self):
        eng = FakeEngine()
        eng.queue.append(TranscribeResult(text="One."))
        eng.queue.append(TranscribeResult(text="Two."))
        pipe, typed, clock, cfg, _ = make_pipeline(eng)
        cfg["context_stitch"] = False
        pipe.process(make_utt(start=0.0))
        pipe.process(make_utt(start=2.5))
        self.assertEqual(len(eng.calls), 2)
        self.assertAlmostEqual(eng.calls[1], 2.0 + TAIL_PAD_S, places=2)

    def test_rejected_noise_leaves_context_intact(self):
        eng = FakeEngine()
        eng.queue.append(TranscribeResult(text="Hello world."))
        eng.queue.append(TranscribeResult(text="Yeah.", words=[]))   # stitched attempt: unusable
        eng.queue.append(TranscribeResult(text="Yeah."))             # plain fallback decode
        pipe, typed, *_ = make_pipeline(eng)
        pipe.process(make_utt(start=0.0))
        pipe.process(make_utt(start=2.5, speech_s=0.15, prob=0.55))   # unconfident lone "Yeah"
        self.assertEqual(typed, ["Hello world"])
        pipe.flush()
        self.assertEqual(typed, ["Hello world", ". "])

    def test_engine_error_is_reported_not_swallowed(self):
        eng = FakeEngine()
        eng.queue.append(TranscribeResult(error=True))
        events = []
        pipe, typed, *_ = make_pipeline(eng, on_event=lambda k, m: events.append(k))
        pipe.process(make_utt())
        self.assertEqual(typed, [])
        self.assertEqual(events, ["error"])

    def test_speech_in_progress_postpones_flush(self):
        eng = FakeEngine()
        eng.queue.append(TranscribeResult(text="Hi there."))
        pipe, typed, clock, *_ = make_pipeline(eng)
        pipe.process(make_utt())
        clock.t += PUNCT_HOLD_S + 1
        pipe.set_speech_active(True)
        pipe.tick()
        self.assertEqual(typed, ["Hi there"])
        pipe.set_speech_active(False)
        pipe.tick()
        self.assertEqual(typed, ["Hi there", ". "])

    def test_deferred_mark_dropped_if_focus_moved(self):
        eng = FakeEngine()
        eng.queue.append(TranscribeResult(text="Hi there."))
        pipe, typed, clock, cfg, hwnd = make_pipeline(eng)
        pipe.process(make_utt())
        hwnd["v"] = 2                                       # user switched windows
        pipe.flush()
        self.assertEqual(typed, ["Hi there"])

    def test_vocabulary_applies_to_body_only(self):
        eng = FakeEngine()
        eng.queue.append(TranscribeResult(text="Send it to my email."))
        pipe, typed, *_ = make_pipeline(eng)
        pipe.vocab.set_replacements({"my email": "a@b.c"})
        pipe.process(make_utt())
        self.assertEqual(typed, ["Send it to a@b.c"])


class VoiceGateTests(unittest.TestCase):
    def run_seg(self, chunks, level=3000.0, gate=True):
        out = []
        seg = UtteranceSegmenter(ScriptedVAD(), out.append, voice_gate=gate)
        seg.set_voice_level(level)
        seg.reset()
        for c in chunks:
            seg.feed(c)
        seg.flush()
        return out

    def test_quiet_background_speech_never_opens_an_utterance(self):
        # 800 RMS is "speech" to the VAD but only ~0.27 of a 3000-RMS voice
        self.assertEqual(self.run_seg([frames(20, 20), frames(80, 800), frames(20, 20)]), [])

    def test_background_does_not_keep_an_utterance_open(self):
        out = self.run_seg([frames(20, 20), frames(30, 3000), frames(120, 800), frames(10, 20)])
        self.assertEqual(len(out), 1)
        self.assertLess(out[0].duration_s, 30 * FRAME_S + 1.6)     # ended ~0.7s after YOU stopped, not after the video

    def test_gate_off_restores_old_behaviour(self):
        out = self.run_seg([frames(20, 20), frames(30, 3000), frames(120, 800), frames(10, 20)], gate=False)
        self.assertGreater(out[0].duration_s, 100 * FRAME_S)

    def test_level_is_learned_and_ratio_reported(self):
        out = self.run_seg([frames(20, 20), frames(30, 3000), frames(40, 20), frames(30, 1800), frames(40, 20)],
                           level=None)
        self.assertIsNone(out[0].level_ratio)
        self.assertAlmostEqual(out[1].level_ratio, 0.6, delta=0.1)

    def test_background_never_drags_level_down(self):
        out = []
        seg = UtteranceSegmenter(ScriptedVAD(), out.append, voice_gate=True, start_rel=0.0, cont_rel=0.0)
        seg.set_voice_level(3000.0)
        seg.reset()
        for c in [frames(20, 20), frames(30, 700), frames(40, 20)]:
            seg.feed(c)
        seg.flush()
        self.assertEqual(seg.voice_level, 3000.0)


    def test_stale_level_is_forgotten(self):
        seg = UtteranceSegmenter(ScriptedVAD(), lambda u: None)
        seg.set_voice_level(3000.0)
        seg._level_time -= 21 * 60
        seg.reset()
        self.assertIsNone(seg.voice_level)
        seg.set_voice_level(3000.0)
        seg.forget_voice_level()
        self.assertIsNone(seg.voice_level)


class LoneSoundTests(unittest.TestCase):
    def test_background_ratio_rejected(self):
        u = make_utt()
        u.level_ratio = 0.2
        self.assertFalse(judge("send the report", u)[0])
        u.level_ratio = 0.9
        self.assertTrue(judge("send the report", u)[0])

    def test_hum_decoded_as_a_word_is_rejected(self):
        self.assertFalse(judge("True.", make_utt(speech_s=0.5, prob=0.80))[0])            # unsure
        self.assertFalse(judge("True.", make_utt(speech_s=1.3, prob=0.97))[0])            # drawn out
        self.assertFalse(judge("True.", make_utt(speech_s=0.8, prob=0.97), word_span_s=0.16)[0])   # sound >> word

    def test_real_lone_words_still_pass(self):
        self.assertTrue(judge("Tomorrow.", make_utt(speech_s=0.55, prob=0.96), word_span_s=0.4)[0])
        self.assertTrue(judge("Yes.", make_utt(speech_s=0.40, prob=0.97), word_span_s=0.3)[0])

    def test_multiword_phrases_unaffected_by_lone_rules(self):
        self.assertTrue(judge("I think so", make_utt(speech_s=2.0, prob=0.8))[0])


class FillerTests(unittest.TestCase):
    def test_mid_sentence_filler_removed(self):
        self.assertEqual(strip_fillers(["So,", "uh,", "I", "think"], True), ["So,", "I", "think"])

    def test_leading_filler_keeps_sentence_capital(self):
        self.assertEqual(strip_fillers(["Um,", "the", "report."], True), ["The", "report."])
        self.assertEqual(strip_fillers(["Um,", "the", "report."], False), ["the", "report."])

    def test_trailing_filler_keeps_final_mark(self):
        self.assertEqual(strip_fillers(["It's", "done.", "Um."], True), ["It's", "done."])
        self.assertEqual(strip_fillers(["It's", "done", "um."], True), ["It's", "done."])

    def test_real_words_untouched(self):
        words_ = ["The", "umbrella", "is", "here.", "Hm-hm"]
        self.assertEqual(strip_fillers(words_[:4], True), words_[:4])

    def test_pipeline_applies_it_and_can_be_disabled(self):
        eng = FakeEngine()
        eng.queue.append(TranscribeResult(text="Uh, send the report, um, today."))
        pipe, typed, clock, cfg, _ = make_pipeline(eng)
        pipe.process(make_utt(speech_s=2.0))
        self.assertEqual(typed, ["Send the report, today"])
        eng.queue.append(TranscribeResult(text="Uh, send it."))
        pipe2, typed2, _, cfg2, _ = make_pipeline(eng)
        cfg2["remove_fillers"] = False
        pipe2.process(make_utt(speech_s=2.0))
        self.assertEqual(typed2, ["Uh, send it"])


class SoundTests(unittest.TestCase):
    SR = 48000

    def test_every_cue_in_every_theme_is_clean_quiet_and_short(self):
        limit_s = {"ready": 1.6, "start": 0.7, "stop": 0.8}
        for theme in SFX.THEMES:
            for cue in SFX.CUES:
                a = SFX.render(theme, cue, self.SR)
                where = f"{theme}/{cue}"
                self.assertEqual(a.ndim, 2, where)
                self.assertEqual(a.shape[1], 2, where)                         # stereo
                self.assertTrue(np.isfinite(a).all(), where)
                self.assertLess(float(np.max(np.abs(a))), 0.25, where)           # quiet: Windows' own cues peak ~0.08
                self.assertGreater(float(np.max(np.abs(a))), 0.10, where)        # ... but not inaudible
                self.assertLess(float(np.max(np.abs(a[0]))), 1e-3, where)        # starts silent -> no click
                self.assertLess(float(np.max(np.abs(a[-1]))), 1e-3, where)       # ends silent -> no click
                self.assertLess(abs(float(a.mean())), 0.005, where)              # no DC offset
                self.assertLess(len(a) / self.SR, limit_s[cue], where)

    def test_cues_are_warm_not_shrill(self):
        for theme in SFX.THEMES:
            for cue in SFX.CUES:
                m = SFX.render(theme, cue, self.SR).mean(axis=1)
                spec = np.abs(np.fft.rfft(m)) ** 2
                freqs = np.fft.rfftfreq(len(m), 1 / self.SR)
                centroid = (spec * freqs).sum() / spec.sum()
                self.assertLess(centroid, 1400, f"{theme}/{cue} centroid {centroid:.0f} Hz")
                self.assertLess(spec[freqs > 6000].sum() / spec.sum(), 0.01, f"{theme}/{cue} too much treble")

    def test_start_rises_and_stop_falls(self):
        def dominant_early(theme, cue):
            m = SFX.render(theme, cue, self.SR).mean(axis=1)
            def peak_freq(seg):
                sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 1 << 15)); fr = np.fft.rfftfreq(1 << 15, 1 / self.SR)
                return fr[(fr > 200) & (fr < 2000)][np.argmax(sp[(fr > 200) & (fr < 2000)])]
            w = int(0.06 * self.SR)
            return peak_freq(m[int(0.01 * self.SR):int(0.01 * self.SR) + w]), peak_freq(m[int(0.12 * self.SR):int(0.12 * self.SR) + w])
        for theme in SFX.THEMES:
            first, later = dominant_early(theme, "start")
            self.assertGreater(later, first, f"{theme} start should rise")
            first, later = dominant_early(theme, "stop")
            self.assertLess(later, first, f"{theme} stop should fall")

    def test_disabled_effects_do_not_play(self):
        fx = SFX.SoundEffects(enabled=False)
        fx._ensure_stream = lambda: self.fail("must not open a stream when disabled")
        fx.play_ready(); fx.play_start(); fx.play_stop()
        self.assertEqual(fx._voices, [])

    def test_mixer_callback_sums_voices_and_retires_them(self):
        fx = SFX.SoundEffects(enabled=True)
        fx._voices = [[np.full((100, 2), 0.1, np.float32), 0, 1.0], [np.full((40, 2), 0.2, np.float32), 0, 0.5]]
        out = np.zeros((64, 2), np.float32)
        fx._callback(out, 64, None, None)
        self.assertAlmostEqual(float(out[0, 0]), 0.2, places=5)            # 0.1 + 0.2*0.5
        self.assertAlmostEqual(float(out[50, 0]), 0.1, places=5)           # short voice already over
        self.assertEqual(len(fx._voices), 1)                               # ... and retired
        fx._callback(out, 64, None, None)
        self.assertEqual(len(fx._voices), 0)


class ServerPipeTests(unittest.TestCase):
    def test_unread_stderr_would_stall_but_drain_keeps_child_alive(self):
        """Regression: the server logs a line per request to stderr; an undrained pipe blocks it after ~4 KB."""
        child = ("import sys\n"
                 "for i in range(4000):\n"
                 "    sys.stderr.write('[fermion] 127.0.0.1 \"POST /v1/audio/transcriptions HTTP/1.1\" 200 -\\n')\n"
                 "    sys.stderr.flush()\n")
        proc = subprocess.Popen([sys.executable, "-c", child], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=0x08000000)
        sink = deque(maxlen=10)
        t = threading.Thread(target=PhononEngine._drain, args=(proc.stderr, "err", sink), daemon=True)
        t.start()
        try:
            self.assertEqual(proc.wait(timeout=20), 0, "child blocked on a full stderr pipe")
        finally:
            if proc.poll() is None:
                proc.kill()
            t.join(timeout=5)
            proc.stdout.close()
            proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
