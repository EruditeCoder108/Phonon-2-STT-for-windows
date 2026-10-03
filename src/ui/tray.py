"""
Windows System Tray Icon and Menu

Provides a background presence in the Windows taskbar system tray with:
- Status indicators (Engine Ready / Listening / Offline)
- Quick toggle between Push-to-Talk and Toggle mode
- Direct access to Settings and Exit
"""

from PySide6.QtWidgets import QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QAction
from PySide6.QtCore import Qt, Signal, QObject
import logging

logger = logging.getLogger(__name__)


from src.ui.icons import get_svg_icon


def create_tray_icon_pixmap(color: QColor = QColor(0, 120, 212)) -> QPixmap:
    """Dynamically generates a clean, modern high-DPI tray icon."""
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Circular background badge
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 24, 24, 7, 7)

    # Stylized soundwave bars in the center
    painter.setBrush(QColor(255, 255, 255))
    painter.drawRoundedRect(9, 13, 2.5, 6, 1, 1)
    painter.drawRoundedRect(13, 9, 2.5, 14, 1, 1)
    painter.drawRoundedRect(17, 11, 2.5, 10, 1, 1)
    painter.drawRoundedRect(21, 14, 2.5, 4, 1, 1)

    painter.end()
    return pixmap


class SystemTray(QObject):
    settings_requested = Signal()
    orb_settings_requested = Signal()
    toggle_mode_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tray_icon = QSystemTrayIcon(parent)

        import os
        icon_path = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "icon.png")
        if os.path.exists(icon_path):
            self.tray_icon.setIcon(QIcon(icon_path))
        else:
            self.icon_pixmap = create_tray_icon_pixmap()
            self.tray_icon.setIcon(QIcon(self.icon_pixmap))

        self.tray_icon.setToolTip("Phonon-2 System Dictation")

        self.menu = QMenu()
        self.menu.setStyleSheet("""
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
        self._build_menu()
        self.tray_icon.setContextMenu(self.menu)
        self.tray_icon.activated.connect(self._on_activated)

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.settings_requested.emit()

    def _build_menu(self):
        self.menu.clear()

        # Status item (disabled, informational)
        self.status_action = QAction("Phonon-2 Ready", self.menu)
        self.status_action.setIcon(get_svg_icon("check", "#2ED573", 14))
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)

        self.menu.addSeparator()

        self.orb_action = QAction("Orb Appearance & Settings...", self.menu)
        self.orb_action.setIcon(get_svg_icon("sliders", "#0078D4", 14))
        self.orb_action.triggered.connect(self.orb_settings_requested.emit)
        self.menu.addAction(self.orb_action)

        self.dashboard_action = QAction("Speech Control Center...", self.menu)
        self.dashboard_action.setIcon(get_svg_icon("cpu", "#8E8E98", 14))
        self.dashboard_action.triggered.connect(self.settings_requested.emit)
        self.menu.addAction(self.dashboard_action)

        self.mode_action = QAction("Mode: Push-to-Talk", self.menu)
        self.mode_action.setIcon(get_svg_icon("keyboard", "#8E8E98", 14))
        self.mode_action.triggered.connect(self.toggle_mode_requested.emit)
        self.menu.addAction(self.mode_action)

        self.menu.addSeparator()

        self.quit_action = QAction("Quit Phonon-2", self.menu)
        self.quit_action.setIcon(get_svg_icon("power", "#E05252", 14))
        self.quit_action.triggered.connect(self.quit_requested.emit)
        self.menu.addAction(self.quit_action)

    def set_status(self, text: str, is_active: bool = True):
        color = "#2ED573" if is_active else "#8E8E98"
        icon_name = "check" if is_active else "clock"
        self.status_action.setIcon(get_svg_icon(icon_name, color, 14))
        self.status_action.setText(text)

    def update_mode_label(self, is_push_to_talk: bool, key_name: str):
        mode_str = "Push-to-Talk" if is_push_to_talk else "Toggle"
        self.mode_action.setText(f"Mode: {mode_str} ({key_name.upper()})")

    def show(self):
        self.tray_icon.show()

    def show_message(self, title: str, message: str):
        self.tray_icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 2000)
