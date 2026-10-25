"""USB hotplug, engine selection and Whistle failure-path regressions.

No hardware recording, executable downloads or text injection are performed.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from src.config import DEFAULT_CONFIG
from src.core import devices, whistle
from src.core.audio import AudioCaptureEngine
from src.core.engine import TranscribeResult
from src.ui.dashboard import DashboardWindow
from test_core import FakePortAudio, ScriptedVAD, FakeEngine, make_pipeline, make_utt


class MicrophoneTests(unittest.TestCase):
    def make_audio(self, **kwargs):
        with patch("src.core.audio.create_vad", return_value=ScriptedVAD()):
            audio = AudioCaptureEngine(**kwargs)
        self.addCleanup(audio.close_stream)
        return audio

    def test_usb_mixer_and_speakerphone_are_not_hidden_by_name(self):
        fake = FakePortAudio()
        fake.devs.extend([
            {"name": "USB Mixer Input", "hostapi": 1, "max_input_channels": 2},
            {"name": "USB Speakerphone Microphone", "hostapi": 1, "max_input_channels": 1},
        ])
        with patch.object(devices, "sd", fake):
            self.assertIn("USB Mixer Input", devices.list_microphones())
            self.assertIn("USB Speakerphone Microphone", devices.list_microphones())

    def test_duplicate_names_fail_with_actionable_error(self):
        fake = FakePortAudio()
        fake.devs.extend([{"name": "USB Mic", "hostapi": 1, "max_input_channels": 1}] * 2)
        with patch.object(devices, "sd", fake):
            with self.assertRaisesRegex(RuntimeError, "Rename them"):
                devices.input_candidates("USB Mic")

    def test_capture_endpoint_on_legacy_api_is_visible(self):
        fake = FakePortAudio()
        fake.devs.append({"name": "Legacy USB Mic", "hostapi": 0, "max_input_channels": 1})
        with patch.object(devices, "sd", fake):
            self.assertIn("Legacy USB Mic", devices.list_microphones())

    def test_no_input_is_recoverable_error_not_raise_none(self):
        audio = self.make_audio()
        with patch.object(devices, "input_candidates", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "No microphone"):
                audio.warm_up()
        self.assertTrue(audio.needs_recovery)

    def test_unplugged_usb_reports_actual_fallback(self):
        audio = self.make_audio(device_name="USB Mic")
        with patch.object(devices, "input_candidates", return_value=[(2, None)]), \
             patch("src.core.audio.sd.query_devices", return_value={"name": "Internal Mic", "max_input_channels": 1}), \
             patch("src.core.audio.sd.RawInputStream", return_value=Mock(active=True)):
            audio.warm_up()
        self.assertEqual(audio.active_device, "Internal Mic")
        self.assertIn("using Internal Mic", audio.fallback_reason)

    def test_stereo_only_usb_is_downmixed_for_segmenter(self):
        audio = self.make_audio(device_name="USB Mic")
        stream = Mock(active=True)
        callbacks = []
        def open_stream(**kwargs):
            if kwargs["channels"] == 1:
                raise RuntimeError("requires stereo")
            callbacks.append(kwargs["callback"])
            return stream
        with patch.object(devices, "input_candidates", return_value=[(2, None)]), \
             patch("src.core.audio.sd.query_devices", return_value={"name": "USB Mic", "max_input_channels": 2}), \
             patch("src.core.audio.sd.RawInputStream", side_effect=open_stream):
            audio.warm_up()
        audio._segmenter = Mock()
        audio._is_recording = True
        import numpy as np
        stereo = np.tile(np.array([2000, 4000], dtype=np.int16), 512).tobytes()
        callbacks[0](stereo, 512, None, None)
        mono = np.frombuffer(audio._segmenter.feed.call_args.args[0], dtype=np.int16)
        self.assertEqual(len(mono), 512)
        self.assertTrue(np.all(mono == 3000))

    def test_two_channel_usb_prefers_stereo_even_when_mono_would_open(self):
        audio = self.make_audio(device_name="USB interface")
        with patch.object(devices, "input_candidates", return_value=[(2, None)]), \
             patch("src.core.audio.sd.query_devices", return_value={"name": "USB interface", "max_input_channels": 2}), \
             patch("src.core.audio.sd.RawInputStream", return_value=Mock(active=True)) as constructor:
            audio.warm_up()
        self.assertEqual(constructor.call_args.kwargs["channels"], 2)

    def test_late_callback_after_close_cannot_feed_recording(self):
        audio = self.make_audio()
        with patch.object(devices, "input_candidates", return_value=[(2, None)]), \
             patch("src.core.audio.sd.query_devices", return_value={"name": "Mic", "max_input_channels": 1}), \
             patch("src.core.audio.sd.RawInputStream", return_value=Mock(active=True)) as constructor:
            audio.warm_up()
        callback = constructor.call_args.kwargs["callback"]
        audio.close_stream()
        audio._segmenter = Mock()
        audio._is_recording = True  # even an obsolete session flag cannot bypass generation
        callback(bytes(1024), 512, None, None)
        audio._segmenter.feed.assert_not_called()

    def test_dead_or_stalled_stream_requests_recovery(self):
        audio = self.make_audio()
        audio._is_stream_open = True
        audio._stream = Mock(active=True)
        audio._last_callback = time.monotonic()
        self.assertFalse(audio.needs_recovery)
        audio._last_callback -= 4
        self.assertTrue(audio.needs_recovery)
        audio._last_callback = time.monotonic()
        audio._stream.active = False
        self.assertTrue(audio.needs_recovery)

    def test_reopen_retries_after_initial_failure(self):
        audio = self.make_audio()
        with patch.object(devices, "refresh"), patch.object(audio, "_warm_up_locked") as warm:
            audio.reopen()
            warm.assert_called_once()

    def test_close_is_attempted_even_if_stop_fails(self):
        audio = self.make_audio()
        stream = Mock()
        stream.stop.side_effect = RuntimeError("removed USB")
        audio._stream = stream
        audio.close_stream()
        stream.close.assert_called_once()


class WhistleTests(unittest.TestCase):
    def make_engine(self):
        with patch.object(whistle, "_create_kill_on_close_job", return_value=None):
            engine = whistle.WhistleEngine(directory=Path("unused-whistle-test"))
        engine._is_ready = True
        return engine

    def test_success_returns_same_pipeline_contract_and_deletes_wav(self):
        engine = self.make_engine()
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps({"text": " Hello. ", "words": [
            {"word": "Hello.", "start": .1, "end": .7}]}), "")
        with patch.object(whistle.subprocess, "Popen", return_value=process) as spawn:
            result = engine.transcribe(bytes(16000))
            args = spawn.call_args.args[0]
            wav = Path(args[args.index("--audio") + 1])
            self.assertEqual(spawn.call_args.kwargs["env"]["NEEDLE_TELEMETRY"], "0")
            self.assertEqual(spawn.call_args.kwargs["env"]["DO_NOT_TRACK"], "1")
        self.assertEqual(result.text, "Hello.")
        self.assertTrue(result.timed)
        self.assertFalse(wav.exists())

    def test_crash_invalid_json_and_bad_timestamps_return_errors(self):
        for code, payload in [(1, ""), (0, "not JSON"), (0, '{"text":7}'),
                              (0, '{"text":"hi","words":[{"word":"hi","start":2,"end":1}]}')]:
            with self.subTest(code=code, payload=payload):
                engine = self.make_engine()
                process = Mock(returncode=code)
                process.communicate.return_value = (payload, "")
                with patch.object(whistle.subprocess, "Popen", return_value=process):
                    self.assertTrue(engine.transcribe(bytes(16000)).error)

    def test_timeout_kills_decoder_before_removing_audio(self):
        engine = self.make_engine()
        process = Mock(returncode=-1)
        process.poll.return_value = None
        process.communicate.side_effect = [subprocess.TimeoutExpired("needle", 60), ("", ""), ("", "")]
        with patch.object(whistle.subprocess, "Popen", return_value=process) as spawn:
            self.assertTrue(engine.transcribe(bytes(16000)).error)
        process.kill.assert_called()
        args = spawn.call_args.args[0]
        self.assertFalse(Path(args[args.index("--audio") + 1]).exists())

    def test_invalid_audio_and_long_clip_do_not_spawn(self):
        engine = self.make_engine()
        with patch.object(whistle.subprocess, "Popen") as spawn:
            self.assertFalse(engine.transcribe(b"").error)
            self.assertTrue(engine.transcribe(b"x").error)
            self.assertTrue(engine.transcribe(bytes(16000), 48000).error)
            self.assertTrue(engine.transcribe(bytes(31 * 32000)).error)
            spawn.assert_not_called()

    def test_stop_request_keeps_final_decode_but_prevents_restart(self):
        engine = self.make_engine()
        engine.request_stop()
        with self.assertRaises(RuntimeError):
            engine.start_server()
        process = Mock(returncode=0)
        process.communicate.return_value = ('{"text":"Final words."}', "")
        with patch.object(whistle.subprocess, "Popen", return_value=process):
            self.assertEqual(engine.transcribe(bytes(16000)).text, "Final words.")
        engine.stop_server()
        self.assertTrue(engine.transcribe(bytes(16000)).error)

    def test_checksum_failure_never_publishes_runtime_or_leaves_partial(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            digest = hashlib.sha256(b"good").hexdigest()
            with patch.object(whistle, "ASSETS", {"needle.exe": ("https://example.invalid", digest)}), \
                 patch.object(whistle, "ROOT", path / "absent"), \
                 patch.object(whistle.urllib.request, "urlopen", return_value=io.BytesIO(b"bad")):
                with self.assertRaisesRegex(ValueError, "checksum"):
                    whistle.ensure_assets(path, threading.Event())
            self.assertEqual(list(path.iterdir()), [])

    def test_verified_cached_files_work_without_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / "needle.exe").write_bytes(b"good")
            digest = hashlib.sha256(b"good").hexdigest()
            with patch.object(whistle, "ASSETS", {"needle.exe": ("unused", digest)}), \
                 patch.object(whistle.urllib.request, "urlopen") as download:
                whistle.ensure_assets(path, threading.Event())
                download.assert_not_called()

    def test_cancelled_download_does_not_create_or_publish_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            cancel = threading.Event()
            cancel.set()
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                whistle.ensure_assets(Path(temporary), cancel)
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_failed_startup_probe_never_marks_engine_ready(self):
        engine = self.make_engine()
        engine._is_ready = False
        states = []
        engine.on_state_change = lambda ready, message: states.append(ready)
        with patch.object(whistle, "ensure_assets"), \
             patch.object(engine, "_decode", return_value=TranscribeResult(error=True)):
            with self.assertRaisesRegex(RuntimeError, "startup"):
                engine.start_server()
        self.assertFalse(engine._is_ready)
        self.assertNotIn(True, states)

    def test_oversized_stitch_falls_back_to_new_phrase(self):
        engine = FakeEngine()
        engine.max_audio_seconds = 30.0
        engine.queue.extend([TranscribeResult(text="Previous."), TranscribeResult(text="New phrase.")])
        pipe, typed, _, _, _ = make_pipeline(engine)
        pipe.process(make_utt(dur=8.0))
        pipe.process(make_utt(pcm=bytes(25 * 32000), start=7.8, dur=25.0))
        self.assertEqual(len(engine.calls), 2)
        self.assertLess(engine.calls[-1], 30)


class SettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def make_window(self, mic=None):
        config = DEFAULT_CONFIG.copy()
        config["mic_name"] = mic
        with patch.object(devices, "list_microphones", return_value=["Internal Mic"]), \
             patch("src.ui.dashboard.get_autostart_registry", return_value=False):
            history = Mock()
            history.get_all.return_value = []
            window = DashboardWindow(config, history)
        self.addCleanup(window.close)
        return window

    def test_whistle_can_be_selected_and_saved(self):
        window = self.make_window()
        changes = []
        window.settings_saved.connect(lambda config: changes.append(config.copy()))
        window.engine_combo.setCurrentIndex(window.engine_combo.findData("whistle"))
        self.assertEqual(changes[-1]["speech_engine"], "whistle")

    def test_live_preview_and_quote_preferences_are_saved(self):
        window = self.make_window()
        changes = []
        window.settings_saved.connect(lambda config: changes.append(config.copy()))
        self.assertTrue(window.live_check.isChecked())
        self.assertTrue(window.preview_check.isChecked())
        self.assertTrue(window.quotes_check.isChecked())
        window.live_check.setChecked(False)
        window.preview_check.setChecked(True)
        window.quotes_check.setChecked(False)
        self.assertFalse(changes[-1]['live_typing'])
        self.assertTrue(changes[-1]['show_preview'])
        self.assertFalse(changes[-1]['strip_outer_quotes'])

    def test_unplugged_usb_is_marked_and_disabled(self):
        window = self.make_window("USB Mic")
        index = window.mic_combo.findData("USB Mic")
        self.assertIn("not connected", window.mic_combo.itemText(index))
        self.assertFalse(window.mic_combo.model().item(index).isEnabled())

    def test_mic_test_uses_app_stream_and_handles_missing_input(self):
        window = self.make_window()
        window.capture_engine = Mock(needs_recovery=True)
        window._toggle_mic_test()
        self.assertFalse(window.mic_testing)
        window.capture_engine.needs_recovery = False
        window._toggle_mic_test()
        self.assertTrue(window.mic_testing)
        self.assertIsNone(window.test_audio_engine)
        window._toggle_mic_test()
        window.capture_engine.start.assert_not_called()

    def test_enumeration_failure_does_not_crash_settings(self):
        window = self.make_window("USB Mic")
        with patch.object(devices, "list_microphones", side_effect=RuntimeError("driver failed")):
            window.populate_mics()
        self.assertIn("not connected", window.mic_combo.currentText())
