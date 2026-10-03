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
from PySide6.QtCore import QObject, Qt, Signal, Slot, QPoint
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
from src.core.audio import AudioCaptureEngine
from src.core.segmenter import Utterance
from src.core.hotkey import GlobalHotkeyManager
from src.core.engine import PhononEngine
from src.core.pipeline import DictationPipeline
from src.core.sound_effects import SoundEffects
from src.core.vocabulary import VocabularyEngine
from src.core.history import HistoryManager
from src.ui.hud import CircularOrbHUD
from src.ui.tray import SystemTray
from src.ui.dashboard import DashboardWindow
from src.ui.orb_settings_dialog import OrbSettingsDialog
from src.ui.icons import get_svg_icon

_FLUSH = object()   # queue marker: finish the current sentence (type any deferred punctuation)


class AppBridge(QObject):
    """Thread-safe bridge between background threads and Qt UI."""
    sig_started = Signal()
    sig_volume = Signal(float)
    sig_stopped = Signal()
    sig_engine_state = Signal(bool, str)      # (ready, message)
    sig_status = Signal(str, bool)            # tray status text, active
    sig_notice = Signal(str, str)             # tray balloon: title, message


class DictationApp:
    def __init__(self, q_app: QApplication):
        self.q_app = q_app
        self.config = load_config()
        self.bridge = AppBridge()

        # Data & Core Features
        self.history = HistoryManager(enabled=self.config.get("save_history", True))
        self.vocab = VocabularyEngine(self.config.get("vocabulary", {}))
        self._is_dictating = False
        self._is_paused = False
        self._session_start_time = 0.0
        self._last_notice_time = 0.0
        self._applied_mic = self.config.get("mic_index")

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
        self.bridge.sig_volume.connect(self.hud.set_volume)
        self.bridge.sig_stopped.connect(lambda: self.hud.set_state("idle"))
        self.bridge.sig_engine_state.connect(self._on_engine_state)
        self.bridge.sig_status.connect(self.tray.set_status)
        self.bridge.sig_notice.connect(self._show_notice)

        # Core Engines
        self.engine = PhononEngine(
            port=self.config.get("port", 8010),
            threads=self.config.get("engine_threads"),
        )
        self.engine.on_state_change = lambda ready, msg: self.bridge.sig_engine_state.emit(ready, msg)
        self.sounds = SoundEffects(
            enabled=self.config.get("sound_effects", True),
            theme=self.config.get("sound_theme", "glass"),
            volume=self.config.get("sound_volume", 60),
        )
        self._applied_sound = (self.config.get("sound_theme", "glass"), self.config.get("sound_volume", 60))
        self.pipeline = DictationPipeline(
            engine=self.engine,
            vocab=self.vocab,
            inject=lambda text: inject_text(text, self.config.get("injection_mode", "auto")),
            history=self.history,
            get_config=lambda key, default=None: self.config.get(key, default),
            get_hwnd=get_foreground_hwnd,
            on_event=self._on_pipeline_event,
        )
        self.audio = AudioCaptureEngine(
            device_index=self.config.get("mic_index"),
            on_utterance=self._on_utterance_ready,
            on_level_update=lambda lvl: self.bridge.sig_volume.emit(lvl),
            on_activity=self.pipeline.set_speech_active,
            pause_ms=self.config.get("pause_ms", 700),
            voice_gate=self.config.get("voice_gate", True),
        )
        self.hotkey = GlobalHotkeyManager(
            trigger_key=self.config.get("trigger_key", "ctrl+space"),
            push_to_talk=self.config.get("push_to_talk", False),
            on_start=self._start_dictation,
            on_stop=self._stop_dictation,
            is_active=lambda: self._is_dictating,
        )
        self._worker_thread.start()

        # Connect Tray actions
        self.tray.orb_settings_requested.connect(self._open_orb_settings)
        self.tray.settings_requested.connect(self._open_dashboard)
        self.tray.toggle_mode_requested.connect(self._toggle_mode)
        self.tray.quit_requested.connect(self.shutdown)
        self.tray.update_mode_label(
            self.config.get("push_to_talk", False),
            self.config.get("trigger_key", "ctrl+space")
        )
        self.tray.show()

        # Start the background server and hooks
        self._init_services()

    def _init_services(self):
        """Starts the Phonon-2 server in background and engages hotkey hook."""
        def _boot():
            try:
                self.bridge.sig_status.emit("Loading Phonon-2...", False)
                self.engine.start_server(wait_timeout=120)
                self.engine.start_watchdog()
            except Exception as e:
                logger.error(f"Failed to initialize engine: {e}")
                self.bridge.sig_status.emit("Engine Error", False)

        threading.Thread(target=_boot, daemon=True, name="EngineBootThread").start()
        self.audio.warm_up()
        self.hotkey.start()

    @Slot(bool, str)
    def _on_engine_state(self, ready: bool, message: str):
        if ready:
            self.tray.set_status("Phonon-2 Ready", is_active=True)
            key_name = self.config.get("trigger_key", "Ctrl+Space").upper()
            # Blooms the orb from grey to colour, with a matching "ready" chime
            self.hud.set_engine_ready(True, f"Ready • [{key_name}]")
            self.sounds.play_ready()
        else:
            self.tray.set_status("Speech engine restarting...", is_active=False)
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
        title = "Phonon-2: can't type here" if kind == "blocked" else "Phonon-2 error"
        self.bridge.sig_notice.emit(title, message)

    # ── Interactive Dictation Control (Hotkey or Orb Click) ──

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
        self._is_paused = not self._is_paused
        if self._is_paused:
            logger.info("⏸️ Dictation paused.")
            self.hud.set_state("paused")
            self.sounds.play_stop()
            self._transcribe_queue.put(_FLUSH)   # finish the sentence typed so far
        else:
            logger.info("▶️ Dictation resumed.")
            self.hud.set_state("listening")
            self.sounds.play_start()

    def _start_dictation(self):
        """Starts dictation session."""
        if not self.engine._is_ready:
            logger.warning("Engine not ready yet, ignoring dictation attempt.")
            return

        if self._is_dictating:
            return

        self._is_dictating = True
        self._is_paused = False
        self._session_start_time = time.time()
        self.pipeline.reset()
        logger.info("🎙️ Dictation started.")

        self.sounds.play_start()
        self.bridge.sig_started.emit()
        self.audio.start()

    def _stop_dictation(self):
        """Stops dictation session; speech still in flight is finalized and typed."""
        if not self._is_dictating:
            return

        self._is_dictating = False
        self._is_paused = False
        logger.info("⏹️ Dictation stopped. Finalizing remaining audio...")

        self.sounds.play_stop()
        self.bridge.sig_stopped.emit()

        # audio.stop() pushes any in-flight utterance onto the queue; the FLUSH marker that
        # follows makes the worker type the last sentence's closing punctuation.
        self.audio.stop()
        self._transcribe_queue.put(_FLUSH)

    # ── Utterance Handling & Sequential Worker ──

    def _on_utterance_ready(self, utt: Utterance):
        """Called from the audio thread when a spoken phrase has ended."""
        if not self._is_dictating or self._is_paused:
            return
        self._transcribe_queue.put(utt)

    def _transcribe_worker(self):
        """Single worker that processes utterances strictly in order.

        All the intelligence lives in DictationPipeline (filtering on VAD/SNR evidence,
        phrase stitching, deferred punctuation, injection). The short get() timeout lets the
        pipeline type a deferred closing mark once nothing has followed it.
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
                break
            try:
                if item is _FLUSH:
                    self.pipeline.flush("session end")
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
            dictate_act = QAction(get_svg_icon("mic", "#FFFFFF", 14), "Start Dictation  [Ctrl+Space]", menu)
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
        quit_act = QAction(get_svg_icon("power", "#E05252", 14), "Quit Phonon-2", menu)
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

        self.dashboard.show()
        self.dashboard.raise_()
        self.dashboard.activateWindow()

    def _apply_new_settings(self, new_config: dict):
        self.config = new_config
        save_config(self.config)

        self.vocab.set_replacements(self.config.get("vocabulary", {}))
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
        # The dashboard edits the live config dict in place, so remember what is applied.
        mic = self.config.get("mic_index")
        if mic != self._applied_mic:
            self._applied_mic = mic
            if self._is_dictating:
                self._stop_dictation()
            self.audio.set_device(mic)

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
        logger.info("Shutting down application...")
        try:
            self._transcribe_queue.put_nowait(None)
        except Exception:
            pass
        self.hotkey.stop()
        self.audio.close_stream()
        self.sounds.close()
        self.engine.stop_server()
        self.q_app.quit()


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
    app.setApplicationName("Phonon-2 Dictation")

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
