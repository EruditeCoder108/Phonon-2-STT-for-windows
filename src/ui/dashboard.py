"""
Windows 11 Stealth Dark Dashboard & Speech Control Center for Phonon-2.

Features:
- Pure stealth black / obsidian palette (#0C0C0E) with razor-sharp borders (#202026).
- 100% vector SVG icons with zero OS emojis.
- Intelligent scrollable, resize-aware cards avoiding text crushing or layout clipping.
- Real-time VU meter test, custom hotkey recorder, vocabulary editor, and searchable history.
"""

import os
import logging
from typing import Dict

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QCheckBox, QStackedWidget, QListWidget,
    QListWidgetItem, QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QProgressBar, QFrame, QScrollArea, QApplication, QSlider
)
from PySide6.QtCore import Qt, Signal, QTimer, QSize
from PySide6.QtGui import QFont, QColor, QIcon, QKeySequence, QKeyEvent

from src.config import get_autostart_registry, set_autostart_registry
from src.core.audio import AudioCaptureEngine
from src.core.history import HistoryManager
from src.core.vocabulary import VocabularyEngine
from src.ui.icons import get_svg_icon, get_svg_pixmap

logger = logging.getLogger(__name__)


def _make_label(text: str, role: str = "") -> QLabel:
    lbl = QLabel(text)
    if role:
        lbl.setProperty("class", role)
        lbl.setObjectName(role)
    return lbl


