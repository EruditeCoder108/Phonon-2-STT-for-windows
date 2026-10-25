"""Live field insertion: stable words, final tails, focus, quotes and backpressure."""
import json
import os
import queue
import threading
import unittest
from unittest.mock import Mock, patch
from websockets.sync.server import serve

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject
from src.core.engine import TranscribeResult
from src.core.live import PhononStream
from src.core.pipeline import strip_wrapping_quotes
from src.core.segmenter import UtteranceSegmenter
from test_core import FakeEngine, ScriptedVAD, frames, make_pipeline, make_utt
with patch("logging.handlers.RotatingFileHandler", side_effect=OSError("test logging disabled")):
    from src import main as APP


def utterance(ident=1, **kwargs):
    result = make_utt(**kwargs)
    result.utterance_id = ident
    result.target_hwnd = 1
    return result


class LiveCommitTests(unittest.TestCase):
    def make_pipeline(self):
        engine = FakeEngine()
        pipe, typed, clock, config, hwnd = make_pipeline(engine)
        config['dictation_mode'] = 'fast'
        return engine, pipe, typed, clock, config, hwnd

    def test_types_agreed_words_before_the_phrase_ends(self):
        engine, pipe, typed, *_ = self.make_pipeline()
        utt = utterance()
        pipe.process_live(utt, TranscribeResult(text="Hello world."))
        self.assertEqual(typed, [])
        pipe.process_live(utt, TranscribeResult(text="Hello world again."))
        self.assertEqual(typed, ["Hello world "])
        engine.queue.append(TranscribeResult(text="Hello world again."))
        pipe.process(utt)
        pipe.flush()
        self.assertEqual("".join(typed), "Hello world again. ")

    def test_repeated_partial_and_final_never_duplicate_committed_prefix(self):
        _, pipe, typed, *_ = self.make_pipeline()
        utt = utterance()
        for _ in range(8):
            pipe.process_live(utt, TranscribeResult(text="I like this model."))
        pipe.process(utt, result=TranscribeResult(text="I like this model."))
        pipe.flush()
        self.assertEqual("".join(typed), "I like this model. ")

    def test_uncertain_word_waits_then_final_adds_correct_spelling(self):
        _, pipe, typed, *_ = self.make_pipeline()
        utt = utterance()
        pipe.process_live(utt, TranscribeResult(text="Meet Jon."))
        pipe.process_live(utt, TranscribeResult(text="Meet John tomorrow."))
        self.assertEqual("".join(typed), "Meet ")
        pipe.process(utt, result=TranscribeResult(text="Meet John tomorrow."))
        pipe.flush()
        self.assertEqual("".join(typed), "Meet John tomorrow. ")

    def test_changed_committed_word_does_not_delete_or_duplicate_field_text(self):
        _, pipe, typed, *_ = self.make_pipeline()
        utt = utterance()
        pipe.process_live(utt, TranscribeResult(text="Meet Jon today."))
        pipe.process_live(utt, TranscribeResult(text="Meet Jon today please."))
        before = "".join(typed)
        pipe.process(utt, result=TranscribeResult(text="Meet John today please."))
        pipe.flush()
        self.assertEqual("".join(typed), before + "please. ")
        self.assertEqual(pipe.recovery.entries()[0].text, "Meet John today please.")
        self.assertEqual(pipe.recovery.entries()[0].status, "Needs review")

    def test_whistle_waits_for_three_hypotheses_and_two_tail_words(self):
        engine, pipe, typed, *_ = self.make_pipeline()
        engine.stable_hypotheses, engine.live_guard_words = 3, 2
        utt = utterance()
        for text in ['Watch.', 'Watch your cut.', 'What your country can...',
                     'What your country can do for you.', 'What your country can do for you.',
                     'What your country can do for you ask what you can do.']:
            pipe.process_live(utt, TranscribeResult(text=text))
        self.assertTrue("".join(typed).startswith('What '))
        self.assertNotIn('Watch', "".join(typed))

    def test_unique_stable_ending_allows_tail_after_older_word_revision(self):
        _, pipe, typed, *_ = self.make_pipeline()
        utt = utterance()
        text = 'What your country can do for you ask what you can do for your'
        for _ in range(2):
            pipe.process_live(utt, TranscribeResult(text=text))
        pipe.process(utt, result=TranscribeResult(text='What your country can do for you as what you can do for your country.'))
        pipe.flush()
        self.assertEqual(''.join(typed), 'What your country can do for you ask what you can do for your country. ')

    def test_repeated_ending_does_not_guess_where_to_append(self):
        _, pipe, typed, *_ = self.make_pipeline()
        utt = utterance()
        for _ in range(2):
            pipe.process_live(utt, TranscribeResult(text='This is the first time we go to the store today'))
        before = ''.join(typed)
        pipe.process(utt, result=TranscribeResult(text='This was the first time we go to the store and then go to the store again.'))
        self.assertEqual(''.join(typed), before)

    def test_focus_change_blocks_entire_live_phrase_even_if_focus_returns(self):
        _, pipe, typed, _, _, hwnd = self.make_pipeline()
        utt = utterance()
        pipe.process_live(utt, TranscribeResult(text="First window text."))
        pipe.process_live(utt, TranscribeResult(text="First window text continues."))
        before = "".join(typed)
        hwnd['v'] = 2
        pipe.process_live(utt, TranscribeResult(text="First window text continues here."))
        hwnd['v'] = 1
        pipe.process(utt, result=TranscribeResult(text="First window text continues here."))
        pipe.flush()
        self.assertEqual("".join(typed), before)

    def test_late_live_result_is_ignored_after_final_commit(self):
        _, pipe, typed, *_ = self.make_pipeline()
        utt = utterance()
        pipe.process(utt, result=TranscribeResult(text="Already finished."))
        pipe.flush()
        before = list(typed)
        for _ in range(3):
            pipe.process_live(utt, TranscribeResult(text="Already finished late."))
        self.assertEqual(typed, before)

    def test_filtered_background_is_never_typed_live(self):
        _, pipe, typed, *_ = self.make_pipeline()
        utt = utterance(rms=150, floor=200)
        for _ in range(3):
            pipe.process_live(utt, TranscribeResult(text="A background sentence."))
        self.assertEqual(typed, [])

    def test_optional_preview_receives_live_words_and_clears_on_final(self):
        _, pipe, _, *_ = self.make_pipeline()
        previews = []
        pipe.on_preview = previews.append
        utt = utterance()
        pipe.process_live(utt, TranscribeResult(text='"Hello there."'))
        self.assertEqual(previews, ['Hello there.'])
        pipe.process(utt, result=TranscribeResult(text='"Hello there."'))
        self.assertEqual(previews[-1], '')

    def test_injection_failure_prevents_later_live_duplicates(self):
        _, pipe, typed, *_ = self.make_pipeline()
        pipe.inject = Mock(return_value=False)
        utt = utterance()
        for _ in range(3):
            pipe.process_live(utt, TranscribeResult(text="A live phrase."))
        pipe.process(utt, result=TranscribeResult(text="A live phrase."))
        self.assertEqual(pipe.inject.call_count, 1)

    def test_wrapper_quotes_removed_in_batch_and_live_but_internal_quotes_stay(self):
        for quoted in ('"Okay, this is Whistle."', '“Okay, this is Whistle.”'):
            with self.subTest(quoted=quoted):
                _, pipe, typed, *_ = self.make_pipeline()
                utt = utterance()
                for _ in range(2):
                    pipe.process_live(utt, TranscribeResult(text=quoted))
                pipe.process(utt, result=TranscribeResult(text=quoted))
                pipe.flush()
                self.assertEqual("".join(typed), "Okay, this is Whistle. ")
        _, pipe, typed, *_ = self.make_pipeline()
        pipe.process(make_utt(), result=TranscribeResult(text='He said "hello" to me.'))
        pipe.flush()
        self.assertEqual("".join(typed), 'He said "hello" to me. ')

    def test_quote_cleanup_can_be_disabled(self):
        _, pipe, typed, _, config, _ = self.make_pipeline()
        config['strip_outer_quotes'] = False
        pipe.process(make_utt(), result=TranscribeResult(text='"Keep these quotes."'))
        pipe.flush()
        self.assertEqual("".join(typed), '"Keep these quotes." ')

    def test_shortened_final_does_not_add_punctuation_after_committed_space(self):
        _, pipe, typed, *_ = self.make_pipeline()
        utt = utterance()
        for _ in range(2):
            pipe.process_live(utt, TranscribeResult(text="Hello world again."))
        pipe.process(utt, result=TranscribeResult(text="Hello world."))
        pipe.flush()
        self.assertEqual("".join(typed), "Hello world ")

    def test_consecutive_live_phrases_keep_spacing_and_punctuation(self):
        _, pipe, typed, *_ = self.make_pipeline()
        for ident, text in [(1, "First complete sentence."), (2, "Second complete sentence.")]:
            utt = utterance(ident, start=ident * 3)
            for _ in range(2):
                pipe.process_live(utt, TranscribeResult(text=text))
            pipe.process(utt, result=TranscribeResult(text=text))
        pipe.flush()
        self.assertEqual("".join(typed), "First complete sentence. Second complete sentence. ")


