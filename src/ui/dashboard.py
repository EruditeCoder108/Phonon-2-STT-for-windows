"""
Windows 11 Fluent Dashboard & Settings Control Center

A modern, multi-tab desktop control panel providing:
1. Dictation & Audio Setup (Device picker, Live VU meter, Sound cues)
2. Interactive Hotkey Recorder & Mode selector
3. Custom Vocabulary & Word Replacement Dictionary
4. Searchable Dictation History with 1-click clipboard copy
5. Local Engine & System diagnostics
"""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QCheckBox, QStackedWidget, QListWidget,
    QListWidgetItem, QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QProgressBar, QFrame, QScrollArea, QApplication
)
from PySide6.QtCore import Qt, Signal, QTimer, QSize
from PySide6.QtGui import QFont, QColor, QIcon, QKeySequence, QKeyEvent
import winreg
import sys
import os
import logging
from typing import Dict

from src.core.audio import AudioCaptureEngine
from src.core.history import HistoryManager
from src.core.vocabulary import VocabularyEngine

logger = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "Phonon2Dictation"


def _make_label(text: str, role: str = "") -> QLabel:
    lbl = QLabel(text)
    if role:
        lbl.setProperty("class", role)
        lbl.setObjectName(role)
    return lbl


def is_launch_on_startup() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except Exception:
        return False


