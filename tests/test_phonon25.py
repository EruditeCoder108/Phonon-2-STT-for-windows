"""Migration, model identity, lifecycle and Unicode dictation regressions."""
import io
import ctypes
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from src import config
from src.core.engine import PhononEngine, TranscribeResult
from src.core.filters import judge, normalized_words
from src.core import injector
from test_core import make_utt, make_pipeline, FakeEngine
import test_phase_two as phase_two
import test_windows_reliability as reliability


class ModelMigrationTests(unittest.TestCase):
    def load(self, saved):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps(saved), encoding='utf-8')
            with patch.object(config, 'CONFIG_PATH', str(path)):
                return config.load_config()

    def test_old_profile_gets_new_model_without_losing_preferences(self):
        saved = {'speech_engine': 'phonon', 'mic_name': 'My USB mic', 'save_history': False,
                 'vocabulary': {'my email': 'private@example.test'}}
        loaded = self.load(saved)
        self.assertEqual(loaded['phonon_model'], 'phonon-2.5-en')
        for key, value in saved.items():
            self.assertEqual(loaded[key], value)

    def test_whistle_and_explicit_legacy_choices_are_preserved(self):
        self.assertEqual(self.load({'speech_engine': 'whistle'})['speech_engine'], 'whistle')
        self.assertEqual(self.load({'phonon_model': 'phonon-2'})['phonon_model'], 'phonon-2')

    def test_bad_model_field_does_not_reset_other_preferences(self):
        for value in ['not-a-model', None, []]:
            loaded = self.load({'phonon_model': value, 'save_history': False})
            self.assertEqual(loaded['phonon_model'], 'phonon-2.5-en')
            self.assertFalse(loaded['save_history'])