class LiveCaptureTests(unittest.TestCase):
    def test_capture_emits_live_updates_without_waiting_for_silence(self):
        snapshots, final, speculative = [], [], []
        segmenter = UtteranceSegmenter(ScriptedVAD(), on_utterance=final.append,
                                      on_live=snapshots.append, live_enabled=True,
                                      on_speculative=lambda *args: speculative.append(args), voice_gate=False)
        segmenter.feed(frames(80, 3000))
        self.assertGreaterEqual(len(snapshots), 4)
        self.assertEqual(final, [])
        self.assertEqual(speculative, [])
        segmenter.flush()
        self.assertEqual(final[0].utterance_id, snapshots[-1].utterance_id)
        self.assertGreaterEqual(len(final[0].pcm), len(snapshots[-1].pcm))

    def test_live_ids_remain_monotonic_across_sessions(self):
        snapshots, final = [], []
        segmenter = UtteranceSegmenter(ScriptedVAD(), final.append, on_live=snapshots.append,
                                      live_enabled=True, voice_gate=False)
        segmenter.feed(frames(30, 3000))
        segmenter.flush()
        first = final[-1].utterance_id
        segmenter.reset()
        segmenter.feed(frames(30, 3000))
        segmenter.flush()
        self.assertGreater(final[-1].utterance_id, first)

    def test_slow_worker_gets_latest_snapshot_without_a_backlog(self):
        app = APP.DictationApp.__new__(APP.DictationApp)
        QObject.__init__(app)
        app._is_dictating, app._is_paused = True, False
        app.config = {'live_typing': True}
        app._session_generation, app._utterance_hwnd = 1, 1
        app._live_closed_id = 0
        app._live_queue_lock = threading.Lock()
        app._live_pending, app._live_results = {}, {}
        app._transcribe_queue = queue.Queue()
        for size in range(100):
            app._on_live_ready(utterance(pcm=bytes(100 + size * 2)))
        self.assertEqual(app._transcribe_queue.qsize(), 1)
        self.assertEqual(len(app._live_pending[(1, 1)].pcm), 298)
        app._on_utterance_ready(utterance())
        self.assertEqual(app._live_pending, {})

    def test_native_partial_cannot_return_after_its_phrase_closed(self):
        app = APP.DictationApp.__new__(APP.DictationApp)
        QObject.__init__(app)
        app._live_queue_lock = threading.Lock()
        app._stream_utt = utterance()
        app._live_closed_id = 1
        app._live_results = {}
        app._transcribe_queue = queue.Queue()
        app._post_live_result(1, 1, 'Late transcript')
        self.assertEqual(app._live_results, {})
        self.assertTrue(app._transcribe_queue.empty())