def set_launch_on_startup(enable: bool):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                cmd = f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Error modifying startup registry: {e}")


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
            self.setText("⏺️ Press any key combo now...")
            self.setStyleSheet("background-color: #6C38E8; color: #FFFFFF; font-weight: bold; padding: 10px; border-radius: 6px;")
        else:
            self.setText(f"🎹 Current: [{self.current_hotkey.upper()}]  (Click to Change)")
            self.setStyleSheet("background-color: #212433; color: #E2E4F0; padding: 10px; border-radius: 6px; border: 1px solid #363A50;")

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

        # Ignore standalone modifier presses
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            return

        # Build key string
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
        self.resize(820, 560)
        self.setMinimumSize(780, 500)

        # Dark Fluent Theme Stylesheet
        self.setStyleSheet("""
            QMainWindow {
                background-color: #12131A;
            }
            QWidget#sidebar {
                background-color: #181924;
                border-right: 1px solid #252838;
            }
            QListWidget#nav_list {
                background-color: transparent;
                border: none;
                outline: none;
                font-size: 13px;
                color: #A0A5BA;
            }
            QListWidget#nav_list::item {
                padding: 12px 16px;
                border-radius: 6px;
                margin: 3px 8px;
            }
            QListWidget#nav_list::item:hover {
                background-color: #222434;
                color: #FFFFFF;
            }
            QListWidget#nav_list::item:selected {
                background-color: #5833C7;
                color: #FFFFFF;
                font-weight: bold;
            }
            QWidget#content_area {
                background-color: #12131A;
            }
            QFrame.card {
                background-color: #191B26;
                border: 1px solid #282B3E;
                border-radius: 10px;
                padding: 16px;
            }
            QLabel.title {
                font-size: 19px;
                font-weight: bold;
                color: #FFFFFF;
            }
            QLabel.subtitle {
                font-size: 12px;
                color: #8C92A8;
            }
            QLabel.section {
                font-size: 14px;
                font-weight: 600;
                color: #E0E3F0;
                margin-top: 4px;
            }
            QLabel.body {
                font-size: 12px;
                color: #A6ACBE;
            }
            QComboBox {
                background-color: #212433;
                border: 1px solid #33374C;
                border-radius: 6px;
                padding: 8px 12px;
                color: #FFFFFF;
                font-size: 13px;
            }
            QComboBox:hover {
                border-color: #6C38E8;
            }
            QComboBox QAbstractItemView {
                background-color: #1E202E;
                color: #FFFFFF;
                selection-background-color: #5833C7;
            }
            QLineEdit {
                background-color: #212433;
                border: 1px solid #33374C;
                border-radius: 6px;
                padding: 8px 12px;
                color: #FFFFFF;
                font-size: 13px;
            }
            QLineEdit:focus {
                border-color: #6C38E8;
            }
            QPushButton.primary {
                background-color: #6236DE;
                color: #FFFFFF;
                font-weight: 600;
                padding: 9px 18px;
                border-radius: 6px;
                border: none;
            }
            QPushButton.primary:hover {
                background-color: #7345F2;
            }
            QPushButton.secondary {
                background-color: #26293A;
                color: #D5D9E8;
                padding: 8px 16px;
                border-radius: 6px;
                border: 1px solid #3A3E56;
            }
            QPushButton.secondary:hover {
                background-color: #34384E;
            }
            QProgressBar {
                background-color: #212433;
                border: 1px solid #33374C;
                border-radius: 4px;
                height: 10px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #2ED573;
                border-radius: 3px;
            }
            QTableWidget {
                background-color: #191B26;
                border: 1px solid #282B3E;
                border-radius: 8px;
                color: #FFFFFF;
                gridline-color: #252838;
            }
            QHeaderView::section {
                background-color: #212433;
                color: #A0A5BA;
                padding: 6px;
                border: none;
                font-weight: bold;
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
        sidebar.setFixedWidth(210)
        s_layout = QVBoxLayout(sidebar)
        s_layout.setContentsMargins(10, 20, 10, 20)

        brand_title = QLabel("⚡ Phonon-2")
        brand_title.setStyleSheet("font-size: 17px; font-weight: bold; color: #FFFFFF; padding-left: 12px;")
        brand_sub = QLabel("Native Local Speech")
        brand_sub.setStyleSheet("font-size: 11px; color: #7B8196; padding-left: 12px; margin-bottom: 12px;")
        s_layout.addWidget(brand_title)
        s_layout.addWidget(brand_sub)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("nav_list")
        self.nav_list.addItem("🎙️  Audio & Voice")
        self.nav_list.addItem("⌨️  Hotkeys & Trigger")
        self.nav_list.addItem("📖  Custom Vocabulary")
        self.nav_list.addItem("📜  Dictation History")
        self.nav_list.addItem("⚡  Engine & About")
        self.nav_list.setCurrentRow(0)
        self.nav_list.currentRowChanged.connect(self._on_tab_changed)
        s_layout.addWidget(self.nav_list)
        s_layout.addStretch()

        save_status_label = QLabel("Auto-saved to config")
        save_status_label.setStyleSheet("color: #555A6E; font-size: 11px; text-align: center; padding-left: 12px;")
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
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(18)

        layout.addWidget(_make_label("Microphone & Audio Input", "title"))
        layout.addWidget(_make_label("Select your input microphone and test sensitivity in real time.", "subtitle"))

        card = QFrame()
        card.setProperty("class", "card")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(14)

        c_layout.addWidget(_make_label("Active Input Device:", "section"))
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

        # Real-time VU meter
        c_layout.addWidget(_make_label("Live Audio Level Test:", "section"))
        self.vu_meter = QProgressBar()
        self.vu_meter.setRange(0, 100)
        self.vu_meter.setValue(0)
        c_layout.addWidget(self.vu_meter)

        self.test_mic_btn = QPushButton("🎙️ Start Microphone Test")
        self.test_mic_btn.setProperty("class", "secondary")
        self.test_mic_btn.clicked.connect(self._toggle_mic_test)
        c_layout.addWidget(self.test_mic_btn)

        layout.addWidget(card)

        # Options card
        opt_card = QFrame()
        opt_card.setProperty("class", "card")
        opt_layout = QVBoxLayout(opt_card)
        opt_layout.setSpacing(10)

        self.chime_check = QCheckBox("Play pleasant audio cues when dictation starts and stops")
        self.chime_check.setChecked(self.config.get("sound_effects", True))
        self.chime_check.toggled.connect(self._save_audio_settings)
        opt_layout.addWidget(self.chime_check)

        self.startup_check = QCheckBox("Start Phonon-2 automatically on Windows startup (minimized)")
        self.startup_check.setChecked(is_launch_on_startup())
        self.startup_check.toggled.connect(lambda val: set_launch_on_startup(val))
        opt_layout.addWidget(self.startup_check)

        layout.addWidget(opt_card)
        layout.addStretch()
        return container

    def _toggle_mic_test(self):
        if self.test_audio_engine is None:
            # Start live test
            mic_data = self.mic_combo.currentData()
            mic_idx = None if mic_data == -1 else mic_data
            self.current_live_level = 0.0

            def _on_lvl(lvl):
                self.current_live_level = lvl

            self.test_audio_engine = AudioCaptureEngine(device_index=mic_idx, on_level_update=_on_lvl)
            self.test_audio_engine.start()
            self.vu_timer.start(30)
            self.test_mic_btn.setText("⏹️ Stop Microphone Test")
            self.test_mic_btn.setStyleSheet("background-color: #B33939; color: #FFFFFF;")
        else:
            # Stop test
            self.vu_timer.stop()
            self.test_audio_engine.stop()
            self.test_audio_engine.close_stream()
            self.test_audio_engine = None
            self.vu_meter.setValue(0)
            self.test_mic_btn.setText("🎙️ Start Microphone Test")
            self.test_mic_btn.setStyleSheet("")

    def _update_live_vu(self):
        val = int(getattr(self, "current_live_level", 0.0) * 100)
        self.vu_meter.setValue(min(100, val))

    def _save_audio_settings(self):
        mic_data = self.mic_combo.currentData()
        self.config["mic_index"] = None if mic_data == -1 else mic_data
        self.config["sound_effects"] = self.chime_check.isChecked()
        self.settings_saved.emit(self.config)

    # ── Tab 2: Hotkeys & Trigger Mode ──
    def _build_hotkey_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(18)

        layout.addWidget(_make_label("Trigger Key & Activation Mode", "title"))
        layout.addWidget(_make_label("Choose how dictation is triggered from any application.", "subtitle"))

        card = QFrame()
        card.setProperty("class", "card")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(14)

        c_layout.addWidget(_make_label("Global Trigger Hotkey:", "section"))
        self.recorder_btn = HotkeyRecorderButton(current_hotkey=self.config.get("trigger_key", "ctrl+space"))
        self.recorder_btn.hotkey_recorded.connect(self._on_hotkey_changed)
        c_layout.addWidget(self.recorder_btn)

        c_layout.addWidget(_make_label("Quick Presets:", "section"))
        preset_layout = QHBoxLayout()
        for label, key_val in [("Ctrl + Space", "ctrl+space"), ("Caps Lock", "capslock"), ("Right Alt", "ralt"), ("F8", "f8")]:
            btn = QPushButton(label)
            btn.setProperty("class", "secondary")
            btn.clicked.connect(lambda _, k=key_val: self._on_hotkey_changed(k))
            preset_layout.addWidget(btn)
        c_layout.addLayout(preset_layout)

        c_layout.addWidget(_make_label("Activation Mode:", "section"))
        self.toggle_mode_btn = QPushButton("Toggle Mode (Press once to start, press again to stop)")
        self.ptt_mode_btn = QPushButton("Push-to-Talk (Hold key down while speaking, release to stop)")

        self.toggle_mode_btn.setProperty("class", "secondary")
        self.ptt_mode_btn.setProperty("class", "secondary")

        self.toggle_mode_btn.clicked.connect(lambda: self._set_mode(False))
        self.ptt_mode_btn.clicked.connect(lambda: self._set_mode(True))

        c_layout.addWidget(self.toggle_mode_btn)
        c_layout.addWidget(self.ptt_mode_btn)

        self._refresh_mode_buttons()
        layout.addWidget(card)
        layout.addStretch()
        return container

    def _set_mode(self, is_ptt: bool):
        self.config["push_to_talk"] = is_ptt
        self._refresh_mode_buttons()
        self.settings_saved.emit(self.config)

    def _refresh_mode_buttons(self):
        is_ptt = self.config.get("push_to_talk", False)
        if is_ptt:
            self.ptt_mode_btn.setStyleSheet("background-color: #5833C7; color: #FFFFFF; font-weight: bold;")
            self.toggle_mode_btn.setStyleSheet("")
        else:
            self.toggle_mode_btn.setStyleSheet("background-color: #5833C7; color: #FFFFFF; font-weight: bold;")
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
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(14)

        layout.addWidget(_make_label("Custom Vocabulary & Replacements", "title"))
        layout.addWidget(_make_label("Automatically replace phonetic words with custom terms, names, or abbreviations.", "subtitle"))

        # Add rule bar
        add_card = QFrame()
        add_card.setProperty("class", "card")
        a_layout = QHBoxLayout(add_card)
        a_layout.setSpacing(10)

        self.vocab_input_spoken = QLineEdit()
        self.vocab_input_spoken.setPlaceholderText("When you say (e.g. 'hi fa')")

        self.vocab_input_written = QLineEdit()
        self.vocab_input_written.setPlaceholderText("Replace with (e.g. 'HIFA')")

        add_btn = QPushButton("➕ Add Rule")
        add_btn.setProperty("class", "primary")
        add_btn.clicked.connect(self._add_vocab_rule)

        a_layout.addWidget(self.vocab_input_spoken, 1)
        a_layout.addWidget(self.vocab_input_written, 1)
        a_layout.addWidget(add_btn)
        layout.addWidget(add_card)

        # Rules Table
        self.vocab_table = QTableWidget()
        self.vocab_table.setColumnCount(3)
        self.vocab_table.setHorizontalHeaderLabels(["Spoken Phrase", "Replaced With", "Action"])
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
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(14)

        header_layout = QHBoxLayout()
        header_text = QVBoxLayout()
        header_text.addWidget(_make_label("Transcription History", "title"))
        header_text.addWidget(_make_label("Recent dictation snippets with one-click clipboard copy.", "subtitle"))
        header_layout.addLayout(header_text)
        header_layout.addStretch()

        clear_btn = QPushButton("🗑️ Clear History")
        clear_btn.setProperty("class", "secondary")
        clear_btn.clicked.connect(self._clear_history)
        header_layout.addWidget(clear_btn)
        layout.addLayout(header_layout)

        # Search Bar
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("🔍 Search past dictations...")
        self.history_search.textChanged.connect(self._filter_history)
        layout.addWidget(self.history_search)

        # Scrollable list area
        self.history_scroll = QScrollArea()
        self.history_scroll.setWidgetResizable(True)
        self.history_scroll.setStyleSheet("background-color: transparent; border: none;")

        self.history_list_widget = QWidget()
        self.history_list_layout = QVBoxLayout(self.history_list_widget)
        self.history_list_layout.setSpacing(10)
        self.history_scroll.setWidget(self.history_list_widget)

        layout.addWidget(self.history_scroll)
        self._refresh_history_tab()
        return container

    def _refresh_history_tab(self):
        # Clear existing items
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
            c_layout.setSpacing(8)

            meta_layout = QHBoxLayout()
            meta_label = QLabel(f"⏱️ {entry.get('timestamp', '')}  •  {entry.get('words', 0)} words  •  {entry.get('duration', 0)}s")
            meta_label.setStyleSheet("color: #7B8196; font-size: 11px;")
            meta_layout.addWidget(meta_label)
            meta_layout.addStretch()

            copy_btn = QPushButton("📋 Copy")
            copy_btn.setProperty("class", "secondary")
            copy_btn.setFixedWidth(75)
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
            empty_label.setStyleSheet("color: #6C7286; font-size: 13px; text-align: center; margin-top: 30px;")
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
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(16)

        layout.addWidget(_make_label("Phonon-2 Speech Engine Diagnostics", "title"))
        layout.addWidget(_make_label("System information and local inference architecture.", "subtitle"))

        info_card = QFrame()
        info_card.setProperty("class", "card")
        i_layout = QVBoxLayout(info_card)
        i_layout.setSpacing(12)

        items = [
            ("Model Architecture", "Phonon-2 (Ternary Quantized Parakeet-TDT 0.6B)"),
            ("Model Download Footprint", "164 MB (12 pinned weight shards)"),
            ("Word Error Rate (WER)", "5.21% (State of the art in <1GB tier)"),
            ("CPU Acceleration Tier", "Intel/AMD AVX-512 VNNI Vector Acceleration"),
            ("Execution Mode", "100% Offline Local Inference (Zero telemetry)"),
            ("Local Daemon", f"http://127.0.0.1:{self.config.get('port', 8010)}"),
            ("Streaming Protocol", f"WebSocket ws://127.0.0.1:{self.config.get('port', 8010)}/v1/audio/stream"),
        ]

        for title, val in items:
            row = QHBoxLayout()
            t_label = QLabel(title)
            t_label.setStyleSheet("color: #A0A5BA; font-size: 13px; font-weight: 500;")
            v_label = QLabel(val)
            v_label.setStyleSheet("color: #FFFFFF; font-size: 13px; font-weight: 600;")
            row.addWidget(t_label)
            row.addStretch()
            row.addWidget(v_label)
            i_layout.addLayout(row)

        layout.addWidget(info_card)
        layout.addStretch()
        return container
