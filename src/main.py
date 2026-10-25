"""
Phonon-2 Windows Native Dictation Application

Orchestrates:
1. Local Phonon-2 speech engine server (supervised: auto-restart if it dies)
2. Global Ctrl+Space toggle keyboard hook (with debouncing & repeat suppression)
3. Hot-mic WASAPI capture + VAD segmentation into speech-only utterances
4. Dedicated FIFO transcription worker running the DictationPipeline
   (evidence-based filtering, phrase stitching, deferred punctuation, injection)
5. Interactive Floating Circular Animated Orb HUD (click to dictate, draggable, reactive ripples)
6. Fluent Dashboard Control Center & System Tray integration
"""

import sys
import os
import time
import queue
import threading
import logging
from PySide6.QtWidgets import QApplication, QMenu
from PySide6.QtCore import QObject, Qt, Signal, Slot, QPoint, QTimer
from PySide6.QtGui import QIcon, QAction
import ctypes

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
    datefmt="%H:%M:%S",
)
# Persistent log (no dictated text unless history is on) so odd behaviour can be diagnosed afterwards.
try:
    from logging.handlers import RotatingFileHandler
    _fh = RotatingFileHandler(os.path.join(os.path.expanduser("~"), ".phonon2.log"),
                              maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s", "%H:%M:%S"))
    logging.getLogger().addHandler(_fh)
except Exception:
    pass
logger = logging.getLogger("PhononApp")

# Internal modules
from src.config import load_config, save_config
from src.core.injector import inject_text, get_foreground_hwnd
from src.core import devices
from src.core.focus import focus_identity, start_focus_tracking, stop_focus_tracking
from src.core.audio import AudioCaptureEngine
from src.core.segmenter import Utterance
from src.core.hotkey import GlobalHotkeyManager, parse_hotkey
from src.core.engine import PhononEngine, TranscribeResult
from src.core.whistle import WhistleEngine
from src.core.pipeline import DictationPipeline
from src.core.sound_effects import SoundEffects
from src.core.vocabulary import VocabularyEngine
from src.core.history import HistoryManager
from src.ui.recovery_dialog import RecoveryDialog
from src.ui.hud import CircularOrbHUD
from src.ui.tray import SystemTray
from src.ui.dashboard import DashboardWindow
from src.ui.orb_settings_dialog import OrbSettingsDialog
from src.ui.icons import get_svg_icon

_FLUSH = object()   # queue marker: finish the current sentence (type any deferred punctuation)
_RESET = object()   # resets run on the same worker as decoding, never concurrently
_ENGINE = object()  # switch engines only after previously queued phrases finish
_LIVE = object()
_LIVE_RESULT = object()


class _SpeculativeItem:
    __slots__ = ("utt", "spec_id")

    def __init__(self, utt: Utterance, spec_id: int):
        self.utt = utt
        self.spec_id = spec_id


class AppBridge(QObject):
    """Thread-safe bridge between background threads and Qt UI."""
    sig_started = Signal()
    sig_volume = Signal(float)
    sig_stopped = Signal()
    sig_engine_state = Signal(object, bool, str)  # source engine, ready, message
    sig_status = Signal(str, bool)            # tray status text, active
    sig_notice = Signal(str, str)             # tray balloon: title, message
    sig_devices_changed = Signal()            # mic list changed / mic reopened: refresh the dashboard list
    sig_preview = Signal(int, str)            # session generation, recognized hypothesis
    sig_start_requested = Signal()
    sig_stop_requested = Signal()
    sig_toggle_requested = Signal()
    sig_shutdown_finished = Signal()
    sig_refresh_required = Signal()
    sig_engine_changed = Signal(int)


class DictationApp(QObject):
    def __init__(self, q_app: QApplication):
        super().__init__(q_app)
        self.q_app = q_app
        start_focus_tracking()
        self.config = load_config()
        try:
            parse_hotkey(self.config.get("trigger_key", "ctrl+space"))
        except (ValueError, AttributeError):
            logger.warning("Invalid saved hotkey; restoring Ctrl+Space.")
            self.config["trigger_key"] = "ctrl+space"
        self.bridge = AppBridge()

        # Data & Core Features
        self.history = HistoryManager(enabled=self.config.get("save_history", True))
        self.vocab = VocabularyEngine(self.config.get("vocabulary", {}))
        self._is_dictating = False
        self._is_paused = False
        self._session_start_time = 0.0
        self._last_notice_time = 0.0
        self._applied_mic = self.config.get("mic_name")
        self._device_lock = threading.Lock()
        self._session_generation = 0
        self._worker_generation = 0
        self._utterance_hwnd = 0
        self._utterance_focus = None
        self._shutting_down = False
        self._device_refreshing = False
        self._engine_switching = False
        self._engine_selection_generation = 0
        self._applied_engine = self.config.get("speech_engine", "phonon")
        self._applied_phonon_settings = (self.config.get("phonon_model", "phonon-2.5-en"),
                                        self.config.get("port", 8010), self.config.get("engine_threads"))
        self._applied_live = self.config.get("live_typing", True)
        self._applied_mode = self.config.get("dictation_mode", "accurate")
        self._live_queue_lock = threading.Lock()
        self._live_pending = {}
        self._live_results = {}
        self._live_closed_id = 0
        self._live_stream = None
        self._stream_utt = None
        self._stream_generation = 0
        self._live_failed_id = None

        # Dedicated FIFO Transcription Queue & Worker Thread
        self._transcribe_queue: queue.Queue = queue.Queue()
        self._worker_thread = threading.Thread(
            target=self._transcribe_worker,
            daemon=True,
            name="SequentialTranscribeWorker"
        )

        # Set high-resolution light-blue orb application icon
        icon_path = os.path.join(os.path.dirname(__file__), "..", "assets", "icon.png")
        if os.path.exists(icon_path):
            self.q_app.setWindowIcon(QIcon(icon_path))

        # Floating Interactive Circular Orb HUD
        self.hud = CircularOrbHUD()
        self.hud.clicked.connect(self._toggle_dictation)
        self.hud.context_menu_requested.connect(self._show_orb_context_menu)
        self.hud.long_pressed.connect(self._open_orb_settings)
        self.hud.apply_appearance_settings(self.config)
        self.hud.show()

        # Tray & Dashboard
        self.tray = SystemTray()
        self.dashboard = None

        # Connect UI bridge signals
        self.bridge.sig_started.connect(lambda: self.hud.set_state("listening"))
        self.bridge.sig_volume.connect(self._on_mic_level)
        self.bridge.sig_stopped.connect(lambda: self.hud.set_state("idle"))
        self.bridge.sig_engine_state.connect(self._on_engine_state)
        self.bridge.sig_status.connect(self.tray.set_status)
        self.bridge.sig_notice.connect(self._show_notice)
        self.bridge.sig_devices_changed.connect(self._on_devices_changed)
        self.bridge.sig_preview.connect(self._on_preview)
        self.bridge.sig_start_requested.connect(self._start_dictation, Qt.ConnectionType.QueuedConnection)
        self.bridge.sig_stop_requested.connect(self._stop_dictation, Qt.ConnectionType.QueuedConnection)
        self.bridge.sig_toggle_requested.connect(self._toggle_dictation, Qt.ConnectionType.QueuedConnection)
        self.bridge.sig_shutdown_finished.connect(self.q_app.quit)
        self.bridge.sig_refresh_required.connect(self._on_refresh_required, Qt.ConnectionType.QueuedConnection)
        self.bridge.sig_engine_changed.connect(self._on_engine_changed)

        # Core Engines
        self.engine = self._create_engine(self.config)
        self._bind_engine(self.engine)
        self.sounds = SoundEffects(
            enabled=self.config.get("sound_effects", True),
            theme=self.config.get("sound_theme", "glass"),
            volume=self.config.get("sound_volume", 60),
        )
        devices.add_stream_closer(self.sounds.close)     # its output stream must close while PortAudio re-scans
        self._applied_sound = (self.config.get("sound_theme", "glass"), self.config.get("sound_volume", 60))
        self.pipeline = DictationPipeline(
            engine=self.engine,
            vocab=self.vocab,
            inject=lambda text: inject_text(text, self.config.get("injection_mode", "auto")),
            inject_target=lambda text, hwnd: inject_text(text, self.config.get("injection_mode", "auto"), expected_hwnd=hwnd),
            history=self.history,
            get_config=lambda key, default=None: self.config.get(key, default),
            get_hwnd=get_foreground_hwnd,
            get_focus=focus_identity,
            on_event=self._on_pipeline_event,
            on_preview=lambda txt: self.bridge.sig_preview.emit(self._worker_generation, txt),
        )
        self.audio = AudioCaptureEngine(
            device_name=self.config.get("mic_name"),
            on_utterance=self._on_utterance_ready,
            on_level_update=lambda lvl: self.bridge.sig_volume.emit(lvl),
            on_activity=self._on_speech_activity,
            on_speculative=self._on_speculative_ready,
            on_cancel_speculative=self._on_cancel_speculative,
            pause_ms=self.config.get("pause_ms", 700),
            voice_gate=self.config.get("voice_gate", True),
            on_live=self._on_live_ready,
            live_enabled=self.config.get("live_typing", True),
            commit_partials=self.config.get("dictation_mode", "accurate") == "fast",
        )
        self.hotkey = GlobalHotkeyManager(
            trigger_key=self.config.get("trigger_key", "ctrl+space"),
            push_to_talk=self.config.get("push_to_talk", False),
            on_start=self.bridge.sig_start_requested.emit,
            on_stop=self.bridge.sig_stop_requested.emit,
            is_active=lambda: self._is_dictating,
            on_toggle=self.bridge.sig_toggle_requested.emit,
        )
        self._worker_thread.start()

        # Connect Tray actions
        self.tray.orb_settings_requested.connect(self._open_orb_settings)
        self.tray.settings_requested.connect(self._open_dashboard)
        self.tray.toggle_mode_requested.connect(self._toggle_mode)
        self.tray.quit_requested.connect(self.shutdown)
        self.tray.recovery_requested.connect(self._show_recovery)
        self.tray.update_mode_label(
            self.config.get("push_to_talk", False),
            self.config.get("trigger_key", "ctrl+space")
        )
        self.tray.show()

        # Start the background server and hooks
        self._init_services()
        self._device_timer = QTimer(self)
        self._device_timer.setInterval(3000)
        self._device_timer.timeout.connect(lambda: self._refresh_audio_devices(force=False))
        self._device_timer.start()

    def _create_engine(self, config):
        if config.get("speech_engine", "phonon") == "whistle":
            return WhistleEngine(threads=config.get("engine_threads"))
        return PhononEngine(port=config.get("port", 8010), threads=config.get("engine_threads"),
                            model=config.get("phonon_model", "phonon-2.5-en"),
                            hotwords=config.get("recognition_terms", []))

    def _bind_engine(self, engine):
        engine.on_state_change = lambda ready, msg: (
            self.bridge.sig_engine_state.emit(engine, ready, msg) if self.engine is engine else None)

    def _boot_engine(self, engine):
        try:
            self.bridge.sig_status.emit(f"Loading {engine.name}...", False)
            engine.start_server(wait_timeout=120)
        except Exception as error:
            logger.error("Could not initialize %s: %s", engine.name, error)
            if not self._shutting_down and self.engine is engine:
                self.bridge.sig_notice.emit("Speech engine", f"{engine.name} could not load: {error}")
        finally:
            engine.start_watchdog()

    @Slot(int)
    def _on_engine_changed(self, generation):
        if generation == self._engine_selection_generation:
            self._engine_switching = False

    def _queue_engine_change(self):
        self._stop_dictation()
        self._engine_switching = True
        self._engine_selection_generation += 1
        self.hud.set_engine_ready(False, "Switching speech engine...")
        self._transcribe_queue.put((_ENGINE, self.config.copy(), self._engine_selection_generation))

    @Slot(float)
    def _on_mic_level(self, level):
        self.hud.set_volume(level)
        if self.dashboard is not None:
            self.dashboard.current_live_level = level

    def _init_services(self):
        """Starts the Phonon-2 server in background and engages hotkey hook."""
        self._device_refreshing = True
        def _open_mic():
            try:
                with self._device_lock:
                    self.audio.warm_up()
            except Exception as e:
                logger.error(f"Could not open the microphone: {e}")
                self.bridge.sig_notice.emit("Rio: microphone", f"Could not open the microphone: {e}")
            finally:
                self.bridge.sig_devices_changed.emit()

        threading.Thread(target=self._boot_engine, args=(self.engine,), daemon=True, name="EngineBootThread").start()
        threading.Thread(target=_open_mic, daemon=True, name="MicOpenThread").start()
        self.hotkey.start()

    @Slot(object, bool, str)
    def _on_engine_state(self, engine, ready: bool, message: str):
        if self._shutting_down or engine is not self.engine:
            return
        if ready:
            self.tray.set_status(f"{self.engine.name} Ready", is_active=True)
            key_name = self.config.get("trigger_key", "Ctrl+Space").upper()
            # Blooms the orb from grey to colour, with a matching "ready" chime
            self.hud.set_engine_ready(True, f"Ready • [{key_name}]")
            self.sounds.play_ready()
        else:
            self.tray.set_status(message, is_active=False)
            self.hud.set_engine_ready(False)
            if self._is_dictating:
                self._stop_dictation()

    @Slot(str, str)
    def _show_notice(self, title: str, message: str):
        # Throttled so a persistent problem doesn't spam balloons.
        now = time.time()
        if now - self._last_notice_time > 30:
            self._last_notice_time = now
            self.tray.show_message(title, message)

    def _on_pipeline_event(self, kind: str, message: str):
        """Called from the worker thread; marshals to the UI via signals."""
        title = ("Rio: transcript needs review" if kind == "revised" else
                 "Rio: focus changed" if kind == "focus" else
                 "Rio: can't type here" if kind == "blocked" else "Rio: speech engine")
        self.bridge.sig_notice.emit(title, message)

    @Slot()
    def _show_recovery(self):
        if not hasattr(self, "_recovery_dialog"):
            self._recovery_dialog = RecoveryDialog(self.pipeline.recovery)
        self._recovery_dialog.refresh()
        self._recovery_dialog.show()
        self._recovery_dialog.raise_()
        self._recovery_dialog.activateWindow()

    # ── Interactive Dictation Control (Hotkey or Orb Click) ──

    @Slot()
    def _toggle_dictation(self):
        """Called when user clicks the floating orb."""
        if self._is_dictating:
            self._stop_dictation()
        else:
            self._start_dictation()

    def _toggle_pause(self):
        """Called when user right-clicks the floating orb: Pause/Resume."""
        if not self._is_dictating:
            return
        if not self._is_paused:
            # Accept the final audio callback before disabling capture callbacks.
            self.audio.stop()
            self._is_paused = True
            self.hud.set_preview_text("")
            logger.info("⏸️ Dictation paused.")
            self.hud.set_state("paused")
            self.sounds.play_stop()
            self._transcribe_queue.put(_FLUSH)   # finish the sentence typed so far
        else:
            if self._device_refreshing or self.audio.needs_recovery:
                self.bridge.sig_notice.emit("Microphone", "Microphone is reconnecting. Resume once it is ready.")
                return
            self._session_generation += 1
            self._transcribe_queue.put((_RESET, self._session_generation))
            self._is_paused = False
            try:
                self.audio.start()
            except Exception as e:
                self._is_paused = True
                self.bridge.sig_notice.emit("Rio: microphone", str(e))
                return
            logger.info("▶️ Dictation resumed.")
            self.hud.set_state("listening")
            self.sounds.play_start()

    @Slot()
    def _start_dictation(self):
        """Starts dictation session."""
        if (self._shutting_down or self._device_refreshing
                or getattr(self, "_engine_switching", False) or not self.engine._is_ready):
            logger.warning("Engine not ready yet, ignoring dictation attempt.")
            return

        if self._is_dictating:
            return

        if self.audio.needs_recovery:
            self._refresh_audio_devices(force=True)
            self.bridge.sig_notice.emit("Microphone", "Reconnecting microphone. Start dictation again when ready.")
            return

        self._is_dictating = True
        self._is_paused = False
        self._session_start_time = time.time()
        self._session_generation += 1
        self._transcribe_queue.put((_RESET, self._session_generation))
        self.hud.set_preview_text("")
        try:
            self.audio.start()
        except Exception as e:
            self._is_dictating = False
            self._transcribe_queue.put(_FLUSH)
            self.bridge.sig_notice.emit("Rio: microphone", f"Could not start dictation: {e}")
            return
        logger.info("🎙️ Dictation started.")

        self.sounds.play_start()
        self.bridge.sig_started.emit()

    @Slot()
    def _stop_dictation(self):
        """Stops dictation session; speech still in flight is finalized and typed."""
        if not self._is_dictating:
            return

        # stop() synchronously emits any final utterance. Keep callbacks enabled until it returns.
        self.audio.stop()
        self._is_dictating = False
        self._is_paused = False
        self.hud.set_preview_text("")
        logger.info("⏹️ Dictation stopped. Finalizing remaining audio...")

        self.sounds.play_stop()
        self.bridge.sig_stopped.emit()

        # audio.stop() pushes any in-flight utterance onto the queue; the FLUSH marker that
        # follows makes the worker type the last sentence's closing punctuation.
        self._transcribe_queue.put(_FLUSH)

    # ── Utterance Handling & Sequential Worker ──

    @Slot(int, str)
    def _on_preview(self, generation: int, text: str):
        if (self.config.get("show_preview", False) and generation == self._session_generation
                and self._is_dictating and not self._is_paused and not self._shutting_down):
            self.hud.set_preview_text(text)

    def _on_live_ready(self, utt: Utterance):
        if (utt.utterance_id is None or not self._is_dictating or self._is_paused
                or not self.config.get("live_typing", True)):
            return
        utt.target_hwnd = self._utterance_hwnd
        utt.target_focus = getattr(self, "_utterance_focus", None)
        key = (self._session_generation, utt.utterance_id)
        with self._live_queue_lock:
            if utt.utterance_id <= self._live_closed_id:
                return
            queued = key in self._live_pending
            self._live_pending[key] = utt
            if not queued:
                self._transcribe_queue.put((_LIVE, key))

    def _post_live_result(self, ident, generation, text):
        with self._live_queue_lock:
            utt = self._stream_utt
            if ident <= self._live_closed_id or utt is None or utt.utterance_id != ident:
                return
            key = (generation, ident)
            queued = key in self._live_results
            self._live_results[key] = (utt, TranscribeResult(text=text))
            if not queued:
                self._transcribe_queue.put((_LIVE_RESULT, key))

    def _close_live_stream(self):
        stream, self._live_stream = self._live_stream, None
        self._stream_utt = None
        if stream is not None:
            try:
                stream.close()
            except Exception:
                logger.debug("Live transport close failed", exc_info=True)

    def _process_live_snapshot(self, utt):
        # A native socket runs its own rolling decoder over EVERY queued audio
        # block. Coalescing our snapshots cannot cancel that work, and final
        # recognition can wait behind tens of stale partials. Make one bounded
        # HTTP preview request at a time; final phrases remain FIFO and complete.
        fast = self.config.get("dictation_mode", "accurate") == "fast"
        if not fast and not self.config.get("show_preview", True):
            return
        key = (self._worker_generation, utt.utterance_id)
        now = time.monotonic()
        if key == getattr(self, "_preview_key", None) and now < self._preview_next_at:
            return
        if len(utt.pcm) < 32000:
            return
        self._preview_key = key
        pcm = utt.pcm if fast else utt.pcm[-8 * 32000:]
        result = self.engine.transcribe(pcm)
        elapsed = time.monotonic() - now
        self._preview_next_at = time.monotonic() + max(.65, 2 * elapsed)
        self.pipeline.process_live(utt, result)

    def _on_speech_activity(self, active: bool):
        if active:
            self._utterance_hwnd = get_foreground_hwnd()
            self._utterance_focus = focus_identity()
        self.pipeline.set_speech_active(active)

    def _on_utterance_ready(self, utt: Utterance):
        """Called from the audio thread when a spoken phrase has ended."""
        if not self._is_dictating or self._is_paused:
            return
        utt.target_hwnd = self._utterance_hwnd
        utt.target_focus = getattr(self, "_utterance_focus", None)
        if utt.utterance_id is not None and hasattr(self, "_live_queue_lock"):
            with self._live_queue_lock:
                self._live_closed_id = max(self._live_closed_id, utt.utterance_id)
                key = (self._session_generation, utt.utterance_id)
                self._live_pending.pop(key, None)
                self._live_results.pop(key, None)
        self._transcribe_queue.put(utt)

    def _on_speculative_ready(self, utt: Utterance, spec_id: int):
        """Called from the audio thread when an utterance has paused long enough for speculative pre-decode."""
        if not self._is_dictating or self._is_paused:
            return
        utt.target_hwnd = self._utterance_hwnd
        utt.target_focus = getattr(self, "_utterance_focus", None)
        self._transcribe_queue.put(_SpeculativeItem(utt, spec_id))

    def _on_cancel_speculative(self, spec_id: int):
        """Called from the audio thread when speaker resumed speaking, invalidating in-flight speculative decode."""
        self.pipeline.cancel_speculative(spec_id)
        self.bridge.sig_preview.emit(self._session_generation, "")

    def _transcribe_worker(self):
        """Single worker that processes utterances strictly in order.

        All the intelligence lives in DictationPipeline (speculative pre-decoding, filtering
        on VAD/SNR evidence, phrase stitching, deferred punctuation, injection). The short get()
        timeout lets the pipeline type a deferred closing mark once nothing has followed it.
        """
        while True:
            try:
                item = self._transcribe_queue.get(timeout=0.25)
            except queue.Empty:
                try:
                    self.pipeline.tick()
                except Exception as e:
                    logger.error(f"Error in pipeline tick: {e}", exc_info=True)
                continue

            if item is None:
                try:
                    self._close_live_stream()
                    self.engine.stop_server()
                finally:
                    stop_focus_tracking()
                    self.bridge.sig_shutdown_finished.emit()
                break
            try:
                if isinstance(item, tuple) and item[0] is _RESET:
                    self._close_live_stream()
                    self._worker_generation = item[1]
                    self.pipeline.reset()
                elif isinstance(item, tuple) and item[0] is _ENGINE:
                    try:
                        if item[2] != self._engine_selection_generation:
                            continue  # a newer selection supersedes this queued change
                        self._close_live_stream()
                        self.engine.stop_server()
                        self.pipeline.reset()
                        if not self._shutting_down:
                            self.engine = self._create_engine(item[1])
                            self.pipeline.engine = self.engine
                            self._bind_engine(self.engine)
                            self._boot_engine(self.engine)
                    finally:
                        self.bridge.sig_engine_changed.emit(item[2])
                elif item is _FLUSH:
                    self.pipeline.flush("session end")
                elif isinstance(item, tuple) and item[0] in (_LIVE, _LIVE_RESULT):
                    kind, key = item
                    with self._live_queue_lock:
                        source = self._live_pending if kind is _LIVE else self._live_results
                        live = source.pop(key, None)
                    if live is None or key[0] != self._worker_generation:
                        continue
                    if kind is _LIVE:
                        self._process_live_snapshot(live)
                    else:
                        self.pipeline.process_live(*live)
                elif isinstance(item, _SpeculativeItem):
                    self.pipeline.process_speculative(item.utt, item.spec_id)
                else:
                    self.pipeline.process(item)
            except Exception as e:
                logger.error(f"Error in transcribe worker: {e}", exc_info=True)

    # ── Dashboard & Orb Settings ──

    def _show_orb_context_menu(self, global_pos: QPoint):
        """Displays a sleek stealth dark context menu when right-clicking the orb."""
        menu = QMenu()
        menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        menu.setStyleSheet("""
            QMenu {
                background-color: #0E0E12;
                border: 1px solid #202026;
                border-radius: 8px;
                padding: 4px;
                color: #FFFFFF;
                font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, sans-serif;
                font-size: 12px;
            }
            QMenu::item {
                padding: 6px 14px 6px 10px;
                border-radius: 5px;
                margin: 1px 0;
            }
            QMenu::item:selected {
                background-color: #1C1C24;
                color: #FFFFFF;
            }
            QMenu::separator {
                height: 1px;
                background: #1E1E24;
                margin: 3px 4px;
            }
        """)

        # Dictate action
        if self._is_dictating:
            dictate_act = QAction(get_svg_icon("square", "#E05252", 14), "Stop Dictation", menu)
        else:
            dictate_act = QAction(get_svg_icon("mic", "#FFFFFF", 14), f"Start Dictation  [{self.config.get('trigger_key', 'ctrl+space').upper()}]", menu)
        dictate_act.triggered.connect(self._toggle_dictation)
        menu.addAction(dictate_act)

        # Pause action
        if self._is_paused:
            pause_act = QAction(get_svg_icon("play", "#0078D4", 14), "Resume Dictation", menu)
        else:
            pause_act = QAction(get_svg_icon("pause", "#8E8E98", 14), "Pause Dictation", menu)
        pause_act.triggered.connect(self._toggle_pause)
        menu.addAction(pause_act)

        menu.addSeparator()

        # Orb Settings Customizer
        orb_act = QAction(get_svg_icon("sliders", "#0078D4", 14), "Orb Appearance & Settings...", menu)
        orb_act.triggered.connect(self._open_orb_settings)
        menu.addAction(orb_act)

        # Full Dashboard
        dash_act = QAction(get_svg_icon("cpu", "#8E8E98", 14), "Speech Control Center...", menu)
        dash_act.triggered.connect(self._open_dashboard)
        menu.addAction(dash_act)

        menu.addSeparator()

        # Quit App
        quit_act = QAction(get_svg_icon("power", "#E05252", 14), "Quit Rio", menu)
        quit_act.triggered.connect(self.shutdown)
        menu.addAction(quit_act)

        menu.exec(global_pos)

    def _open_orb_settings(self):
        """Opens the Quick Orb Appearance & Dynamics Customizer."""
        dlg = OrbSettingsDialog(self.config)
        dlg.settings_changed.connect(self._on_orb_settings_changed)
        dlg.quit_app_requested.connect(self.shutdown)

        # Smart positioning near the floating orb
        hud_geo = self.hud.geometry()
        target_x = max(40, hud_geo.x() - dlg.width() + 40)
        target_y = max(40, hud_geo.y() - dlg.height() - 10)
        dlg.move(target_x, target_y)

        dlg.exec()

    def _on_orb_settings_changed(self, new_settings: dict):
        """Immediately applies and propagates new appearance settings to HUD."""
        self.config.update(new_settings)
        self.hud.apply_appearance_settings(self.config)

    def _open_dashboard(self):
        if self.dashboard is None:
            self.dashboard = DashboardWindow(self.config, self.history)
            self.dashboard.settings_saved.connect(self._apply_new_settings)
            self.dashboard.devices_refresh_requested.connect(self._refresh_audio_devices)
            self.dashboard.engine_retry_requested.connect(self._queue_engine_change)
            self.dashboard.capture_engine = self.audio

        self.dashboard.show()
        self.dashboard.raise_()
        self.dashboard.activateWindow()
        self.dashboard.update_capture_status()
        self._refresh_audio_devices(force=False)     # pick up a mic plugged in since startup (only acts if hardware changed)

    def _refresh_audio_devices(self, force: bool = True):
        """Re-scans audio hardware in the background (never blocks the UI)."""
        if self._shutting_down or self._device_refreshing:
            return
        if force and self._is_dictating:
            self._stop_dictation()
        self._device_refreshing = True
        threading.Thread(target=self._refresh_devices_worker, args=(force,), daemon=True, name="DeviceRefresh").start()

    def _refresh_devices_worker(self, force: bool):
        with self._device_lock:
            handoff = False
            try:
                if self._shutting_down:
                    return
                if not force:
                    fresh = devices.fresh_scan()
                    if fresh is None:
                        return
                    try:
                        cached = (devices.list_microphones(), devices.default_microphone_name())
                    except Exception:
                        cached = None  # a broken local PortAudio context also needs reinitialization
                    same = fresh == cached
                    if (same and not self.audio.needs_recovery
                            and self.audio.device_name == self.config.get("mic_name")):
                        return
                    logger.info("Audio hardware changed; re-scanning.")
                if self._is_dictating:
                    handoff = True
                    return
                self.audio.set_device(self.config.get("mic_name"))
            except Exception as e:
                logger.error(f"Could not reopen the microphone: {e}")
                self.bridge.sig_notice.emit("Rio: microphone", f"Could not open the microphone: {e}")
            finally:
                if handoff:
                    self.bridge.sig_refresh_required.emit()
                else:
                    self.bridge.sig_devices_changed.emit()

    @Slot()
    def _on_refresh_required(self):
        self._device_refreshing = False
        self._refresh_audio_devices(force=True)

    @Slot()
    def _on_devices_changed(self):
        self._device_refreshing = False
        if self.dashboard is not None:
            self.dashboard.populate_mics()
            self.dashboard.update_capture_status()
        if self.audio.fallback_reason:
            self.bridge.sig_notice.emit("Microphone changed", self.audio.fallback_reason)

    def _apply_new_settings(self, new_config: dict):
        self.config = new_config
        save_config(self.config)

        selected_engine = self.config.get("speech_engine", "phonon")
        phonon_settings = (self.config.get("phonon_model", "phonon-2.5-en"),
                           self.config.get("port", 8010), self.config.get("engine_threads"))
        if (selected_engine != self._applied_engine or
                (selected_engine == "phonon" and phonon_settings !=
                 getattr(self, "_applied_phonon_settings", phonon_settings))):
            self._applied_engine = selected_engine
            self._queue_engine_change()
        self._applied_phonon_settings = phonon_settings

        self.vocab.set_replacements(self.config.get("vocabulary", {}))
        if isinstance(self.engine, PhononEngine):
            self.engine.set_hotwords(self.config.get("recognition_terms", []))
        self.sounds.enabled = self.config.get("sound_effects", True)
        sound = (self.config.get("sound_theme", "glass"), self.config.get("sound_volume", 60))
        if sound != self._applied_sound:          # the dashboard edits the live dict, so compare with what is applied
            self._applied_sound = sound
            self.sounds.configure(theme=sound[0], volume=sound[1])
            self.sounds.play_start()              # let the user hear the change
        self.hotkey.set_trigger_key(self.config.get("trigger_key", "ctrl+space"))
        self.hotkey.set_mode(self.config.get("push_to_talk", False))
        self.history.enabled = self.config.get("save_history", True)
        self.audio.set_pause_ms(self.config.get("pause_ms", 700))
        self.audio.set_voice_gate(self.config.get("voice_gate", True))
        live = self.config.get("live_typing", True)
        mode = self.config.get("dictation_mode", "accurate")
        if live != self._applied_live or mode != getattr(self, "_applied_mode", "accurate"):
            self._stop_dictation()
            self._applied_live = live
            self._applied_mode = mode
        self.audio.set_live_enabled(live)
        self.audio.set_commit_partials(mode == "fast")
        if not self.config.get("show_preview", False):
            self.hud.set_preview_text("")
        # The dashboard edits the live config dict in place, so remember what is applied.
        mic = self.config.get("mic_name")
        if mic != self._applied_mic:
            self._applied_mic = mic
            if self._is_dictating:
                self._stop_dictation()
            # Opening a USB driver can block for seconds or fail. Route it through
            # the same background operation as hardware refresh, with UI errors.
            self._refresh_audio_devices(force=True)

        self.tray.update_mode_label(
            self.config.get("push_to_talk", False),
            self.config.get("trigger_key", "ctrl+space")
        )
        logger.info("Settings applied successfully.")

    def _toggle_mode(self):
        new_mode = not self.config.get("push_to_talk", False)
        self.config["push_to_talk"] = new_mode
        save_config(self.config)
        self.hotkey.set_mode(new_mode)
        self.tray.update_mode_label(new_mode, self.config.get("trigger_key", "ctrl+space"))

    def shutdown(self):
        if self._shutting_down:
            return
        logger.info("Shutting down application...")
        self._stop_dictation()
        self._shutting_down = True
        if hasattr(self, "_device_timer"):
            self._device_timer.stop()
        self.engine.request_stop()
        self.hotkey.stop()
        self.audio.close_stream()
        if self.dashboard is not None and self.dashboard.mic_testing:
            self.dashboard._toggle_mic_test()
        self.sounds.close()
        # The FIFO sentinel follows final speech and punctuation. Keep Qt responsive while draining.
        self._transcribe_queue.put(None)


def main():
    import signal
    import atexit

    # Assign distinct Windows Application User Model ID for dedicated taskbar grouping & icon
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Phonon-2 Dictation")
    except Exception:
        pass

    # Windows Single-Instance Named Mutex Protection
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    _app_mutex = kernel32.CreateMutexW(None, False, "Phonon2_Dictation_SingleInstance_Lock_2026")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        logger.warning("Another instance of Phonon-2 Dictation is already active. Exiting.")
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Rio")

    icon_path = os.path.join(os.path.dirname(__file__), "..", "assets", "icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    dictation_app = DictationApp(app)

    def _force_cleanup():
        try:
            dictation_app.hotkey.stop()
        except Exception:
            pass
        try:
            dictation_app.audio.close_stream()
        except Exception:
            pass
        try:
            dictation_app.engine.stop_server()
        except Exception:
            pass

    atexit.register(_force_cleanup)

    def _signal_handler(signum, frame):
        logger.info(f"Signal {signum} received, shutting down...")
        dictation_app.shutdown()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    from PySide6.QtCore import QTimer
    signal_timer = QTimer()
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start(200)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
