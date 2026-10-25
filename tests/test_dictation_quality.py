"""Delivery completeness, recoverability and actual observed dictation regressions."""
import os
import unittest
from unittest.mock import Mock, patch, mock_open

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from src.config import DEFAULT_CONFIG, recognition_terms
from src.config import load_config
from src.core.engine import PhononEngine, TranscribeResult
from src.core.filters import judge
from src.core.pipeline import continuation_index
from src.core.recovery import TranscriptRecovery
from src.core.vocabulary import VocabularyEngine
from src.core import injector as INJ
from test_core import FakeEngine, make_pipeline, make_utt


def utterance():
    u = make_utt()
    u.target_hwnd, u.utterance_id = 1, 1
    return u


class DeliveryQualityTests(unittest.TestCase):
    def test_default_delivers_final_contraction_without_committing_early_guess(self):
        p, typed, *_ = make_pipeline(FakeEngine())
        u = utterance()
        for _ in range(3):
            p.process_live(u, TranscribeResult(text="I'm from the cutter."))
        self.assertEqual(typed, [])
        p.process(u, result=TranscribeResult(text='I am from the cutter lying off the coast.'))
        p.flush()
        self.assertEqual(''.join(typed), 'I am from the cutter lying off the coast. ')
        self.assertEqual(p.recovery.entries()[0].status, 'Delivered')

    def test_accuracy_mode_preserves_internal_punctuation_and_final_replacements(self):
        p, typed, *_ = make_pipeline(FakeEngine())
        p.vocab = VocabularyEngine({'new york': 'NYC'})
        u = utterance()
        for text in ['Visit New.', 'Visit New today.']:
            p.process_live(u, TranscribeResult(text=text))
        p.process(u, result=TranscribeResult(text='Visit New York. Meet John tomorrow!'))
        p.flush()
        self.assertEqual(''.join(typed), 'Visit NYC. Meet John tomorrow! ')

    def test_fast_mode_handles_contraction_boundary_and_delivers_tail(self):
        p, typed, _, cfg, _ = make_pipeline(FakeEngine())
        cfg['dictation_mode'] = 'fast'
        u = utterance()
        for _ in range(2):
            p.process_live(u, TranscribeResult(text="I'm from the cutter."))
        p.process(u, result=TranscribeResult(text='I am from the cutter lying off the coast.'))
        p.flush()
        self.assertEqual(''.join(typed), "I'm from the cutter lying off the coast. ")
        self.assertEqual(p.recovery.entries()[0].text, 'I am from the cutter lying off the coast.')

    def test_ambiguous_fast_revision_retains_the_entire_final_without_history(self):
        history = Mock(enabled=False)
        p, typed, _, cfg, _ = make_pipeline(FakeEngine(), history=history)
        cfg['dictation_mode'] = 'fast'
        u = utterance()
        for _ in range(2):
            p.process_live(u, TranscribeResult(text='First uncertain words today.'))
        before = ''.join(typed)
        final = 'Completely different wording and a valuable ending.'
        p.process(u, result=TranscribeResult(text=final))
        self.assertEqual(''.join(typed), before)
        entry = p.recovery.entries()[0]
        self.assertEqual(entry.text, final)
        self.assertEqual(entry.status, 'Needs review')

    def test_native_control_change_blocks_output_even_inside_same_window(self):
        control = [1]
        e = FakeEngine()
        e.queue.append(TranscribeResult(text='Retain my complete text.'))
        p, typed, *_ = make_pipeline(e, get_focus=lambda: (1, control[0]))
        u = utterance()
        u.target_focus = (1, 1)
        control[0] = 2
        p.process(u)
        p.flush()
        self.assertEqual(typed, [])
        self.assertEqual(p.recovery.entries()[0].text, 'Retain my complete text.')

    def test_focus_event_move_blocks_shared_handle_fields(self):
        epoch = [10]
        e = FakeEngine()
        e.queue.append(TranscribeResult(text='Virtual field protection.'))
        p, typed, *_ = make_pipeline(e, get_focus=lambda: (1, 1, epoch[0]))
        u = utterance()
        u.target_focus = (1, 1, 10)
        epoch[0] = 11
        p.process(u)
        self.assertEqual(typed, [])
        self.assertEqual(p.recovery.entries()[0].text, 'Virtual field protection.')

    def test_injection_failure_keeps_the_final(self):
        p, typed, *_ = make_pipeline(FakeEngine())
        p.inject = lambda text: False
        p.process(utterance(), result=TranscribeResult(text='Keep the ending for recovery.'))
        self.assertEqual(typed, [])
        self.assertEqual(p.recovery.entries()[0].text, 'Keep the ending for recovery.')
        self.assertEqual(p.recovery.entries()[0].status, 'Not typed')

    def test_long_technical_word_passes_but_a_short_timestamp_for_a_hum_does_not(self):
        u = make_utt(speech_s=1.12, prob=.98, rms=1239, floor=30)
        self.assertTrue(judge('monocotyledons', u)[0])
        self.assertTrue(judge('monocotyledons', u, word_span_s=1.0)[0])
        self.assertFalse(judge('True', u, word_span_s=.15)[0])

    def test_recovery_is_bounded_and_can_be_cleared(self):
        store = TranscriptRecovery(limit=2)
        for text in ['old', 'middle', '<b>new</b>']:
            store.add(text, 'Delivered')
        self.assertEqual([e.text for e in store.entries()], ['<b>new</b>', 'middle'])
        store.clear()
        self.assertEqual(store.entries(), [])

    def test_supplementary_character_has_its_own_intact_input_packet(self):
        packets = list(INJ._unicode_batches('before 🌱 after'))
        self.assertEqual([flag for _,flag in packets], [False, True, False])
        self.assertEqual([event for batch,_ in packets for event in batch], INJ._text_to_key_events('before 🌱 after'))
        self.assertEqual(len(packets[1][0]), 4)
        with patch.object(INJ.user32, 'SendInput', side_effect=lambda count,*args:count) as send, patch.object(INJ.time, 'sleep') as pause:
            self.assertTrue(INJ.type_unicode_chars('before 🌱 after'))
        self.assertEqual(send.call_count, 3)
        self.assertEqual(pause.call_count, 2)

    def test_repeated_anchor_does_not_guess_a_continuation(self):
        self.assertIsNone(continuation_index('meet Jon today'.split(), 'meet John today and Jon today again'.split()))

    def test_http_sends_recognition_terms_without_changing_replacement_rules(self):
        engine = PhononEngine(hotwords=['Phonon', 'macOS'])
        conn = Mock()
        response = conn.getresponse.return_value
        response.status, response.will_close = 200, False
        response.read.return_value = b'{"text":"Phonon", "words":[]}'
        with patch.object(engine, '_connection', return_value=conn):
            self.assertFalse(engine.transcribe(bytes(32000)).error)
        body = conn.request.call_args.kwargs['body']
        self.assertIn(b'name="hotwords"', body)
        self.assertIn(b'["Phonon", "macOS"]', body)
        self.assertEqual(recognition_terms([' Phonon ', 'phonon', 3, 'macOS']), ['Phonon', 'macOS'])
        self.assertEqual(DEFAULT_CONFIG['recognition_terms'], [])

    def test_old_profile_migrates_to_complete_phrases_with_feedback(self):
        with patch('src.config.os.path.exists',return_value=True), patch('builtins.open',mock_open(read_data='{"show_preview":false,"save_history":false}')):
            cfg = load_config()
        self.assertEqual(cfg['dictation_mode'], 'accurate')
        self.assertTrue(cfg['show_preview'])
        self.assertFalse(cfg['save_history'])
        with patch('src.config.os.path.exists',return_value=True), patch('builtins.open',mock_open(read_data='{"dictation_mode":"accurate","show_preview":false}')):
            self.assertFalse(load_config()['show_preview'])


if __name__ == '__main__':
    unittest.main()
