"""Windows integration regressions. Real Qt event loop; simulated audio, HTTP and Win32 input.

These tests never install a global hook, type into another app, or use the user's microphone.
"""
import ctypes
import logging
import json
import os
import queue
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

# Import the orchestrator without creating the user's persistent log.
with patch("logging.handlers.RotatingFileHandler", side_effect=OSError("test logging disabled")):
    from src import main as APP
from src.config import DEFAULT_CONFIG
from src.core.audio import AudioCaptureEngine
from src.core.engine import PhononEngine, TranscribeResult
from src.core.hotkey import (GlobalHotkeyManager, KBDLLHOOKSTRUCT, LLKHF_INJECTED,
                             WM_KEYDOWN, WM_KEYUP, parse_hotkey)
from src.core import injector as INJ
from src.core.pipeline import DictationPipeline
from src.core.vocabulary import VocabularyEngine
from src.ui.dashboard import HotkeyRecorderButton
from tools.benchmark_whistle import word_error_rate
from test_core import FakeEngine, ScriptedVAD, frames, make_pipeline, make_utt, words

logging.getLogger().setLevel(logging.WARNING)


class PipelineFocusTests(unittest.TestCase):
    def test_new_window_gets_no_previous_context_or_punctuation(self):
        eng = FakeEngine()
        eng.queue.extend([TranscribeResult(text="First window."), TranscribeResult(text="Second window.")])
        pipe, typed, _, _, hwnd = make_pipeline(eng)
        pipe.process(make_utt())
        hwnd['v'] = 2
        pipe.process(make_utt(start=2.5))
        self.assertEqual(typed, ["First window", "Second window"])
        self.assertEqual(len(eng.calls), 2)

    def test_queued_phrase_never_types_into_a_different_window(self):
        eng = FakeEngine()
        eng.queue.append(TranscribeResult(text='Recover this complete phrase.'))
        events = []
        pipe, typed, _, _, hwnd = make_pipeline(eng, on_event=lambda k,m: events.append(k))
        utt = make_utt()
        utt.target_hwnd = 1
        hwnd['v'] = 2
        pipe.process(utt)
        self.assertEqual(typed, [])
        self.assertEqual(len(eng.calls), 1)
        self.assertEqual(pipe.recovery.entries()[0].text, 'Recover this complete phrase.')
        self.assertEqual(events, ['focus'])

    def test_focus_changed_while_engine_was_decoding(self):
        events = []
        eng = Mock()
        pipe, typed, _, _, hwnd = make_pipeline(eng, on_event=lambda k,m: events.append(k))
        def decode(pcm):
            hwnd['v'] = 2
            return TranscribeResult(text="Must not leak.")
        eng.transcribe.side_effect = decode
        pipe.process(make_utt())
        self.assertEqual(typed, [])
        self.assertIsNone(pipe._prev)
        self.assertEqual(events, ['focus'])

    def test_no_foreground_window_drops_deferred_punctuation(self):
        eng = FakeEngine()
        eng.queue.append(TranscribeResult(text="Hello world."))
        pipe, typed, _, _, hwnd = make_pipeline(eng)
        pipe.process(make_utt())
        hwnd['v'] = 0
        pipe.flush()
        self.assertEqual(typed, ['Hello world'])

    def test_failed_injection_never_creates_context_or_history(self):
        eng = FakeEngine()
        eng.queue.append(TranscribeResult(text="Hello world."))
        history = Mock()
        pipe, typed, *_ = make_pipeline(eng, history=history)
        pipe.inject = Mock(return_value=False)
        pipe.process(make_utt())
        pipe.flush()
        self.assertIsNone(pipe._prev)
        history.add_entry.assert_not_called()
        self.assertEqual(pipe.inject.call_count, 1)

    def test_original_hwnd_is_passed_to_guarded_injector(self):
        eng = FakeEngine()
        eng.queue.append(TranscribeResult(text="Hello world."))
        guarded = Mock(return_value=True)
        pipe, typed, *_ = make_pipeline(eng, inject_target=guarded)
        pipe.process(make_utt())
        pipe.flush()
        self.assertEqual(guarded.call_args_list[0].args, ('Hello world', 1))
        self.assertEqual(guarded.call_args_list[1].args, ('. ', 1))
        self.assertEqual(typed, [])

    def test_stitch_context_mismatch_falls_back(self):
        eng = FakeEngine()
        eng.queue.extend([TranscribeResult(text='Hello world.'),
                          TranscribeResult(text='wrong context', words=words('different thing,', per=.2) + words('new phrase.', t0=2.4)),
                          TranscribeResult(text='Fresh phrase.')])
        pipe, typed, *_ = make_pipeline(eng)
        pipe.process(make_utt())
        pipe.process(make_utt(start=2.5))
        self.assertEqual(typed, ['Hello world', '. Fresh phrase'])
        self.assertEqual(len(eng.calls), 3)

    def test_speculation_invalidated_when_prior_context_flushed(self):
        eng = FakeEngine()
        eng.queue.extend([TranscribeResult(text='Hello world.'),
                          TranscribeResult(text='all', words=words('Hello world,', per=.2) + words('next phrase.', t0=2.4)),
                          TranscribeResult(text='Next phrase.')])
        pipe, typed, *_ = make_pipeline(eng)
        pipe.process(make_utt())
        utt = make_utt(start=2.5)
        utt.spec_id = 1
        pipe.process_speculative(utt, 1)
        pipe.flush()
        pipe.process(utt)
        self.assertEqual(typed, ['Hello world', '. ', 'Next phrase'])
        self.assertEqual(len(eng.calls), 3)

    def test_multiple_cancellations_invalidate_all_older_decodes(self):
        eng = FakeEngine()
        pipe, *_ = make_pipeline(eng)
        pipe.cancel_speculative(1)
        pipe.cancel_speculative(2)
        pipe.process_speculative(make_utt(), 1)
        self.assertEqual(eng.calls, [])

    def test_cancel_during_inflight_speculation_never_publishes(self):
        started, release = threading.Event(), threading.Event()
        eng = Mock()
        def decode(pcm):
            started.set()
            self.assertTrue(release.wait(2))
            return TranscribeResult(text='Old preview.')
        eng.transcribe.side_effect = decode
        previews = []
        pipe, *_ = make_pipeline(eng, on_preview=previews.append)
        worker = threading.Thread(target=pipe.process_speculative, args=(make_utt(), 1))
        worker.start()
        self.assertTrue(started.wait(2))
        pipe.cancel_speculative(1)
        pipe.cancel_speculative(2)
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertNotIn('Old preview.', previews)
        self.assertIsNone(pipe._spec_match)


