"""
Preferences & Settings Dialog

Allows customization of:
- Trigger Hotkey (Caps Lock, Right Alt, F8, etc.)
- Dictation Mode (Push-to-Talk vs Toggle)
- Input Microphone selection
- Text Injection method (Clipboard paste vs Direct Unicode typing)
- Windows Startup Toggle
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QCheckBox, QPushButton, QGroupBox, QWidget
)
from PySide6.QtCore import Qt, Signal
import winreg
import sys
import os
import logging
from src.core.audio import AudioCaptureEngine

logger = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "Phonon2Dictation"


def is_launch_on_startup() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except FileNotFoundError:
        return False
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
                except FileNotFoundError:
                    pass
    except Exception as e:
        logger.error(f"Error modifying startup registry: {e}")


class SettingsDialog(QDialog):
    settings_saved = Signal(dict)

    def __init__(self, current_settings: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Phonon-2 Dictation Settings")
        self.setFixedSize(460, 420)
        self.setStyleSheet("""
            QDialog {
                background-color: #16181F;
                color: #E2E4EC;
                font-family: 'Segoe UI', sans-serif;
            }
            QGroupBox {
                border: 1px solid #2B2E3D;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 16px;
                font-weight: bold;
                color: #A3A7BA;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 4px;
            }
            QLabel {
                color: #D2D4DE;
                font-size: 13px;
            }
            QComboBox {
                background-color: #212430;
                border: 1px solid #32364A;
                border-radius: 6px;
                padding: 6px 10px;
                color: #FFFFFF;
                font-size: 13px;
            }
            QComboBox:hover {
                border: 1px solid #7451EB;
            }
            QComboBox QAbstractItemView {
                background-color: #212430;
                border: 1px solid #32364A;
                color: #FFFFFF;
                selection-background-color: #6C38E8;
            }
            QCheckBox {
                color: #D2D4DE;
                font-size: 13px;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 1px solid #3E4256;
                background-color: #212430;
            }
            QCheckBox::indicator:checked {
                background-color: #7451EB;
                border: 1px solid #7451EB;
            }
            QPushButton {
                background-color: #6C38E8;
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 8px 18px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #7E4DF2;
            }
            QPushButton#cancelBtn {
                background-color: #2A2D3C;
                color: #D2D4DE;
            }
            QPushButton#cancelBtn:hover {
                background-color: #383C50;
            }
        """)

        self.settings = current_settings.copy()
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # 1. Hotkey & Mode Group
        hotkey_group = QGroupBox("Trigger & Activation Mode")
        hotkey_layout = QVBoxLayout(hotkey_group)
        hotkey_layout.setSpacing(10)

        # Trigger key
        k_layout = QHBoxLayout()
        k_label = QLabel("Trigger Key:")
        self.key_combo = QComboBox()
        self.key_combo.addItems([
            "Ctrl+Space (Recommended)",
            "Caps Lock",
            "Right Alt",
            "Left Alt",
            "F8",
            "F9",
            "Pause",
            "Space"
        ])
        k_layout.addWidget(k_label)
        k_layout.addWidget(self.key_combo, 1)
        hotkey_layout.addLayout(k_layout)

        # Push to talk vs Toggle
        self.ptt_check = QCheckBox("Push-to-Talk (Hold key to dictate, uncheck for Toggle mode)")
        self.ptt_check.setChecked(self.settings.get("push_to_talk", False))
        hotkey_layout.addWidget(self.ptt_check)

        layout.addWidget(hotkey_group)

        # 2. Audio Input Group
        audio_group = QGroupBox("Audio Input")
        audio_layout = QVBoxLayout(audio_group)

        m_layout = QHBoxLayout()
        m_label = QLabel("Microphone:")
        self.mic_combo = QComboBox()
        self.mic_combo.addItem("Default System Microphone", -1)

        devices = AudioCaptureEngine.list_input_devices()
        current_idx = self.settings.get("mic_index", -1)
        select_pos = 0

        for i, dev in enumerate(devices):
            display_name = dev["name"]
            self.mic_combo.addItem(display_name, dev["index"])
            if dev["index"] == current_idx:
                select_pos = i + 1

        self.mic_combo.setCurrentIndex(select_pos)
        m_layout.addWidget(m_label)
        m_layout.addWidget(self.mic_combo, 1)
        audio_layout.addLayout(m_layout)

        layout.addWidget(audio_group)

        # 3. Behavior Group
        behavior_group = QGroupBox("Behavior & System Integration")
        behavior_layout = QVBoxLayout(behavior_group)
        behavior_layout.setSpacing(10)

        self.paste_check = QCheckBox("Fast Clipboard Paste (Recommended, transparently restores clipboard)")
        self.paste_check.setChecked(self.settings.get("prefer_paste", True))
        behavior_layout.addWidget(self.paste_check)

        self.startup_check = QCheckBox("Launch Phonon-2 on Windows startup")
        self.startup_check.setChecked(is_launch_on_startup())
        behavior_layout.addWidget(self.startup_check)

        layout.addWidget(behavior_group)

        # Select matching hotkey
        curr_key = self.settings.get("trigger_key", "capslock").lower()
        key_map_rev = {
            "ctrl+space": 0,
            "capslock": 1,
            "ralt": 2,
            "lalt": 3,
            "f8": 4,
            "f9": 5,
            "pause": 6,
            "space": 7,
        }
        self.key_combo.setCurrentIndex(key_map_rev.get(curr_key, 0))

        # Bottom Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("cancelBtn")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Save Preferences")
        save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(save_btn)

        layout.addLayout(btn_layout)

    def _on_save(self):
        key_lookup = ["ctrl+space", "capslock", "ralt", "lalt", "f8", "f9", "pause", "space"]
        selected_key = key_lookup[self.key_combo.currentIndex()]

        mic_data = self.mic_combo.currentData()
        mic_idx = None if mic_data == -1 else mic_data

        self.settings["trigger_key"] = selected_key
        self.settings["push_to_talk"] = self.ptt_check.isChecked()
        self.settings["mic_index"] = mic_idx
        self.settings["prefer_paste"] = self.paste_check.isChecked()

        # Update Windows startup setting
        set_launch_on_startup(self.startup_check.isChecked())

        self.settings_saved.emit(self.settings)
        self.accept()
