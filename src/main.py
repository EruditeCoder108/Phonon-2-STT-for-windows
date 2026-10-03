"""
Phonon-2 Windows Native Dictation Application

Orchestrates:
1. Local Phonon-2 speech engine server (AVX-512 VNNI accelerated)
2. Global Ctrl+Space toggle keyboard hook (with debouncing & repeat suppression)
3. Hot-mic WASAPI capture with:
   - Dynamic Adaptive Noise Floor (room ambient noise filtering)
   - Continuous Micro-Cadence Segmentation (words stream every 1.2-1.8s)
4. Dedicated FIFO transcription worker ensuring 100% chronological, non-dropped text
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
from PySide6.QtCore import QObject, Signal, Slot, QPoint
from PySide6.QtGui import QIcon, QAction
import ctypes

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("PhononApp")

# Internal modules
from src.config import load_config, save_config
from src.core.injector import inject_text, type_unicode_chars
from src.core.audio import AudioCaptureEngine
from src.core.hotkey import GlobalHotkeyManager
from src.core.engine import PhononEngine
from src.core.sound_effects import SoundEffects
from src.core.vocabulary import VocabularyEngine
from src.core.history import HistoryManager
from src.ui.hud import CircularOrbHUD
from src.ui.tray import SystemTray
from src.ui.dashboard import DashboardWindow
from src.ui.orb_settings_dialog import OrbSettingsDialog


class AppBridge(QObject):
    """Thread-safe bridge between Win32 background threads and Qt UI."""
    sig_started = Signal()
    sig_volume = Signal(float)
    sig_stopped = Signal()
    sig_engine_ready = Signal()


class DictationApp:
    def __init__(self, q_app: QApplication):
        self.q_app = q_app
        self.config = load_config()
        self.bridge = AppBridge()

        # Data & Core Features
        self.history = HistoryManager()
        self.vocab = VocabularyEngine(self.config.get("vocabulary", {}))
        self._is_dictating = False
        self._session_start_time = 0.0

        # Dedicated FIFO Transcription Queue & Worker Thread
        self._transcribe_queue: queue.Queue = queue.Queue()
        self._worker_thread = threading.Thread(
            target=self._transcribe_worker,
            daemon=True,
            name="SequentialTranscribeWorker"
        )
        self._worker_thread.start()

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
        self.bridge.sig_engine_ready.connect(self._on_engine_ready)

        # Core Engines
        self.engine = PhononEngine(port=self.config.get("port", 8010))
        self.sounds = SoundEffects(enabled=self.config.get("sound_effects", True))
        self.audio = AudioCaptureEngine(
            device_index=self.config.get("mic_index"),
            on_utterance=self._on_audio_chunk_ready,
            on_level_update=lambda lvl: self.bridge.sig_volume.emit(lvl),
        )
        self.hotkey = GlobalHotkeyManager(
            trigger_key=self.config.get("trigger_key", "ctrl+space"),
            push_to_talk=self.config.get("push_to_talk", False),
            on_start=self._start_dictation,
            on_stop=self._stop_dictation,
        )

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
                self.tray.set_status("Loading Phonon-2...", is_active=False)
                self.engine.start_server(wait_timeout=120)
                self.bridge.sig_engine_ready.emit()
            except Exception as e:
                logger.error(f"Failed to initialize engine: {e}")
                self.tray.set_status("Engine Error", is_active=False)

        threading.Thread(target=_boot, daemon=True, name="EngineBootThread").start()
        self.audio.warm_up()
        self.hotkey.start()

    @Slot()
    def _on_engine_ready(self):
        self.tray.set_status("Phonon-2 Ready", is_active=True)
        key_name = self.config.get("trigger_key", "Ctrl+Space").upper()
        # Blooms orb from grey to color — no sound, the visual bloom is the feedback
        self.hud.set_engine_ready(True, f"Ready • [{key_name}]")

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
        self._is_paused = not getattr(self, "_is_paused", False)
        if self._is_paused:
            logger.info("⏸️ Dictation paused.")
            self.hud.set_state("paused")
            self.sounds.play_stop()
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
        logger.info("🎙️ Dictation started.")

        self.sounds.play_start()
        self.bridge.sig_started.emit()
        self.audio.start()

    def _stop_dictation(self):
        """Stops dictation session and finalizes any remaining tail audio."""
        if not self._is_dictating:
            return

        self._is_dictating = False
        self._is_paused = False
        logger.info("⏹️ Dictation stopped. Finalizing remaining audio...")

        self.sounds.play_stop()
        self.bridge.sig_stopped.emit()

        # Stop audio capture and queue any remaining tail PCM bytes
        tail_bytes = self.audio.stop()
        if tail_bytes and len(tail_bytes) >= 1600:  # > 0.1s
            self._transcribe_queue.put(tail_bytes)

    # ── Continuous Chunk Handling & Sequential Worker ──

    def _on_audio_chunk_ready(self, pcm_bytes: bytes):
        """Called as you speak when a natural micro-pause or cadence is reached."""
        if not self._is_dictating or getattr(self, "_is_paused", False):
            return
        # Queue chunk for strict sequential FIFO transcription
        self._transcribe_queue.put(pcm_bytes)

    def _transcribe_worker(self):
        """Dedicated background worker that serializes transcription in exact chronological order.

        Three noise filters protect against false positives:
          A) Duration gate  — clips under MIN_AUDIO_SEC skipped (almost always noise).
          B) Energy gate    — clips with RMS below MIN_RMS_ENERGY skipped (near-silence).
          C) Two-tier word filter:
               HARD_BLOCK — pure filler/noise sounds (um, uh, hmm…). Always suppressed.
               SOFT_BLOCK — real words you might genuinely say (yeah, okay, yes…).
                            Only suppressed when the clip is also low-energy OR very short,
                            meaning it almost certainly came from noise, not intentional speech.
                            If you clearly say "yeah", it goes through.
        """
        import numpy as np

        # ── Tuneable thresholds ─────────────────────────────────────────────────
        MIN_AUDIO_SEC  = 0.35   # (A) clips shorter than this are skipped
        MIN_RMS_ENERGY = 350    # (B) 16-bit PCM RMS floor; raise if noise still leaks

        # (C-i) Always suppressed — these are never real dictation words
        HARD_BLOCK = {
            "um", "uh", "hmm", "hm", "ah", "mm",
            "mhm", "mm-hmm", "erm", "er",
        }

        # (C-ii) Suppressed ONLY when clip is also low-energy or very short.
        # If you actually say these clearly they pass through normally.
        SOFT_BLOCK = {
            "yeah", "yep", "yes", "no", "nope",
            "ok", "okay", "alright", "all right",
            "right", "sure", "like", "so", "well",
            "but", "and", "or", "oh", "the", "a", "an",
            "i", "you", "bye", "thanks", "thank you", "thank",
        }
        # A soft-block word is suppressed when energy is below this OR clip is very short.
        # Think of it as: "did the person actually intend to say this?"
        SOFT_BLOCK_RMS_THRESHOLD  = 700   # below this = probably noise, not speech
        SOFT_BLOCK_DUR_THRESHOLD  = 0.55  # below this = probably not an intentional word
        # ────────────────────────────────────────────────────────────────────────

        while True:
            try:
                pcm_bytes = self._transcribe_queue.get()
                if pcm_bytes is None:
                    break

                # ── A: Duration gate ──────────────────────────────────────────
                duration_sec = len(pcm_bytes) / 32000.0   # 16kHz × 2 bytes
                if duration_sec < MIN_AUDIO_SEC:
                    logger.debug(f"🔇 Skipped (too short: {duration_sec:.2f}s)")
                    self._transcribe_queue.task_done()
                    continue

                # ── B: Energy gate ────────────────────────────────────────────
                audio_arr = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
                rms = float(np.sqrt(np.mean(audio_arr ** 2))) if len(audio_arr) > 0 else 0.0
                if rms < MIN_RMS_ENERGY:
                    logger.debug(f"🔇 Skipped (low energy RMS={rms:.0f})")
                    self._transcribe_queue.task_done()
                    continue

                # ── Transcribe ─────────────────────────────────────────────────
                t0 = time.time()
                raw_text = self.engine.transcribe_wav_bytes(pcm_bytes)
                elapsed = time.time() - t0

                if raw_text and raw_text.strip():
                    # ── C: Two-tier word filter ───────────────────────────────
                    clean = raw_text.strip().lower().rstrip(".,!?")

                    # Hard block — always noise, never real words
                    if clean in HARD_BLOCK:
                        logger.debug(f"🔇 Hard-blocked: '{raw_text.strip()}'")
                        self._transcribe_queue.task_done()
                        continue

                    # Soft block — only suppress if the audio looked like noise too
                    if clean in SOFT_BLOCK:
                        looks_like_noise = (
                            rms < SOFT_BLOCK_RMS_THRESHOLD or
                            duration_sec < SOFT_BLOCK_DUR_THRESHOLD
                        )
                        if looks_like_noise:
                            logger.debug(
                                f"🔇 Soft-blocked (noise-level audio RMS={rms:.0f} "
                                f"dur={duration_sec:.2f}s): '{raw_text.strip()}'"
                            )
                            self._transcribe_queue.task_done()
                            continue
                        # Energy and duration look intentional — let it through
                        logger.debug(f"✅ Soft-block passed (RMS={rms:.0f} dur={duration_sec:.2f}s): '{clean}'")

                    # Apply custom vocabulary replacements
                    text = self.vocab.apply(raw_text.strip())
                    logger.info(f"✨ Transcribed in {elapsed:.2f}s (RMS={rms:.0f}): '{text}'")

                    # Inject directly into the active field at cursor
                    inject_text(text + " ", prefer_paste=self.config.get("prefer_paste", True))

                    # Log to history
                    self.history.add_entry(text, duration_sec=duration_sec)

                self._transcribe_queue.task_done()
            except Exception as e:
                logger.error(f"Error in transcribe worker: {e}", exc_info=True)


    # ── Dashboard & Orb Settings ──

    def _show_orb_context_menu(self, global_pos: QPoint):
        """Displays an ultra-modern Fluent dark context menu when right-clicking the orb."""
        menu = QMenu()
        menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        menu.setStyleSheet("""
            QMenu {
                background-color: rgba(24, 24, 32, 0.96);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 12px;
                padding: 6px;
                color: #f0f0f5;
                font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, sans-serif;
                font-size: 13px;
            }
            QMenu::item {
                padding: 7px 22px 7px 12px;
                border-radius: 6px;
                margin: 2px 0;
            }
            QMenu::item:selected {
                background-color: rgba(0, 180, 216, 0.22);
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background: rgba(255, 255, 255, 0.08);
                margin: 4px 6px;
            }
        """)

        # Dictate action
        dictate_label = "⏹ Stop Dictation" if self._is_dictating else "🎙 Start Dictation  [Ctrl+Space]"
        dictate_act = QAction(dictate_label, menu)
        dictate_act.triggered.connect(self._toggle_dictation)
        menu.addAction(dictate_act)

        # Pause action
        pause_label = "▶ Resume Dictation" if self._is_paused else "⏸ Pause Dictation"
        pause_act = QAction(pause_label, menu)
        pause_act.triggered.connect(self._toggle_pause)
        menu.addAction(pause_act)

        menu.addSeparator()

        # Orb Settings Customizer
        orb_act = QAction("🔮 Orb Appearance && Settings...", menu)
        orb_act.triggered.connect(self._open_orb_settings)
        menu.addAction(orb_act)

        # Full Dashboard
        dash_act = QAction("⚡ History && Full Settings...", menu)
        dash_act.triggered.connect(self._open_dashboard)
        menu.addAction(dash_act)

        menu.addSeparator()

        # Quit App
        quit_act = QAction("✕ Close / Quit Phonon-2", menu)
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
        self.hotkey.set_trigger_key(self.config.get("trigger_key", "ctrl+space"))
        self.hotkey.set_mode(self.config.get("push_to_talk", False))
        self.audio.set_device(self.config.get("mic_index"))

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
        self.hotkey.stop()
        self.audio.close_stream()
        self.engine.stop_server()
        try:
            self._transcribe_queue.put_nowait(None)
        except Exception:
            pass
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