def _make_scrollable(content_widget: QWidget) -> QScrollArea:
    """Wraps a content widget in a smooth, responsive, frameless dark scroll container."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setStyleSheet("""
        QScrollArea {
            background-color: transparent;
            border: none;
        }
        QScrollBar:vertical {
            background: #0C0C0E;
            width: 8px;
            margin: 4px 2px 4px 2px;
            border-radius: 4px;
        }
        QScrollBar::handle:vertical {
            background: #2A2A32;
            min-height: 24px;
            border-radius: 4px;
        }
        QScrollBar::handle:vertical:hover {
            background: #3E3E4A;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
        }
    """)
    scroll.setWidget(content_widget)
    return scroll


# ── Interactive Keypress Capture Widget ──

class HotkeyRecorderButton(QPushButton):
    hotkey_recorded = Signal(str)

    def __init__(self, current_hotkey: str = "ctrl+space", parent=None):
        super().__init__(parent)
        self.current_hotkey = current_hotkey
        self.is_recording = False
        self._update_text()
        self.clicked.connect(self._start_recording)

    def _update_text(self):
        if self.is_recording:
            self.setIcon(get_svg_icon("keyboard", "#FFFFFF", 16))
            self.setText("  Press any key combination now...")
            self.setStyleSheet("""
                background-color: #0078D4;
                color: #FFFFFF;
                font-weight: 600;
                padding: 10px 14px;
                border-radius: 6px;
                border: 1px solid #1084D9;
                text-align: left;
            """)
        else:
            self.setIcon(get_svg_icon("keyboard", "#8E8E98", 16))
            self.setText(f"  Current: [{self.current_hotkey.upper()}]  —  Click to rebind")
            self.setStyleSheet("""
                background-color: #151518;
                color: #D4D4DC;
                padding: 10px 14px;
                border-radius: 6px;
                border: 1px solid #24242C;
                text-align: left;
                font-weight: 500;
            """)

    def _start_recording(self):
        self.is_recording = True
        self._update_text()
        self.setFocus()

    def keyPressEvent(self, event: QKeyEvent):
        if not self.is_recording:
            super().keyPressEvent(event)
            return

        key = event.key()
        modifiers = event.modifiers()

        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            return

        parts = []
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")

        if key == Qt.Key.Key_Space:
            parts.append("space")
        elif key == Qt.Key.Key_CapsLock:
            parts.append("capslock")
        elif Qt.Key.Key_F1 <= key <= Qt.Key.Key_F12:
            parts.append(f"f{key - Qt.Key.Key_F1 + 1}")
        elif key == Qt.Key.Key_Pause:
            parts.append("pause")
        else:
            text = event.text().lower()
            if text:
                parts.append(text)

        if parts:
            new_key = "+".join(parts)
            self.current_hotkey = new_key
            self.is_recording = False
            self._update_text()
            self.hotkey_recorded.emit(new_key)


# ── Main Fluent Dashboard Window ──

class DashboardWindow(QMainWindow):
    settings_saved = Signal(dict)

    def __init__(self, config: dict, history_manager: HistoryManager, parent=None):
        super().__init__(parent)
        self.config = config.copy()
        self.history = history_manager

        self.setWindowTitle("Phonon-2 Speech Control Center")
        self.resize(860, 580)
        self.setMinimumSize(760, 480)

        # Pure Stealth Dark Stylesheet
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0C0C0E;
            }
            QWidget#sidebar {
                background-color: #101014;
                border-right: 1px solid #1E1E24;
            }
            QListWidget#nav_list {
                background-color: transparent;
                border: none;
                outline: none;
                font-size: 13px;
                color: #8E8E98;
            }
            QListWidget#nav_list::item {
                padding: 10px 14px;
                border-radius: 6px;
                margin: 2px 6px;
            }
            QListWidget#nav_list::item:hover {
                background-color: #18181E;
                color: #FFFFFF;
            }
            QListWidget#nav_list::item:selected {
                background-color: #1C1C24;
                color: #FFFFFF;
                font-weight: 600;
                border-left: 3px solid #0078D4;
            }
            QWidget#content_area {
                background-color: #0C0C0E;
            }
            QFrame.card {
                background-color: #141418;
                border: 1px solid #202026;
                border-radius: 8px;
                padding: 14px 16px;
            }
            QLabel.title {
                font-size: 18px;
                font-weight: 700;
                color: #FFFFFF;
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
            }
            QLabel.subtitle {
                font-size: 12px;
                color: #727280;
                margin-bottom: 2px;
            }
            QLabel.section {
                font-size: 12px;
                font-weight: 600;
                color: #B4B4C0;
                margin-top: 2px;
            }
            QLabel.body {
                font-size: 12px;
                color: #8C8C9A;
            }
            QComboBox {
                background-color: #18181E;
                border: 1px solid #282832;
                border-radius: 6px;
                padding: 7px 10px;
                color: #FFFFFF;
                font-size: 12px;
            }
            QComboBox:hover {
                border-color: #383846;
            }
            QComboBox:focus {
                border-color: #0078D4;
            }
            QComboBox QAbstractItemView {
                background-color: #141418;
                border: 1px solid #282832;
                color: #FFFFFF;
                selection-background-color: #0078D4;
            }
            QLineEdit {
                background-color: #18181E;
                border: 1px solid #282832;
                border-radius: 6px;
                padding: 7px 10px;
                color: #FFFFFF;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #0078D4;
            }
            QPushButton.primary {
                background-color: #0078D4;
                color: #FFFFFF;
                font-weight: 600;
                padding: 8px 16px;
                border-radius: 6px;
                border: 1px solid #1084D9;
                font-size: 12px;
            }
            QPushButton.primary:hover {
                background-color: #1084D9;
            }
            QPushButton.secondary {
                background-color: #18181E;
                color: #D4D4DC;
                padding: 7px 14px;
                border-radius: 6px;
                border: 1px solid #282832;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton.secondary:hover {
                background-color: #22222A;
                border-color: #383846;
            }
            QProgressBar {
                background-color: #18181E;
                border: 1px solid #282832;
                border-radius: 3px;
                height: 8px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #0078D4;
                border-radius: 2px;
            }
            QTableWidget {
                background-color: #141418;
                border: 1px solid #202026;
                border-radius: 6px;
                color: #FFFFFF;
                gridline-color: #1C1C22;
            }
            QHeaderView::section {
                background-color: #18181E;
                color: #8E8E98;
                padding: 6px 10px;
                border: none;
                font-weight: 600;
                font-size: 11px;
            }
            QCheckBox {
                color: #B4B4C0;
                font-size: 12px;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid #33333E;
                background: #141418;
            }
            QCheckBox::indicator:checked {
                background-color: #0078D4;
                border-color: #0078D4;
            }
            QSlider::groove:horizontal {
                height: 5px;
                background: #1C1C22;
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #0078D4;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #FFFFFF;
                border: 1px solid #444450;
                width: 14px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 7px;
            }
        """)

        # Main Layout
        root = QWidget()
        self.setCentralWidget(root)
        main_layout = QHBoxLayout(root)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Sidebar Navigation ──
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(200)
        s_layout = QVBoxLayout(sidebar)
        s_layout.setContentsMargins(8, 16, 8, 16)
        s_layout.setSpacing(6)

        # Brand header
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(8, 4, 8, 8)
        brand_icon = QLabel()
        icon_path = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "icon.png")
        if os.path.exists(icon_path):
            brand_icon.setPixmap(QIcon(icon_path).pixmap(20, 20))
        else:
            brand_icon.setPixmap(get_svg_pixmap("mic", "#0078D4", 20))
        brand_title = QLabel("Phonon-2")
        brand_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #FFFFFF;")
        brand_row.addWidget(brand_icon)
        brand_row.addWidget(brand_title)
        brand_row.addStretch()
        s_layout.addLayout(brand_row)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("nav_list")

        items_spec = [
            ("Audio & Voice", "mic"),
            ("Hotkeys & Trigger", "keyboard"),
            ("Custom Vocabulary", "book"),
            ("Dictation History", "history"),
            ("Engine & Info", "cpu"),
        ]
        for title, icon_name in items_spec:
            item = QListWidgetItem(get_svg_icon(icon_name, "#A0A0AC", 16), title)
            self.nav_list.addItem(item)

        self.nav_list.setCurrentRow(0)
        self.nav_list.currentRowChanged.connect(self._on_tab_changed)
        s_layout.addWidget(self.nav_list)
        s_layout.addStretch()

        save_status_label = QLabel("Settings auto-saved")
        save_status_label.setStyleSheet("color: #4A4A56; font-size: 11px; padding-left: 10px; margin-bottom: 4px;")
        s_layout.addWidget(save_status_label)

        main_layout.addWidget(sidebar)

        # ── Content Stack ──
        self.stack = QStackedWidget()
        self.stack.setObjectName("content_area")

        self.tab_audio = self._build_audio_tab()
        self.tab_hotkeys = self._build_hotkey_tab()
        self.tab_vocab = self._build_vocab_tab()
        self.tab_history = self._build_history_tab()
        self.tab_about = self._build_about_tab()

        self.stack.addWidget(self.tab_audio)
        self.stack.addWidget(self.tab_hotkeys)
        self.stack.addWidget(self.tab_vocab)
        self.stack.addWidget(self.tab_history)
        self.stack.addWidget(self.tab_about)

        main_layout.addWidget(self.stack, 1)

        # Meter timer for live mic testing
        self.test_audio_engine = None
        self.vu_timer = QTimer(self)
        self.vu_timer.timeout.connect(self._update_live_vu)

    def _on_tab_changed(self, idx: int):
        self.stack.setCurrentIndex(idx)
        if idx == 3:  # History tab
            self._refresh_history_tab()

    # ── Tab 1: Audio & Microphone ──
    def _build_audio_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(_make_label("Audio & Speech Input", "title"))
        layout.addWidget(_make_label("Configure microphone capture, audio feedback cues, and speech filtering.", "subtitle"))

        # Card 1: Input Device & Live Test
        card_dev = QFrame()
        card_dev.setProperty("class", "card")
        c_layout = QVBoxLayout(card_dev)
        c_layout.setSpacing(10)

        c_layout.addWidget(_make_label("Input Microphone", "section"))
        self.mic_combo = QComboBox()
        self.mic_combo.addItem("Default System Microphone", -1)

        devices = AudioCaptureEngine.list_input_devices()
        curr_idx = self.config.get("mic_index", -1)
        select_pos = 0

        for pos, dev in enumerate(devices, start=1):
            self.mic_combo.addItem(f"{dev['name']}", dev["index"])
            if curr_idx is not None and dev["index"] == curr_idx:
                select_pos = pos

        self.mic_combo.setCurrentIndex(select_pos)
        self.mic_combo.currentIndexChanged.connect(self._save_audio_settings)
        c_layout.addWidget(self.mic_combo)

        # Real-time VU meter test
        meter_row = QHBoxLayout()
        meter_row.setSpacing(10)
        self.vu_meter = QProgressBar()
        self.vu_meter.setRange(0, 100)
        self.vu_meter.setValue(0)
        self.vu_meter.setFixedHeight(8)
        meter_row.addWidget(self.vu_meter, 1)

        self.test_mic_btn = QPushButton("Test Mic")
        self.test_mic_btn.setIcon(get_svg_icon("mic", "#FFFFFF", 14))
        self.test_mic_btn.setProperty("class", "secondary")
        self.test_mic_btn.setFixedHeight(30)
        self.test_mic_btn.clicked.connect(self._toggle_mic_test)
        meter_row.addWidget(self.test_mic_btn)
        c_layout.addLayout(meter_row)
        layout.addWidget(card_dev)

        # Card 2: Audio Cues & Sound
        card_snd = QFrame()
        card_snd.setProperty("class", "card")
        s_layout = QVBoxLayout(card_snd)
        s_layout.setSpacing(10)

        self.chime_check = QCheckBox("Play subtle sound cues on start, stop, and ready")
        self.chime_check.setChecked(self.config.get("sound_effects", True))
        self.chime_check.toggled.connect(self._save_audio_settings)
        s_layout.addWidget(self.chime_check)

        snd_row = QHBoxLayout()
        snd_row.setSpacing(12)

        col_theme = QVBoxLayout()
        col_theme.setSpacing(4)
        col_theme.addWidget(_make_label("Sound Style", "section"))
        self.sound_theme_combo = QComboBox()
        for label, key in (("Glass (Clear & Soft)", "glass"), ("Wood (Warm & Rounded)", "wood"), ("Air (Minimal)", "air")):
            self.sound_theme_combo.addItem(label, key)
        idx = self.sound_theme_combo.findData(self.config.get("sound_theme", "glass"))
        self.sound_theme_combo.setCurrentIndex(max(0, idx))
        self.sound_theme_combo.currentIndexChanged.connect(self._save_audio_settings)
        col_theme.addWidget(self.sound_theme_combo)
        snd_row.addLayout(col_theme, 1)

        col_vol = QVBoxLayout()
        col_vol.setSpacing(4)
        vol_hdr = QHBoxLayout()
        vol_hdr.addWidget(_make_label("Cue Volume", "section"))
        vol_hdr.addStretch()
        self.sound_volume_label = QLabel(f"{int(self.config.get('sound_volume', 60))}%")
        self.sound_volume_label.setStyleSheet("color: #0078D4; font-weight: 600; font-size: 11px;")
        vol_hdr.addWidget(self.sound_volume_label)
        col_vol.addLayout(vol_hdr)

        self.sound_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.sound_volume_slider.setRange(0, 100)
        self.sound_volume_slider.setValue(int(self.config.get("sound_volume", 60)))
        self.sound_volume_slider.valueChanged.connect(lambda v: self.sound_volume_label.setText(f"{v}%"))
        self.sound_volume_slider.sliderReleased.connect(self._save_audio_settings)
        col_vol.addWidget(self.sound_volume_slider)
        snd_row.addLayout(col_vol, 1)

        s_layout.addLayout(snd_row)
        layout.addWidget(card_snd)

        # Card 3: Speech Filters & Cadence
        card_filt = QFrame()
        card_filt.setProperty("class", "card")
        f_layout = QVBoxLayout(card_filt)
        f_layout.setSpacing(8)

        f_layout.addWidget(_make_label("Speech Filtering & Cadence", "section"))

        self.stitch_check = QCheckBox("Smart sentence joining (reconnect mid-sentence pauses)")
        self.stitch_check.setChecked(self.config.get("context_stitch", True))
        self.stitch_check.toggled.connect(self._save_audio_settings)
        f_layout.addWidget(self.stitch_check)

        self.gate_check = QCheckBox("Voice gate (ignore quiet background chatter && ambient noise)")
        self.gate_check.setChecked(self.config.get("voice_gate", True))
        self.gate_check.toggled.connect(self._save_audio_settings)
        f_layout.addWidget(self.gate_check)

        self.filler_check = QCheckBox("Strip filler vocalizations (\"uh\", \"um\") from typed text")
        self.filler_check.setChecked(self.config.get("remove_fillers", True))
        self.filler_check.toggled.connect(self._save_audio_settings)
        f_layout.addWidget(self.filler_check)

        pause_row = QHBoxLayout()
        pause_row.setSpacing(8)
        pause_lbl = QLabel("Phrase End Timeout:")
        pause_lbl.setStyleSheet("color: #B4B4C0; font-size: 12px;")
        pause_row.addWidget(pause_lbl)

        self.pause_combo = QComboBox()
        for label, ms in (("Quick — 500 ms (rapid typing)", 500),
                          ("Balanced — 700 ms (recommended)", 700),
                          ("Relaxed — 1000 ms (thoughtful speech)", 1000),
                          ("Patient — 1400 ms (long pauses)", 1400)):
            self.pause_combo.addItem(label, ms)
        cur_ms = self.config.get("pause_ms", 700)
        self.pause_combo.setCurrentIndex(min(range(self.pause_combo.count()),
                                             key=lambda i: abs(self.pause_combo.itemData(i) - cur_ms)))
        self.pause_combo.currentIndexChanged.connect(self._save_audio_settings)
        pause_row.addWidget(self.pause_combo, 1)
        f_layout.addLayout(pause_row)

        layout.addWidget(card_filt)

        # Card 4: General Preferences
        card_gen = QFrame()
        card_gen.setProperty("class", "card")
        g_layout = QVBoxLayout(card_gen)
        g_layout.setSpacing(8)

        self.startup_check = QCheckBox("Start Phonon-2 automatically on Windows startup (minimized)")
        self.startup_check.setChecked(get_autostart_registry())
        self.startup_check.toggled.connect(self._on_startup_toggled)
        g_layout.addWidget(self.startup_check)

        self.history_check = QCheckBox("Save dictated text to local history on this PC")
        self.history_check.setChecked(self.config.get("save_history", True))
        self.history_check.toggled.connect(self._save_audio_settings)
        g_layout.addWidget(self.history_check)

        layout.addWidget(card_gen)
        layout.addStretch()

        return _make_scrollable(container)

    def _on_startup_toggled(self, enabled: bool):
        set_autostart_registry(enabled)
        self.config["autostart"] = enabled
        self.settings_saved.emit(self.config)

    def _toggle_mic_test(self):
        if self.test_audio_engine is None:
            mic_data = self.mic_combo.currentData()
            mic_idx = None if mic_data == -1 else mic_data
            self.current_live_level = 0.0

            def _on_lvl(lvl):
                self.current_live_level = lvl

            self.test_audio_engine = AudioCaptureEngine(device_index=mic_idx, on_level_update=_on_lvl)
            self.test_audio_engine.start()
            self.vu_timer.start(30)
            self.test_mic_btn.setIcon(get_svg_icon("square", "#FFFFFF", 14))
            self.test_mic_btn.setText("Stop Test")
            self.test_mic_btn.setStyleSheet("background-color: #A32020; color: #FFFFFF; border-color: #C42B1C;")
        else:
            self.vu_timer.stop()
            self.test_audio_engine.stop()
            self.test_audio_engine.close_stream()
            self.test_audio_engine = None
            self.vu_meter.setValue(0)
            self.test_mic_btn.setIcon(get_svg_icon("mic", "#FFFFFF", 14))
            self.test_mic_btn.setText("Test Mic")
            self.test_mic_btn.setStyleSheet("")

    def _update_live_vu(self):
        val = int(getattr(self, "current_live_level", 0.0) * 100)
        self.vu_meter.setValue(min(100, val))

    def _save_audio_settings(self):
        mic_data = self.mic_combo.currentData()
        self.config["mic_index"] = None if mic_data == -1 else mic_data
        self.config["sound_effects"] = self.chime_check.isChecked()
        self.config["sound_theme"] = self.sound_theme_combo.currentData()
        self.config["sound_volume"] = self.sound_volume_slider.value()
        self.config["save_history"] = self.history_check.isChecked()
        self.config["context_stitch"] = self.stitch_check.isChecked()
        self.config["voice_gate"] = self.gate_check.isChecked()
        self.config["remove_fillers"] = self.filler_check.isChecked()
        self.config["pause_ms"] = self.pause_combo.currentData()
        self.settings_saved.emit(self.config)

    # ── Tab 2: Hotkeys & Trigger Mode ──
    def _build_hotkey_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(_make_label("Trigger Key & Activation Mode", "title"))
        layout.addWidget(_make_label("Configure global hotkeys and choose between push-to-talk or continuous toggle.", "subtitle"))

        card = QFrame()
        card.setProperty("class", "card")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(12)

        c_layout.addWidget(_make_label("Global Trigger Hotkey", "section"))
        self.recorder_btn = HotkeyRecorderButton(current_hotkey=self.config.get("trigger_key", "ctrl+space"))
        self.recorder_btn.hotkey_recorded.connect(self._on_hotkey_changed)
        c_layout.addWidget(self.recorder_btn)

        c_layout.addWidget(_make_label("Quick Presets", "section"))
        preset_layout = QHBoxLayout()
        preset_layout.setSpacing(8)
        for label, key_val in [("Ctrl + Space", "ctrl+space"), ("Caps Lock", "capslock"), ("Right Alt", "ralt"), ("F8", "f8")]:
            btn = QPushButton(label)
            btn.setProperty("class", "secondary")
            btn.clicked.connect(lambda _, k=key_val: self._on_hotkey_changed(k))
            preset_layout.addWidget(btn)
        c_layout.addLayout(preset_layout)

        c_layout.addWidget(_make_label("Activation Behavior", "section"))
        self.toggle_mode_btn = QPushButton("Toggle Mode  (Press once to start, press again to stop)")
        self.ptt_mode_btn = QPushButton("Push-to-Talk  (Hold hotkey while speaking, release to stop)")

        self.toggle_mode_btn.setProperty("class", "secondary")
        self.ptt_mode_btn.setProperty("class", "secondary")

        self.toggle_mode_btn.clicked.connect(lambda: self._set_mode(False))
        self.ptt_mode_btn.clicked.connect(lambda: self._set_mode(True))

        c_layout.addWidget(self.toggle_mode_btn)
        c_layout.addWidget(self.ptt_mode_btn)

        self._refresh_mode_buttons()
        layout.addWidget(card)
        layout.addStretch()

        return _make_scrollable(container)

    def _set_mode(self, is_ptt: bool):
        self.config["push_to_talk"] = is_ptt
        self._refresh_mode_buttons()
        self.settings_saved.emit(self.config)

    def _refresh_mode_buttons(self):
        is_ptt = self.config.get("push_to_talk", False)
        if is_ptt:
            self.ptt_mode_btn.setStyleSheet("background-color: #0078D4; color: #FFFFFF; font-weight: 600; border-color: #1084D9;")
            self.toggle_mode_btn.setStyleSheet("")
        else:
            self.toggle_mode_btn.setStyleSheet("background-color: #0078D4; color: #FFFFFF; font-weight: 600; border-color: #1084D9;")
            self.ptt_mode_btn.setStyleSheet("")

    def _on_hotkey_changed(self, new_key: str):
        self.config["trigger_key"] = new_key
        self.recorder_btn.current_hotkey = new_key
        self.recorder_btn._update_text()
        self.settings_saved.emit(self.config)

    # ── Tab 3: Custom Vocabulary ──
    def _build_vocab_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(_make_label("Custom Vocabulary Replacements", "title"))
        layout.addWidget(_make_label("Automatically rewrite spoken phonetic phrases into formatted names, brands, or shortcuts.", "subtitle"))

        add_card = QFrame()
        add_card.setProperty("class", "card")
        a_layout = QHBoxLayout(add_card)
        a_layout.setSpacing(8)

        self.vocab_input_spoken = QLineEdit()
        self.vocab_input_spoken.setPlaceholderText("Spoken phrase (e.g. 'dot com')")

        self.vocab_input_written = QLineEdit()
        self.vocab_input_written.setPlaceholderText("Replace with (e.g. '.com')")

        add_btn = QPushButton("Add Rule")
        add_btn.setIcon(get_svg_icon("plus", "#FFFFFF", 14))
        add_btn.setProperty("class", "primary")
        add_btn.clicked.connect(self._add_vocab_rule)

        a_layout.addWidget(self.vocab_input_spoken, 1)
        a_layout.addWidget(self.vocab_input_written, 1)
        a_layout.addWidget(add_btn)
        layout.addWidget(add_card)

        # Rules Table
        self.vocab_table = QTableWidget()
        self.vocab_table.setColumnCount(3)
        self.vocab_table.setHorizontalHeaderLabels(["When You Say", "Replace With", "Action"])
        self.vocab_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.vocab_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.vocab_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.vocab_table.setColumnWidth(2, 90)

        self._refresh_vocab_table()
        layout.addWidget(self.vocab_table)

        return container

    def _refresh_vocab_table(self):
        replacements = self.config.get("vocabulary", {})
        self.vocab_table.setRowCount(len(replacements))

        for row, (spoken, written) in enumerate(replacements.items()):
            self.vocab_table.setItem(row, 0, QTableWidgetItem(spoken))
            self.vocab_table.setItem(row, 1, QTableWidgetItem(written))

            del_btn = QPushButton("Delete")
            del_btn.setIcon(get_svg_icon("trash", "#E05252", 12))
            del_btn.setProperty("class", "secondary")
            del_btn.clicked.connect(lambda _, k=spoken: self._delete_vocab_rule(k))
            self.vocab_table.setCellWidget(row, 2, del_btn)

    def _add_vocab_rule(self):
        spoken = self.vocab_input_spoken.text().strip()
        written = self.vocab_input_written.text().strip()
        if not spoken or not written:
            return

        vocab = self.config.get("vocabulary", {})
        vocab[spoken] = written
        self.config["vocabulary"] = vocab

        self.vocab_input_spoken.clear()
        self.vocab_input_written.clear()
        self._refresh_vocab_table()
        self.settings_saved.emit(self.config)

    def _delete_vocab_rule(self, spoken: str):
        vocab = self.config.get("vocabulary", {})
        if spoken in vocab:
            del vocab[spoken]
            self.config["vocabulary"] = vocab
            self._refresh_vocab_table()
            self.settings_saved.emit(self.config)

    # ── Tab 4: Dictation History ──
    def _build_history_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        header_layout = QHBoxLayout()
        header_text = QVBoxLayout()
        header_text.addWidget(_make_label("Transcription History", "title"))
        header_text.addWidget(_make_label("Recent dictation snippets with one-click clipboard copy.", "subtitle"))
        header_layout.addLayout(header_text)
        header_layout.addStretch()

        clear_btn = QPushButton("Clear History")
        clear_btn.setIcon(get_svg_icon("trash", "#E05252", 14))
        clear_btn.setProperty("class", "secondary")
        clear_btn.clicked.connect(self._clear_history)
        header_layout.addWidget(clear_btn)
        layout.addLayout(header_layout)

        # Search Bar
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("Search past dictations...")
        self.history_search.textChanged.connect(self._filter_history)
        layout.addWidget(self.history_search)

        # Scrollable list area
        self.history_scroll = QScrollArea()
        self.history_scroll.setWidgetResizable(True)
        self.history_scroll.setStyleSheet("background-color: transparent; border: none;")

        self.history_list_widget = QWidget()
        self.history_list_layout = QVBoxLayout(self.history_list_widget)
        self.history_list_layout.setSpacing(8)
        self.history_scroll.setWidget(self.history_list_widget)

        layout.addWidget(self.history_scroll)
        self._refresh_history_tab()
        return container

    def _refresh_history_tab(self):
        while self.history_list_layout.count():
            child = self.history_list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        filter_text = self.history_search.text().lower() if hasattr(self, "history_search") else ""
        entries = self.history.get_all()

        matched = 0
        for entry in entries:
            text = entry.get("text", "")
            if filter_text and filter_text not in text.lower():
                continue

            card = QFrame()
            card.setProperty("class", "card")
            c_layout = QVBoxLayout(card)
            c_layout.setSpacing(6)

            meta_layout = QHBoxLayout()
            meta_label = QLabel(f"{entry.get('timestamp', '')}  •  {entry.get('words', 0)} words  •  {entry.get('duration', 0)}s")
            meta_label.setStyleSheet("color: #727280; font-size: 11px;")
            meta_layout.addWidget(meta_label)
            meta_layout.addStretch()

            copy_btn = QPushButton("Copy")
            copy_btn.setIcon(get_svg_icon("copy", "#D4D4DC", 12))
            copy_btn.setProperty("class", "secondary")
            copy_btn.setFixedWidth(80)
            copy_btn.clicked.connect(lambda _, t=text: self._copy_to_clipboard(t))
            meta_layout.addWidget(copy_btn)
            c_layout.addLayout(meta_layout)

            text_label = QLabel(text)
            text_label.setWordWrap(True)
            text_label.setStyleSheet("color: #FFFFFF; font-size: 13px; line-height: 1.4;")
            c_layout.addWidget(text_label)

            self.history_list_layout.addWidget(card)
            matched += 1

        if matched == 0:
            empty_label = QLabel("No dictation history found.")
            empty_label.setStyleSheet("color: #555562; font-size: 13px; text-align: center; margin-top: 30px;")
            self.history_list_layout.addWidget(empty_label)

        self.history_list_layout.addStretch()

    def _filter_history(self):
        self._refresh_history_tab()

    def _copy_to_clipboard(self, text: str):
        QApplication.clipboard().setText(text)

    def _clear_history(self):
        self.history.clear()
        self._refresh_history_tab()

    # ── Tab 5: Engine & Diagnostics ──
    def _build_about_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(_make_label("Engine & Architecture Diagnostics", "title"))
        layout.addWidget(_make_label("Local inference architecture, runtime acceleration, and daemon endpoints.", "subtitle"))

        info_card = QFrame()
        info_card.setProperty("class", "card")
        i_layout = QVBoxLayout(info_card)
        i_layout.setSpacing(10)

        items = [
            ("Speech Model Architecture", "Phonon-2 (Ternary Quantized Parakeet-TDT 0.6B)"),
            ("Model Weight Footprint", "164 MB (12 pinned quantized shards)"),
            ("Word Error Rate (WER)", "5.21% (Zero cloud telemetry)"),
            ("Hardware Acceleration", "Intel/AMD AVX-512 / AVX2 Vector Acceleration"),
            ("Execution Mode", "100% Offline Local Inference"),
            ("Local HTTP Daemon", f"http://127.0.0.1:{self.config.get('port', 8010)}"),
            ("Local WebSocket Stream", f"ws://127.0.0.1:{self.config.get('port', 8010)}/v1/audio/stream"),
        ]

        for title, val in items:
            row = QHBoxLayout()
            t_label = QLabel(title)
            t_label.setStyleSheet("color: #8E8E98; font-size: 12px; font-weight: 500;")
            v_label = QLabel(val)
            v_label.setStyleSheet("color: #FFFFFF; font-size: 12px; font-weight: 600;")
            row.addWidget(t_label)
            row.addStretch()
            row.addWidget(v_label)
            i_layout.addLayout(row)

        layout.addWidget(info_card)
        layout.addStretch()

        return _make_scrollable(container)