class LiveTransportTests(unittest.TestCase):
    def test_real_websocket_partial_final_done_protocol(self):
        received = []
        ready = threading.Event()
        def handler(socket):
            received.append(json.loads(socket.recv()))
            for message in socket:
                if isinstance(message, bytes):
                    received.append(message)
                    socket.send(json.dumps({'type': 'partial', 'text': 'Hello there.'}))
                else:
                    self.assertEqual(json.loads(message), {'type': 'end'})
                    socket.send(json.dumps({'type': 'final', 'text': 'Hello there.'}))
                    socket.send(json.dumps({'type': 'done', 'text': 'Hello there.'}))
                    return
        with serve(handler, '127.0.0.1', 0) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.socket.getsockname()[1]
            partials = []
            def partial(text):
                partials.append(text)
                ready.set()
            stream = PhononStream(Mock(host='127.0.0.1', port=port), partial)
            stream.update(bytes(6400))
            self.assertTrue(ready.wait(2))
            result = stream.finish(bytes(9600), timeout=2)
            server.shutdown()
            thread.join(2)
        self.assertFalse(result.error)
        self.assertEqual(result.text, 'Hello there.')
        self.assertEqual(received[0], {'sample_rate': 16000, 'format': 'pcm_s16le'})
        self.assertEqual(sum(len(part) for part in received[1:]), 9600)
        self.assertIn('Hello there.', partials)

    def test_server_refusal_returns_error_and_closes(self):
        def handler(socket):
            socket.recv()
            socket.send(json.dumps({'type': 'error', 'message': 'engine busy'}))
        with serve(handler, '127.0.0.1', 0) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            stream = PhononStream(Mock(host='127.0.0.1', port=server.socket.getsockname()[1]), Mock())
            self.assertTrue(stream._done.wait(2))
            result = stream.finish(bytes(3200), timeout=2)
            server.shutdown()
            thread.join(2)
        self.assertTrue(result.error)

    def test_native_transport_sends_only_new_pcm_then_end(self):
        messages = [json.dumps({'type': 'partial', 'text': 'Hello there.'}),
                    json.dumps({'type': 'final', 'text': 'Hello there.'}),
                    json.dumps({'type': 'done', 'text': 'Hello there.'})]
        socket = Mock()
        socket.__enter__ = Mock(return_value=socket)
        socket.__exit__ = Mock()
        socket.__iter__ = Mock(return_value=iter(messages))
        partials = []
        with patch('src.core.live.connect', return_value=socket):
            stream = PhononStream(Mock(host='127.0.0.1', port=8010), partials.append)
            stream._reader.join(1)
            # The fake delivered done early. Allow writes to inspect frame shape.
            stream._done.clear()
            stream.update(bytes(6400))
            stream.update(bytes(9600))
            self.assertEqual(sum(len(c.args[0]) for c in socket.send.call_args_list if isinstance(c.args[0], bytes)), 9600)
            stream._done.set()
            result = stream.finish(bytes(9600))
            # A completed socket cannot be resumed and finish returns an explicit error.
            self.assertTrue(result.error)
            self.assertEqual(partials, ['Hello there.'])
            socket.__exit__.assert_called()

    def test_quote_helper_keeps_unmatched_final_quotes_and_internal_quotes(self):
        self.assertEqual(strip_wrapping_quotes('He said "go".'), 'He said "go".')
        self.assertEqual(strip_wrapping_quotes('"unfinished'), '"unfinished')
        self.assertEqual(strip_wrapping_quotes('"unfinished', partial=True), 'unfinished')