class HotkeyTests(unittest.TestCase):
    def manager(self, name='ctrl+space', ptt=False):
        events = []
        mgr = GlobalHotkeyManager(name, ptt, on_start=lambda: events.append('start'),
                                  on_stop=lambda: events.append('stop'))
        return mgr, events

    def press(self, mgr, vk):
        return mgr._handle_key(vk, True, False)

    def release(self, mgr, vk):
        return mgr._handle_key(vk, False, True)

    def test_recorder_combinations_are_parsed(self):
        for key in ['ctrl+shift+a', 'alt+f12', 'ctrl+alt+7', 'win+space', 'f1', 'f24', 'pause', 'ralt', 'capslock']:
            with self.subTest(key=key):
                modifiers, vk = parse_hotkey(key)
                self.assertIsInstance(vk, int)

    def test_invalid_hotkeys_never_silently_become_capslock(self):
        for key in ['ctrl+?', 'ctrl+ctrl+a', 'control+a', '', 'f25', 'shift', 'ctrl+space+x']:
            with self.subTest(key=key), self.assertRaises(ValueError):
                parse_hotkey(key)

    def test_toggle_autorepeat_suppressed_and_keyup_consumed(self):
        mgr, events = self.manager()
        self.press(mgr, 0xA2)
        for _ in range(100):
            self.assertTrue(self.press(mgr, 0x20))
        self.assertTrue(self.release(mgr, 0x20))
        self.press(mgr, 0x20)
        self.assertEqual(events, ['start', 'stop'])

    def test_ctrl_space_push_to_talk(self):
        mgr, events = self.manager(ptt=True)
        self.press(mgr, 0xA3)
        self.press(mgr, 0x20)
        self.press(mgr, 0x20)
        self.release(mgr, 0x20)
        self.assertEqual(events, ['start', 'stop'])

    def test_ptt_modifier_released_first_stops_once(self):
        mgr, events = self.manager(ptt=True)
        self.press(mgr, 0xA2)
        self.press(mgr, 0x20)
        self.release(mgr, 0xA2)
        self.assertTrue(self.press(mgr, 0x20))
        self.assertTrue(self.release(mgr, 0x20))
        self.assertEqual(events, ['start', 'stop'])

    def test_releasing_one_ctrl_does_not_release_other_ctrl(self):
        mgr, events = self.manager(ptt=True)
        self.press(mgr, 0xA2)
        self.press(mgr, 0xA3)
        self.press(mgr, 0x20)
        self.release(mgr, 0xA2)
        self.assertEqual(events, ['start'])
        self.release(mgr, 0xA3)
        self.assertEqual(events, ['start', 'stop'])

    def test_extra_modifiers_do_not_trigger_wrong_shortcut(self):
        mgr, events = self.manager('ctrl+a')
        self.press(mgr, 0xA2)
        self.press(mgr, 0xA0)
        self.assertFalse(self.press(mgr, 0x41))
        self.assertEqual(events, [])

    def test_rebind_and_mode_change_release_ptt(self):
        for change in [lambda m: m.set_trigger_key('f9'), lambda m: m.set_mode(False)]:
            with self.subTest(change=change):
                mgr, events = self.manager('f8', True)
                self.press(mgr, 0x77)
                change(mgr)
                self.assertEqual(events, ['start', 'stop'])
                self.assertTrue(self.release(mgr, 0x77))

    def test_invalid_rebind_leaves_valid_binding(self):
        mgr, _ = self.manager()
        with self.assertRaises(ValueError):
            mgr.set_trigger_key('ctrl+?')
        self.assertEqual(mgr.trigger_vk, 0x20)

    def test_injected_keys_never_trigger_dictation(self):
        mgr, events = self.manager('a')
        kb = KBDLLHOOKSTRUCT(vkCode=0x41, flags=LLKHF_INJECTED)
        with patch.object(INJ.user32, 'CallNextHookEx', return_value=0):
            self.assertEqual(mgr._hook_callback(0, WM_KEYDOWN, ctypes.addressof(kb)), 0)
        self.assertEqual(events, [])

    def test_multiple_fast_toggles_dispatch_in_order(self):
        events = []
        mgr = GlobalHotkeyManager('f8', on_toggle=lambda: events.append('toggle'), is_active=lambda: False)
        for _ in range(20):
            self.press(mgr, 0x77)
            self.release(mgr, 0x77)
        self.assertEqual(events, ['toggle'] * 20)