class ModelIdentityTests(unittest.TestCase):
    def health(self, engine, payload):
        response = Mock(status=200)
        response.read.return_value = json.dumps(payload).encode()
        engine._health_opener = Mock()
        engine._health_opener.open.return_value.__enter__ = Mock(return_value=response)
        engine._health_opener.open.return_value.__exit__ = Mock(return_value=False)
        return engine.is_server_running()

    def test_checks_official_repo_instead_of_a_served_display_alias(self):
        engine = PhononEngine()
        self.assertTrue(self.health(engine, {'kind': 'speech', 'model': 'my-speech-service',
                                           'repo': 'FermionResearch/Phonon-2.5-English'}))
        self.assertFalse(self.health(engine, {'kind': 'speech', 'model': engine.model,
                                            'repo': 'FermionResearch/Phonon-2'}))

    def test_wrong_server_is_never_adopted_or_killed(self):
        engine = PhononEngine()
        self.health(engine, {'kind': 'speech', 'model': 'FermionResearch/Phonon-2'})
        with patch.object(engine, '_spawn') as spawn, patch.object(engine, '_terminate_process') as kill:
            with self.assertRaisesRegex(RuntimeError, 'expected Phonon-2.5 English'):
                engine.start_server()
            spawn.assert_not_called()
            kill.assert_not_called()

    def test_generic_healthy_web_service_is_not_speech_readiness(self):
        engine = PhononEngine()
        self.assertFalse(self.health(engine, {'status': 'ok'}))

    def test_rechecking_owned_server_retains_cleanup_ownership(self):
        engine = PhononEngine()
        child = Mock()
        child.poll.return_value = None
        engine._process, engine._owns_process = child, True
        with patch.object(engine, 'is_server_running', return_value=True):
            engine.start_server()
        engine.stop_server()
        child.terminate.assert_called_once()

    def test_timed_out_startup_reaps_child(self):
        engine = PhononEngine()
        child = Mock()
        child.poll.return_value = None
        with patch.object(engine, 'is_server_running', return_value=False), \
             patch.object(engine, '_spawn', return_value=child):
            with self.assertRaises(TimeoutError):
                engine.start_server(wait_timeout=0)
        child.terminate.assert_called_once()
        self.assertIsNone(engine._process)

    def test_restart_closes_job_to_reap_redirector_descendants(self):
        engine = PhononEngine()
        child = Mock()
        child.poll.return_value = None
        engine._process, engine._owns_process, engine._job = child, True, 123
        with patch('src.core.engine._close_job') as close:
            engine._terminate_process()
        close.assert_called_once_with(123)
        self.assertIsNone(engine._job)

    def test_launch_names_model_and_binds_to_loopback(self):
        engine = PhononEngine(model='phonon-2.5')
        child = Mock(stdout=io.BytesIO(), stderr=io.BytesIO())
        with patch('src.core.engine.importlib.metadata.version', return_value='0.2.14'), \
             patch('src.core.engine.os.path.exists', return_value=True), \
             patch('src.core.engine._create_kill_on_close_job', return_value=None), \
             patch('src.core.engine.subprocess.Popen', return_value=child) as spawn:
            engine._spawn()
        command = spawn.call_args.args[0]
        self.assertTrue(command[0].endswith('python.exe'))
        self.assertEqual(command[1:4], ['-c', 'from fermion.cli import main; main()', 'serve'])
        self.assertEqual(command[4:], ['phonon-2.5', '--host', '127.0.0.1', '--port', '8010'])
        self.assertEqual(spawn.call_args.kwargs['env']['HF_HUB_DISABLE_TELEMETRY'], '1')

    def test_old_runtime_fails_before_spawning_wrong_model(self):
        with patch('src.core.engine.importlib.metadata.version', return_value='0.2.7'), \
             patch('src.core.engine.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, '0.2.14'):
                PhononEngine()._spawn()
            spawn.assert_not_called()


class MultilingualFilterTests(unittest.TestCase):
    def test_cyrillic_greek_and_accented_words_are_not_discarded(self):
        for text in ['Привет мир.', 'Καλημέρα κόσμε.', 'Écoutez ça.', 'Guten Morgen.']:
            with self.subTest(text=text):
                self.assertEqual(len(normalized_words(text)), 2)
                self.assertTrue(judge(text, make_utt())[0])
                engine = FakeEngine()
                engine.queue.append(TranscribeResult(text=text))
                pipeline, typed, *_ = make_pipeline(engine)
                pipeline.process(make_utt())
                pipeline.flush()
                self.assertEqual(''.join(typed).strip(), text)

    def test_rejection_reason_does_not_leak_private_word(self):
        keep, reason = judge('SecretProjectName', make_utt(prob=.6))
        self.assertFalse(keep)
        self.assertNotIn('secretprojectname', reason.lower())


class ModelSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = phase_two.QApplication.instance() or phase_two.QApplication([])

    def test_model_selection_is_saved_and_disabled_for_whistle(self):
        window = phase_two.SettingsTests.make_window(self)
        changes = []
        window.settings_saved.connect(lambda saved: changes.append(saved.copy()))
        window.phonon_model_combo.setCurrentIndex(window.phonon_model_combo.findData('phonon-2.5'))
        self.assertEqual(changes[-1]['phonon_model'], 'phonon-2.5')
        window.engine_combo.setCurrentIndex(window.engine_combo.findData('whistle'))
        self.assertFalse(window.phonon_model_combo.isEnabled())


class ClipboardUnicodeTests(unittest.TestCase):
    def test_rich_clipboard_formats_force_non_destructive_typing(self):
        with patch.object(injector.user32, 'OpenClipboard', return_value=True), \
             patch.object(injector.user32, 'CloseClipboard'), \
             patch.object(injector.user32, 'EnumClipboardFormats', side_effect=[13, 49301]):
            self.assertTrue(injector._clipboard_has_non_text())

    def test_plain_text_clipboard_allows_paste(self):
        with patch.object(injector.user32, 'OpenClipboard', return_value=True), \
             patch.object(injector.user32, 'CloseClipboard'), \
             patch.object(injector.user32, 'EnumClipboardFormats', side_effect=[1, 7, 13, 16, 0]):
            self.assertFalse(injector._clipboard_has_non_text())

    def test_non_bmp_text_includes_all_utf16_units_and_terminator(self):
        text = 'Hello 😀 café'
        encoded = text.encode('utf-16-le') + b'\x00\x00'
        buffer = ctypes.create_string_buffer(len(encoded))
        with patch.object(injector.user32, 'OpenClipboard', return_value=True), \
             patch.object(injector.user32, 'CloseClipboard'), \
             patch.object(injector.user32, 'EmptyClipboard', return_value=True), \
             patch.object(injector.user32, 'SetClipboardData', return_value=1), \
             patch.object(injector.kernel32, 'GlobalAlloc', return_value=1) as allocate, \
             patch.object(injector.kernel32, 'GlobalLock', return_value=ctypes.addressof(buffer)), \
             patch.object(injector.kernel32, 'GlobalUnlock'):
            self.assertTrue(injector.set_clipboard_text(text))
        self.assertEqual(allocate.call_args.args[1], len(encoded))
        self.assertEqual(buffer.raw, encoded)

    def test_failed_allocation_preserves_existing_clipboard(self):
        with patch.object(injector.user32, 'OpenClipboard', return_value=True), \
             patch.object(injector.user32, 'CloseClipboard'), \
             patch.object(injector.user32, 'EmptyClipboard') as empty, \
             patch.object(injector.kernel32, 'GlobalAlloc', return_value=0):
            self.assertFalse(injector.set_clipboard_text('hello'))
            empty.assert_not_called()


class ModelSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = phase_two.QApplication.instance() or phase_two.QApplication([])

    def setUp(self):
        reliability.SessionTests.setUp(self)
        self.app.vocab = Mock()
        self.app.history = Mock()
        self.app.tray = Mock()
        self.app._applied_sound = ('glass', 60)
        self.app._applied_live = True
        self.app._applied_hotkey = ('ctrl+space', False)
        self.app._applied_mic = None

    def test_model_change_drains_worker_even_when_engine_family_is_unchanged(self):
        app = self.app
        app._applied_phonon_settings = ('phonon-2.5-en', 8010, None)
        app.config['phonon_model'] = 'phonon-2.5'
        with patch.object(reliability.APP, 'save_config'), patch.object(app, '_queue_engine_change') as switch:
            app._apply_new_settings(app.config.copy())
            switch.assert_called_once()
        self.assertEqual(app._applied_phonon_settings[0], 'phonon-2.5')


if __name__ == '__main__':
    unittest.main()
