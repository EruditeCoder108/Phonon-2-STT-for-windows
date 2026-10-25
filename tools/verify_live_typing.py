"""Pace public test audio through capture, live transport and field-output collector.

No microphone, hook or external field injection. --engine phonon starts an owned
server on a separate free port and stops it afterward. --engine whistle uses the
existing verified local runtime. Output times are from audio playback start.
"""
import argparse
import json
from pathlib import Path
import queue
import socket
import sys
import threading
import time
from unittest.mock import Mock, patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QObject
from src.config import DEFAULT_CONFIG
from src.config import DEFAULT_PHONON_MODEL, PHONON_MODELS
from src.core.engine import PhononEngine
from src.core.pipeline import DictationPipeline
from src.core.segmenter import UtteranceSegmenter
from src.core.vad import create_vad
from src.core.vocabulary import VocabularyEngine
from src.core.whistle import WhistleEngine
from tools.benchmark_whistle import word_error_rate
with patch("logging.handlers.RotatingFileHandler", side_effect=OSError("verification log disabled")):
    from src import main as APP

REFERENCE = "And so my fellow Americans ask not what your country can do for you ask what you can do for your country"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', choices=['phonon', 'whistle'], default='phonon')
    parser.add_argument('--model', choices=list(PHONON_MODELS), default=DEFAULT_PHONON_MODEL)
    parser.add_argument('--mode', choices=['accurate', 'fast'], default='accurate')
    parser.add_argument('--audio', type=Path, default=Path('.model-research/jfk.wav'))
    parser.add_argument('--reference', default=REFERENCE)
    parser.add_argument('--max-wer', type=float, default=0.0)
    args = parser.parse_args()
    with wave.open(str(args.audio)) as source:
        assert (source.getframerate(), source.getsampwidth(), source.getnchannels()) == (16000, 2, 1)
        pcm = source.readframes(source.getnframes())
    if args.engine == 'phonon':
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        engine = PhononEngine(port=port, model=args.model)
    else:
        engine = WhistleEngine()
    worker = None
    decodes = []
    original_transcribe = engine.transcribe
    def tracked_transcribe(pcm):
        result = original_transcribe(pcm)
        decodes.append({'audio_seconds': round(len(pcm) / 32000, 3), 'text': result.text, 'error': result.error})
        return result
    engine.transcribe = tracked_transcribe
    try:
        engine.start_server()
        app = APP.DictationApp.__new__(APP.DictationApp)
        QObject.__init__(app)
        app.engine = engine
        app.bridge = APP.AppBridge()
        app._transcribe_queue = queue.Queue()
        app._live_queue_lock = threading.Lock()
        app._live_pending, app._live_results = {}, {}
        app._live_closed_id = 0
        app._live_stream, app._stream_utt, app._live_failed_id = None, None, None
        app._worker_generation = app._session_generation = 1
        app._is_dictating, app._is_paused, app._shutting_down = True, False, False
        app._utterance_hwnd = 1
        app.config = DEFAULT_CONFIG.copy()
        app.config['dictation_mode'] = args.mode
        app.config['show_preview'] = True
        output, events, notices = [], [], []
        start = time.perf_counter()
        def inject(text):
            output.append(text)
            events.append({'at_s': round(time.perf_counter() - start, 3), 'text': text})
            return True
        app.pipeline = DictationPipeline(engine, VocabularyEngine({}), inject=inject,
                                         get_config=lambda k,d=None: app.config.get(k,d), get_hwnd=lambda: 1,
                                         on_event=lambda k,m: notices.append({'kind':k,'message':m}))
        segmenter = UtteranceSegmenter(create_vad(), app._on_utterance_ready,
                                      on_activity=app.pipeline.set_speech_active,
                                      on_live=app._on_live_ready, live_enabled=True, voice_gate=False,
                                      commit_partials=args.mode == 'fast')
        worker = threading.Thread(target=app._transcribe_worker, daemon=True)
        worker.start()
        start = time.perf_counter()
        for offset in range(0, len(pcm), 1024):
            segmenter.feed(pcm[offset:offset + 1024])
            due = start + min(offset + 1024, len(pcm)) / 32000
            time.sleep(max(0, due - time.perf_counter()))
        speech_end = time.perf_counter() - start
        segmenter.flush()
        app._is_dictating = False
        app._transcribe_queue.put(APP._FLUSH)
        app._transcribe_queue.put(None)
        worker.join(45)
        assert not worker.is_alive(), 'Worker did not finish'
        assert events, 'No words inserted'
        if args.mode == 'fast':
            assert events[0]['at_s'] < speech_end, 'Fast mode did not insert words before phrase end'
        transcript = ''.join(output)
        report = {'engine': args.engine, 'speech_seconds': round(speech_end, 3),
                  'first_field_text_s': events[0]['at_s'], 'last_field_text_s': events[-1]['at_s'],
                  'reference_wer': word_error_rate(args.reference, transcript), 'transcript': transcript,
                  'insertions': events, 'decodes': decodes,
                  'mode':args.mode, 'notices':notices,
                  'recovery':[entry.__dict__ for entry in app.pipeline.recovery.entries()],
                  'measurement': 'Public WAV paced to real time; actual VAD, worker, engine and output collector. No microphone or external typing.'}
        if args.mode == 'accurate':
            accepted = ' '.join(entry.text for entry in reversed(app.pipeline.recovery.entries()) if entry.status == 'Delivered')
            report['accepted_final_delivery_wer'] = word_error_rate(accepted, transcript)
            assert report['accepted_final_delivery_wer'] == 0, 'Final accepted words were lost during delivery'
        report['model'] = getattr(engine, 'model', args.engine)
        target = Path('.model-research') / f'live-{args.model if args.engine == "phonon" else args.engine}-{args.mode}.json'
        target.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))
        assert report['reference_wer'] <= args.max_wer, 'Output exceeded the allowed word error rate'
    finally:
        if worker is not None and worker.is_alive():
            app._transcribe_queue.put(None)
            worker.join(5)
        engine.stop_server()


if __name__ == '__main__':
    main()