class InjectionTests(unittest.TestCase):
    def test_focus_mismatch_does_not_touch_keyboard_or_clipboard(self):
        with patch.object(INJ, 'get_foreground_hwnd', return_value=2), \
             patch.object(INJ, 'type_via_clipboard') as paste, patch.object(INJ, 'type_unicode_chars') as type_text:
            self.assertFalse(INJ.inject_text('private text', 'paste', expected_hwnd=1))
            paste.assert_not_called()
            type_text.assert_not_called()

    def test_focus_rechecked_between_keyboard_batches(self):
        with patch.object(INJ, 'get_foreground_hwnd', side_effect=[1, 2]), \
             patch.object(INJ.user32, 'SendInput', side_effect=lambda n,*args: n) as send:
            self.assertFalse(INJ.type_unicode_chars('a' * 300, expected_hwnd=1))
            self.assertEqual(send.call_count, 1)

    def test_clipboard_restored_when_focus_changes_before_paste(self):
        clipboard = ['original']
        def set_text(text):
            clipboard[0] = text
            return True
        with patch.object(INJ, 'get_foreground_hwnd', side_effect=[1,2]), \
             patch.object(INJ, '_clipboard_has_non_text', return_value=False), \
             patch.object(INJ, 'get_clipboard_text', side_effect=lambda: clipboard[0]), \
             patch.object(INJ, 'set_clipboard_text', side_effect=set_text), \
             patch.object(INJ, '_simulate_ctrl_v') as paste:
            self.assertFalse(INJ.type_via_clipboard('private text', expected_hwnd=1))
            self.assertEqual(clipboard, ['original'])
            paste.assert_not_called()

    def test_sendinput_failure_is_reported(self):
        with patch.object(INJ.user32, 'SendInput', return_value=0):
            self.assertFalse(INJ.type_unicode_chars('hello'))
            self.assertFalse(INJ._simulate_ctrl_v())

    def test_clipboard_restored_even_when_paste_raises(self):
        clipboard = ['original']
        def set_text(text):
            clipboard[0] = text
            return True
        with patch.object(INJ, '_clipboard_has_non_text', return_value=False), \
             patch.object(INJ, 'get_clipboard_text', side_effect=lambda: clipboard[0]), \
             patch.object(INJ, 'set_clipboard_text', side_effect=set_text), \
             patch.object(INJ, '_simulate_ctrl_v', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                INJ.type_via_clipboard('private text')
            self.assertEqual(clipboard, ['original'])

    def test_emoji_has_surrogate_pairs_and_input_size_is_64_bit_safe(self):
        self.assertEqual(ctypes.sizeof(INJ.INPUT), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        self.assertEqual(len(INJ._text_to_key_events('😀')), 4)


class SessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.app = APP.DictationApp.__new__(APP.DictationApp)
        QObject.__init__(self.app)
        a = self.app
        a.q_app = Mock()
        a.bridge = APP.AppBridge()
        a.hud = Mock()
        a.sounds = Mock()
        a.audio = Mock()
        a.audio.needs_recovery = False
        a.audio.fallback_reason = ""
        a.engine = Mock()
        a.engine._is_ready = True
        a.pipeline = Mock()
        a._is_dictating = False
        a._is_paused = False
        a._shutting_down = False
        a._device_refreshing = False
        a._device_lock = threading.Lock()
        a._engine_switching = False
        a._engine_selection_generation = 0
        a._applied_engine = "phonon"
        a.config = DEFAULT_CONFIG.copy()
        a.config['show_preview'] = True
        a._live_stream = None
        a._stream_utt = None
        a._live_queue_lock = threading.Lock()
        a._live_pending = {}
        a._live_results = {}
        a._live_closed_id = 0
        a._live_failed_id = None
        a.dashboard = None
        a._session_generation = a._worker_generation = 0
        a._utterance_hwnd = 1
        a._transcribe_queue = queue.Queue()
        a.hotkey = Mock()
        a.bridge.sig_start_requested.connect(a._start_dictation, Qt.ConnectionType.QueuedConnection)
        a.bridge.sig_stop_requested.connect(a._stop_dictation, Qt.ConnectionType.QueuedConnection)
        a.bridge.sig_toggle_requested.connect(a._toggle_dictation, Qt.ConnectionType.QueuedConnection)
        a.bridge.sig_preview.connect(a._on_preview)

    def drain(self):
        self.app._transcribe_queue.put(None)
        self.app._transcribe_worker()

    def test_stop_retains_final_utterance_before_flush(self):
        a = self.app
        a._start_dictation()
        utt = make_utt()
        a.audio.stop.side_effect = lambda: a._on_utterance_ready(utt)
        a._stop_dictation()
        self.drain()
        self.assertEqual([c[0] for c in a.pipeline.method_calls], ['reset', 'process', 'flush'])
        self.assertIs(a.pipeline.process.call_args.args[0], utt)

    def test_pause_stops_capture_and_resume_starts_fresh_context(self):
        a = self.app
        a._start_dictation()
        utt = make_utt()
        a.audio.stop.side_effect = lambda: a._on_utterance_ready(utt)
        a._toggle_pause()
        a._on_utterance_ready(make_utt())  # callbacks while paused are discarded
        a._toggle_pause()
        self.drain()
        self.assertEqual([c[0] for c in a.pipeline.method_calls], ['reset', 'process', 'flush', 'reset'])
        self.assertEqual(a.audio.start.call_count, 2)

    def test_stop_and_immediate_restart_keep_fifo_session_boundaries(self):
        a = self.app
        a._start_dictation()
        a._on_utterance_ready(make_utt())
        a._stop_dictation()
        a._start_dictation()
        a._on_utterance_ready(make_utt())
        self.drain()
        self.assertEqual([c[0] for c in a.pipeline.method_calls], ['reset', 'process', 'flush', 'reset', 'process'])
        self.assertEqual(a._worker_generation, 2)

    def test_microphone_start_failure_rolls_back_state(self):
        a = self.app
        a.audio.start.side_effect = RuntimeError('no mic')
        a._start_dictation()
        self.assertFalse(a._is_dictating)
        a.sounds.play_start.assert_not_called()
        a.audio.start.side_effect = None
        a._start_dictation()
        self.assertTrue(a._is_dictating)

    def test_failed_resume_remains_paused_until_microphone_recovers(self):
        a = self.app
        a._start_dictation()
        a._toggle_pause()
        a.audio.start.side_effect = RuntimeError('missing mic')
        a._toggle_pause()
        self.assertTrue(a._is_paused)
        self.assertTrue(a._is_dictating)
        a.audio.start.side_effect = None
        a._toggle_pause()
        self.assertFalse(a._is_paused)

    def test_device_refresh_blocks_session_start(self):
        a = self.app
        a._device_refreshing = True
        a._start_dictation()
        self.assertFalse(a._is_dictating)
        a.audio.start.assert_not_called()

    def test_failed_capture_triggers_background_recovery_before_start(self):
        a = self.app
        a.audio.needs_recovery = True
        with patch.object(a, '_refresh_audio_devices') as refresh:
            a._start_dictation()
        refresh.assert_called_once_with(force=True)
        a.audio.start.assert_not_called()

    def test_engine_switch_drains_final_phrase_before_stopping_old_engine(self):
        a = self.app
        order = []
        old = a.engine
        old.stop_server.side_effect = lambda: order.append('stop old')
        a.pipeline.process.side_effect = lambda u: order.append('phrase')
        a.pipeline.flush.side_effect = lambda r: order.append('punctuation')
        a._start_dictation()
        a.audio.stop.side_effect = lambda: a._on_utterance_ready(make_utt())
        a.config['speech_engine'] = 'whistle'
        a._queue_engine_change()
        a._start_dictation()
        self.assertFalse(a._is_dictating)
        replacement = Mock()
        with patch.object(a, '_create_engine', return_value=replacement), \
             patch.object(a, '_boot_engine') as boot:
            self.drain()
        self.assertEqual(order, ['phrase', 'punctuation', 'stop old'])
        self.assertIs(a.pipeline.engine, replacement)
        boot.assert_called_once_with(replacement)

    def test_rapid_engine_selections_only_load_latest(self):
        a = self.app
        a.config['speech_engine'] = 'whistle'
        a._queue_engine_change()
        a.config['speech_engine'] = 'phonon'
        a._queue_engine_change()
        a._on_engine_changed(1)
        self.assertTrue(a._engine_switching)
        with patch.object(a, '_create_engine', return_value=Mock()) as create, \
             patch.object(a, '_boot_engine'):
            self.drain()
        create.assert_called_once()
        self.assertEqual(create.call_args.args[0]['speech_engine'], 'phonon')
        a._on_engine_changed(2)
        self.assertFalse(a._engine_switching)

    def test_late_state_from_replaced_engine_is_ignored(self):
        a = self.app
        a.tray = Mock()
        replaced = Mock()
        a._on_engine_state(replaced, True, 'old engine ready')
        a.tray.set_status.assert_not_called()
        a.hud.set_engine_ready.assert_not_called()

    def test_previews_do_not_open_an_unbounded_native_stream(self):
        a = self.app
        a._worker_generation = 1
        a.engine.supports_live_stream = True
        stream = a.engine.open_live_stream.return_value
        result = TranscribeResult(text='Direct field text.')
        stream.finish.return_value = result
        utt = make_utt()
        utt.utterance_id = 1
        a._process_live_snapshot(utt)
        a._transcribe_queue.put(utt)
        self.drain()
        a.engine.open_live_stream.assert_not_called()
        a.engine.transcribe.assert_called_once_with(utt.pcm)
        a.pipeline.process.assert_called_once_with(utt)

    def test_preview_cadence_skips_stale_work_and_keeps_final_path_free(self):
        a = self.app
        a._worker_generation = 1
        a.engine.supports_live_stream = True
        a.engine.open_live_stream.side_effect = RuntimeError('older server')
        a.engine.transcribe.return_value = TranscribeResult(text='Fallback words.')
        utt = make_utt()
        utt.utterance_id = 1
        a._process_live_snapshot(utt)
        a._process_live_snapshot(utt)
        a.engine.open_live_stream.assert_not_called()
        self.assertEqual(a.engine.transcribe.call_count, 1)
        self.assertEqual(a.pipeline.process_live.call_count, 1)

    def test_orb_preview_is_hidden_by_default(self):
        a = self.app
        a.config['show_preview'] = False
        a._is_dictating = True
        a._on_preview(0, 'A hypothesis under the orb')
        a.hud.set_preview_text.assert_not_called()

    def test_stalled_mic_recovers_even_when_device_list_is_unchanged(self):
        a = self.app
        a.audio.needs_recovery = True
        with patch.object(APP.devices, 'fresh_scan', return_value=(['Mic'], 'Mic')), \
             patch.object(APP.devices, 'list_microphones', return_value=['Mic']), \
             patch.object(APP.devices, 'default_microphone_name', return_value='Mic'):
            a._refresh_devices_worker(False)
        a.audio.set_device.assert_called_once_with(None)

    def test_broken_cached_enumeration_still_attempts_recovery(self):
        a = self.app
        with patch.object(APP.devices, 'fresh_scan', return_value=(['Mic'], 'Mic')), \
             patch.object(APP.devices, 'list_microphones', side_effect=RuntimeError('PortAudio lost')):
            a._refresh_devices_worker(False)
        a.audio.set_device.assert_called_once_with(None)

    def test_unplug_during_dictation_hands_stop_to_qt_before_reopen(self):
        a = self.app
        a._start_dictation()
        handoff = []
        a.bridge.sig_refresh_required.connect(lambda: handoff.append(True))
        with patch.object(APP.devices, 'fresh_scan', return_value=([], None)), \
             patch.object(APP.devices, 'list_microphones', return_value=['USB']), \
             patch.object(APP.devices, 'default_microphone_name', return_value='USB'):
            a._refresh_devices_worker(False)
        self.assertEqual(handoff, [True])
        a.audio.set_device.assert_not_called()

    def test_microphone_open_error_reports_notice_and_releases_guard(self):
        a = self.app
        a._device_refreshing = True
        a.audio.set_device.side_effect = RuntimeError('USB removed')
        notices = []
        a.bridge.sig_notice.connect(lambda title, message: notices.append(message))
        a.bridge.sig_devices_changed.connect(a._on_devices_changed)
        a._refresh_devices_worker(True)
        self.assertFalse(a._device_refreshing)
        self.assertIn('USB removed', notices[0])

    def test_speech_target_captured_at_onset_not_end(self):
        a = self.app
        a._start_dictation()
        with patch.object(APP, 'get_foreground_hwnd', return_value=123):
            a._on_speech_activity(True)
        with patch.object(APP, 'get_foreground_hwnd', return_value=456):
            utt = make_utt()
            a._on_utterance_ready(utt)
        self.assertEqual(utt.target_hwnd, 123)

    def test_constructor_wires_queued_hotkeys_and_clean_shutdown(self):
        with patch.object(APP, 'load_config', return_value=DEFAULT_CONFIG.copy()), \
             patch.object(APP, 'HistoryManager'), patch.object(APP, 'CircularOrbHUD'), \
             patch.object(APP, 'SystemTray'), patch.object(APP, 'SoundEffects'), \
             patch.object(APP, 'AudioCaptureEngine'), patch.object(APP.devices, 'add_stream_closer'), \
             patch.object(APP.DictationApp, '_init_services'):
            a = APP.DictationApp(self.qt)
            a.audio.needs_recovery = False
            a.audio.fallback_reason = ""
            a.engine._is_ready = True
            a.hotkey._handle_key(0xA2, True, False)
            a.hotkey._handle_key(0x20, True, False)
            a.hotkey._handle_key(0x20, False, True)
            self.assertFalse(a._is_dictating) # queued, not executed on the hook thread
            self.qt.processEvents()
            self.assertTrue(a._is_dictating)
            a.shutdown()
            a._worker_thread.join(2)
            self.assertFalse(a._worker_thread.is_alive())

    def test_device_scan_error_releases_refresh_guard(self):
        a = self.app
        a._device_lock = threading.Lock()
        a._device_refreshing = True
        completed = []
        a.bridge.sig_devices_changed.connect(lambda: completed.append(True))
        with patch.object(APP.devices, 'fresh_scan', side_effect=RuntimeError('scan failed')):
            a._refresh_devices_worker(False)
        self.assertEqual(completed, [True])
        a._on_devices_changed()
        self.assertFalse(a._device_refreshing)

    def test_old_preview_does_not_reappear_after_restart_or_pause(self):
        a = self.app
        a._start_dictation()
        a._stop_dictation()
        a._start_dictation()
        a.hud.set_preview_text.reset_mock()
        a._on_preview(1, 'stale')
        a._on_preview(2, 'current')
        a.hud.set_preview_text.assert_called_once_with('current')
        a._toggle_pause()
        a.hud.set_preview_text.reset_mock()
        a._on_preview(2, 'paused')
        a.hud.set_preview_text.assert_not_called()

    def test_background_hotkeys_execute_on_qt_thread_in_order(self):
        a = self.app
        threads = []
        a.audio.start.side_effect = lambda: threads.append(threading.get_ident())
        def toggle():
            for _ in range(20):
                a.bridge.sig_toggle_requested.emit()
        worker = threading.Thread(target=toggle)
        worker.start()
        worker.join(2)
        self.qt.processEvents()
        self.assertFalse(a._is_dictating)
        self.assertEqual(threads, [threading.get_ident()] * 10)
        self.assertEqual(a.audio.stop.call_count, 10)

    def test_shutdown_finishes_final_audio_before_stopping_server(self):
        a = self.app
        order = []
        a.pipeline.process.side_effect = lambda u: order.append('phrase')
        a.pipeline.flush.side_effect = lambda r: order.append('punctuation')
        a.engine.stop_server.side_effect = lambda: order.append('server stop')
        a._start_dictation()
        a.audio.stop.side_effect = lambda: a._on_utterance_ready(make_utt())
        a.shutdown()
        a.shutdown()
        a._start_dictation()  # cannot start while closing
        a._transcribe_worker()
        self.assertEqual(order, ['phrase', 'punctuation', 'server stop'])
        self.assertEqual(a.audio.start.call_count, 1)

    def test_real_audio_segmenter_flush_reaches_real_pipeline(self):
        a = self.app
        typed = []
        eng = FakeEngine()
        eng.queue.append(TranscribeResult(text='Keep my final words.'))
        a.pipeline = DictationPipeline(eng, VocabularyEngine({}), inject=lambda t: typed.append(t) or True,
                                      get_hwnd=lambda: 1)
        vad = ScriptedVAD()
        with patch('src.core.audio.create_vad', return_value=vad):
            a.audio = AudioCaptureEngine(on_utterance=a._on_utterance_ready,
                                         on_activity=a._on_speech_activity, voice_gate=False)
        a.audio._is_stream_open = True
        a.audio._stream = Mock(active=True)
        a.audio._last_callback = time.monotonic()
        self.addCleanup(a.audio.close_stream)
        with patch.object(APP, 'get_foreground_hwnd', return_value=1):
            a._start_dictation()
            # No pause frames: this phrase can only be emitted by the stop flush.
            a.audio._segmenter.feed(frames(20, 3000))
            a._stop_dictation()
        self.drain()
        self.assertEqual(''.join(typed), 'Keep my final words. ')

    def test_recorder_accepts_ctrl_shift_letter_and_rejects_symbols(self):
        button = HotkeyRecorderButton()
        received = []
        button.hotkey_recorded.connect(received.append)
        button._start_recording()
        button.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_A,
                                      Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, 'A'))
        self.assertEqual(received, ['ctrl+shift+a'])
        button._start_recording()
        button.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_1,
                                      Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, '!'))
        self.assertEqual(received, ['ctrl+shift+a', 'ctrl+shift+1'])
        button._start_recording()
        button.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Question,
                                      Qt.KeyboardModifier.NoModifier, '?'))
        self.assertEqual(received, ['ctrl+shift+a', 'ctrl+shift+1'])
        self.assertTrue(button.is_recording)


class AudioLifecycleTests(unittest.TestCase):
    def make_audio(self):
        with patch('src.core.audio.create_vad', return_value=ScriptedVAD()):
            return AudioCaptureEngine()

    def test_failed_stream_candidate_is_closed_before_fallback(self):
        audio = self.make_audio()
        failed, working = Mock(), Mock()
        failed.start.side_effect = RuntimeError('unsupported format')
        with patch('src.core.audio.devices.input_candidates', return_value=[(1, None), (2, None)]), \
             patch('src.core.audio.sd.RawInputStream', side_effect=[failed, working]), \
             patch('src.core.audio.sd.query_devices', return_value={'name': 'working mic'}):
            audio.warm_up()
        failed.close.assert_called_once()
        self.assertIs(audio._stream, working)
        audio.close_stream()
        working.close.assert_called_once()

    def test_closed_capture_cannot_be_reopened_by_late_startup_thread(self):
        audio = self.make_audio()
        audio.close_stream()
        with patch('src.core.audio.devices.refresh') as refresh:
            audio.reopen()
            refresh.assert_not_called()
        with self.assertRaises(RuntimeError):
            audio.warm_up()

    def test_silent_launcher_uses_console_python_for_captured_scan_output(self):
        with patch.object(APP.devices.sys, 'executable', r'C:\fake\pythonw.exe'), \
             patch.object(APP.devices.os.path, 'exists', return_value=True), \
             patch.object(APP.devices.subprocess, 'run', return_value=Mock(stdout='[["USB mic"], "USB mic"]')) as run:
            self.assertEqual(APP.devices.fresh_scan(), (['USB mic'], 'USB mic'))
            self.assertEqual(run.call_args.args[0][0], r'C:\fake\python.exe')


class HttpTransportTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.status = 200
        self.payload = {'text': 'Hello world.', 'words': [{'word': 'Hello', 'start': .1, 'end': .5}]}
        owner = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args):
                pass
            def do_POST(self):
                audio = self.rfile.read(int(self.headers['Content-Length']))
                owner.requests.append((self.client_address, audio))
                data = json.dumps(owner.payload).encode()
                self.send_response(owner.status)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.engine = PhononEngine(port=self.server.server_port)

    def tearDown(self):
        conn = getattr(self.engine._tls, 'conn', None)
        if conn:
            conn.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def test_real_local_http_reuses_connection_and_sends_wav(self):
        a = self.engine.transcribe(b'\x00\x00' * 16000)
        b = self.engine.transcribe(b'\x00\x00' * 8000)
        self.assertFalse(a.error)
        self.assertFalse(b.error)
        self.assertEqual(a.words[0]['start'], .1)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[0][0], self.requests[1][0])
        self.assertIn(b'RIFF', self.requests[0][1])
        self.assertIn(b'WAVE', self.requests[0][1])

    def test_http_failure_is_not_silently_empty_and_not_retried(self):
        self.status = 503
        result = self.engine.transcribe(b'\x00\x00' * 16000)
        self.assertTrue(result.error)
        self.assertEqual(len(self.requests), 1)

    def test_empty_transcript_is_a_success(self):
        self.payload = {'text': '', 'words': []}
        result = self.engine.transcribe(b'\x00\x00' * 16000)
        self.assertFalse(result.error)
        self.assertEqual(result.text, '')


class ProbeScoringTests(unittest.TestCase):
    def test_reference_edit_rate(self):
        self.assertEqual(word_error_rate('Hello, WORLD.', 'hello world'), 0)
        self.assertEqual(word_error_rate('one two three four', 'one two wrong four'), .25)
        self.assertEqual(word_error_rate('one two', 'one'), .5)
        self.assertEqual(word_error_rate('one two', 'one two three'), .5)

    def test_empty_reference_cannot_report_a_misleading_zero(self):
        with self.assertRaises(ValueError):
            word_error_rate('', 'text')


class EngineRecoveryTests(unittest.TestCase):
    def test_watchdog_retries_failed_restart_and_recovers(self):
        engine = PhononEngine()
        recovered = threading.Event()
        calls = []
        def restart(**kwargs):
            calls.append('start')
            if len(calls) == 1:
                raise RuntimeError('temporary failure')
            engine._is_ready = True
            recovered.set()
        with patch.object(engine, '_terminate_process'), patch.object(engine, 'start_server', side_effect=restart), \
             patch.object(engine, 'is_server_running', return_value=True):
            engine.start_watchdog(interval=.01)
            self.assertTrue(recovered.wait(2))
            engine.request_stop()
        self.assertEqual(calls, ['start', 'start'])

    def test_shutdown_prevents_startup_and_global_process_kill(self):
        engine = PhononEngine()
        engine.request_stop()
        with patch.object(engine, '_kill_stale_servers') as kill, patch.object(engine, '_spawn') as spawn:
            with self.assertRaises(RuntimeError):
                engine.start_server()
            kill.assert_not_called()
            spawn.assert_not_called()

    def test_shutdown_during_startup_cleans_up_only_owned_child(self):
        engine = PhononEngine()
        spawned = threading.Event()
        child = Mock()
        child.poll.return_value = None
        errors = []
        def spawn():
            spawned.set()
            return child
        def boot():
            try:
                engine.start_server(wait_timeout=2)
            except RuntimeError as e:
                errors.append(str(e))
        with patch.object(engine, '_kill_stale_servers'), patch.object(engine, '_spawn', side_effect=spawn), \
             patch.object(engine, 'is_server_running', return_value=False):
            worker = threading.Thread(target=boot)
            worker.start()
            self.assertTrue(spawned.wait(2))
            engine.stop_server()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        child.terminate.assert_called_once()
        self.assertTrue(errors)


if __name__ == '__main__':
    unittest.main()
