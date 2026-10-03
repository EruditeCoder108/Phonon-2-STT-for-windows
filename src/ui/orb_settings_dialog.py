"""
Ultra-Modern Glassmorphic Orb Customizer & Settings Dialog

Features:
- Frameless Fluent Design with rounded corners, translucent acrylic dark surface, and glow accents.
- Visual Glowing Color Swatches (Rainbow Chroma, Ice Cyan, Solar Amber, Emerald Green, Royal Violet, Custom Hue).
- Segmented Control for Animation Style (Liquid Wave vs Calm Solid Glow).
- Segmented Control for Voice Scale Reactivity (Off, Subtle, Normal, High).
- Windows Autostart toggle switch.
- Prominent "Quit App" button to easily exit the application anytime.
- Real-time live updating: clicking any swatch or toggle immediately updates the live desktop orb!
"""

import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QWidget, QSlider, QCheckBox, QGraphicsDropShadowEffect
)
from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QLinearGradient, QBrush, QPen, QMouseEvent

from src.config import save_config, set_autostart_registry, get_autostart_registry


class ColorSwatch(QPushButton):
    """Visual circular color swatch with glowing active ring."""
    def __init__(self, key: str, title: str, brush: QBrush, parent=None):
        super().__init__(parent)
        self.key = key
        self.setToolTip(title)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 40)
        self.brush = brush

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(4, 4, -4, -4)

        # Draw color circle
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.brush)
        painter.drawEllipse(rect)

        # Draw glowing ring if selected
        if self.isChecked():
            pen = QPen(QColor(255, 255, 255), 2.5)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))


class SegmentedPill(QPushButton):
    """Modern iOS / Windows 11 style segmented toggle pill."""
    def __init__(self, text: str, value: str, parent=None):
        super().__init__(text, parent)
        self.value = value
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(32)
        self.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #a0a0b2;
                border: none;
                border-radius: 6px;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 10px;
            }
            QPushButton:hover {
                color: #ffffff;
                background-color: rgba(255, 255, 255, 0.05);
            }
            QPushButton:checked {
                background-color: #0078d4;
                color: #ffffff;
            }
        """)


class OrbSettingsDialog(QDialog):
    settings_changed = Signal(dict)
    quit_app_requested = Signal()

    def __init__(self, current_config: dict, parent=None):
        super().__init__(parent)
        self.config = dict(current_config)

        # ── Frameless Glassmorphic Dialog ──
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(380, 520)

        self._drag_pos = None
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        # Card container with rounded border and acrylic dark glass
        card = QFrame()
        card.setObjectName("mainCard")
        card.setStyleSheet("""
            QFrame#mainCard {
                background-color: rgba(18, 18, 24, 0.96);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 18px;
            }
            QLabel {
                color: #dcdce6;
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ff0055, stop:0.17 #ff9900, stop:0.33 #00f076,
                    stop:0.50 #00d2ff, stop:0.67 #0066ff, stop:0.83 #aa00ff, stop:1 #ff0055);
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                border: 2px solid #00d2ff;
                width: 16px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 8px;
            }
            QCheckBox {
                color: #dcdce6;
                font-size: 12px;
                font-weight: 500;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 1px solid rgba(255, 255, 255, 0.2);
                background: rgba(255, 255, 255, 0.05);
            }
            QCheckBox::indicator:checked {
                background-color: #00d2ff;
                border-color: #00d2ff;
            }
        """)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)

        # ── 1. Header with Close Button ──
        header = QHBoxLayout()
        header.setSpacing(10)

        # Mini icon
        icon_lbl = QLabel()
        icon_path = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "icon.png")
        if os.path.exists(icon_path):
            icon_lbl.setPixmap(QIcon(icon_path).pixmap(24, 24))
        header.addWidget(icon_lbl)

        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("Orb Customizer")
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #ffffff;")
        sub = QLabel("Personalize colors, animations, and controls")
        sub.setStyleSheet("font-size: 11px; color: #8e8ea0;")
        title_box.addWidget(title)
        title_box.addWidget(sub)
        header.addLayout(title_box)
        header.addStretch()

        # Modern circular close button (✕)
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.07);
                color: #a0a0b0;
                border-radius: 14px;
                font-size: 13px;
                border: none;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(255, 80, 80, 0.85);
                color: #ffffff;
            }
        """)
        close_btn.clicked.connect(self.reject)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # ── 2. Visual Color Theme Swatches ──
        c_title = QLabel("COLOR THEME")
        c_title.setStyleSheet("font-size: 10px; font-weight: bold; color: #707085; letter-spacing: 1px;")
        layout.addWidget(c_title)

        swatch_row = QHBoxLayout()
        swatch_row.setSpacing(12)

        # Create gradient brushes for each swatch
        rainbow_grad = QLinearGradient(0, 0, 40, 40)
        rainbow_grad.setColorAt(0.0, QColor("#ff0077"))
        rainbow_grad.setColorAt(0.35, QColor("#ffaa00"))
        rainbow_grad.setColorAt(0.65, QColor("#00d2ff"))
        rainbow_grad.setColorAt(1.0, QColor("#9900ff"))

        cyan_grad = QLinearGradient(0, 0, 40, 40)
        cyan_grad.setColorAt(0.0, QColor("#00d2ff"))
        cyan_grad.setColorAt(1.0, QColor("#0055ff"))

        gold_grad = QLinearGradient(0, 0, 40, 40)
        gold_grad.setColorAt(0.0, QColor("#ffbb33"))
        gold_grad.setColorAt(1.0, QColor("#cc4400"))

        emerald_grad = QLinearGradient(0, 0, 40, 40)
        emerald_grad.setColorAt(0.0, QColor("#00f076"))
        emerald_grad.setColorAt(1.0, QColor("#007733"))

        violet_grad = QLinearGradient(0, 0, 40, 40)
        violet_grad.setColorAt(0.0, QColor("#d946ef"))
        violet_grad.setColorAt(1.0, QColor("#4c1d95"))

        custom_grad = QLinearGradient(0, 0, 40, 40)
        custom_grad.setColorAt(0.0, QColor("#ffffff"))
        custom_grad.setColorAt(0.5, QColor("#aaaaaa"))
        custom_grad.setColorAt(1.0, QColor("#555555"))

        self.swatches = [
            ColorSwatch("rainbow", "Rainbow Chroma Flow", QBrush(rainbow_grad)),
            ColorSwatch("cyan_blue", "Electric Cyan & Ice Blue", QBrush(cyan_grad)),
            ColorSwatch("gold_fire", "Solar Amber & Gold", QBrush(gold_grad)),
            ColorSwatch("emerald", "Emerald Neon Green", QBrush(emerald_grad)),
            ColorSwatch("violet", "Royal Violet & Cyberpunk", QBrush(violet_grad)),
            ColorSwatch("custom", "Custom Color", QBrush(custom_grad)),
        ]

        active_theme = self.config.get("color_theme", "rainbow")
        for sw in self.swatches:
            sw.setChecked(sw.key == active_theme)
            sw.clicked.connect(lambda _, s=sw: self._on_swatch_clicked(s))
            swatch_row.addWidget(sw)

        layout.addLayout(swatch_row)

        # Custom Hue Slider (Visible when custom swatch is selected)
        self.slider_box = QWidget()
        s_layout = QVBoxLayout(self.slider_box)
        s_layout.setContentsMargins(0, 0, 0, 0)
        s_layout.setSpacing(4)

        self.hue_slider = QSlider(Qt.Orientation.Horizontal)
        self.hue_slider.setRange(0, 360)
        self.hue_slider.setValue(self.config.get("custom_hue", 195))
        self.hue_slider.valueChanged.connect(self._on_hue_slider_changed)
        s_layout.addWidget(self.hue_slider)

        layout.addWidget(self.slider_box)
        self.slider_box.setVisible(active_theme == "custom")

        # ── 3. Animation Style (Segmented) ──
        s_title = QLabel("ANIMATION STYLE")
        s_title.setStyleSheet("font-size: 10px; font-weight: bold; color: #707085; letter-spacing: 1px;")
        layout.addWidget(s_title)

        style_seg = QFrame()
        style_seg.setStyleSheet("background: rgba(255, 255, 255, 0.05); border-radius: 8px; padding: 2px;")
        style_layout = QHBoxLayout(style_seg)
        style_layout.setContentsMargins(2, 2, 2, 2)
        style_layout.setSpacing(4)

        self.btn_liquid = SegmentedPill("🌊 Dynamic Liquid Molten", "liquid")
        self.btn_glow = SegmentedPill("✨ Calm Solid Glow", "glow_only")

        cur_style = self.config.get("animation_style", "liquid")
        self.btn_liquid.setChecked(cur_style == "liquid")
        self.btn_glow.setChecked(cur_style == "glow_only")

        self.btn_liquid.clicked.connect(lambda: self._set_style("liquid"))
        self.btn_glow.clicked.connect(lambda: self._set_style("glow_only"))

        style_layout.addWidget(self.btn_liquid)
        style_layout.addWidget(self.btn_glow)
        layout.addWidget(style_seg)

        # ── 4. Voice Size Reactivity (Segmented) ──
        r_title = QLabel("VOICE SIZE REACTIVITY")
        r_title.setStyleSheet("font-size: 10px; font-weight: bold; color: #707085; letter-spacing: 1px;")
        layout.addWidget(r_title)

        react_seg = QFrame()
        react_seg.setStyleSheet("background: rgba(255, 255, 255, 0.05); border-radius: 8px; padding: 2px;")
        react_layout = QHBoxLayout(react_seg)
        react_layout.setContentsMargins(2, 2, 2, 2)
        react_layout.setSpacing(4)

        self.react_btns = [
            SegmentedPill("Off", "none"),
            SegmentedPill("Subtle (~10%)", "subtle"),
            SegmentedPill("Normal (~20%)", "normal"),
            SegmentedPill("High (~35%)", "high"),
        ]

        cur_react = self.config.get("scale_reactivity", "normal")
        for b in self.react_btns:
            b.setChecked(b.value == cur_react)
            b.clicked.connect(lambda _, btn=b: self._set_reactivity(btn.value))
            react_layout.addWidget(b)

        layout.addWidget(react_seg)

        # ── 5. Start with Windows Checkbox Card ──
        card_auto = QFrame()
        card_auto.setStyleSheet("""
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.07);
            border-radius: 10px;
            padding: 8px 12px;
        """)
        card_auto_layout = QHBoxLayout(card_auto)
        card_auto_layout.setContentsMargins(6, 4, 6, 4)

        self.autostart_cb = QCheckBox("Start silently with Windows")
        self.autostart_cb.setChecked(get_autostart_registry() or self.config.get("autostart", False))
        card_auto_layout.addWidget(self.autostart_cb)
        layout.addWidget(card_auto)

        layout.addStretch()

        # ── 6. Bottom Action Bar (Quit App Button + Save Button) ──
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(12)

        # Prominent Quit Application Button
        quit_btn = QPushButton("✕ Quit App")
        quit_btn.setFixedHeight(36)
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 60, 60, 0.15);
                color: #ff6666;
                border: 1px solid rgba(255, 80, 80, 0.35);
                border-radius: 8px;
                font-weight: 600;
                font-size: 12px;
                padding: 0 16px;
            }
            QPushButton:hover {
                background: rgba(255, 60, 60, 0.85);
                color: #ffffff;
                border-color: transparent;
            }
        """)
        quit_btn.clicked.connect(self._on_quit_clicked)
        bottom_bar.addWidget(quit_btn)

        # Save & Apply Button
        save_btn = QPushButton("Save && Apply")
        save_btn.setFixedHeight(36)
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0078d4, stop:1 #00b4d8);
                color: #ffffff;
                border: none;
                border-radius: 8px;
                font-weight: bold;
                font-size: 13px;
                padding: 0 20px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1088e6, stop:1 #1fc4e8);
            }
        """)
        save_btn.clicked.connect(self._save_and_apply)
        bottom_bar.addWidget(save_btn)

        layout.addLayout(bottom_bar)
        root.addWidget(card)

    # ── Gesture Dragging for Frameless Window ──
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    # ── Interactive Control Handlers ──

    def _on_swatch_clicked(self, selected_swatch: ColorSwatch):
        for sw in self.swatches:
            sw.setChecked(sw == selected_swatch)
        self.slider_box.setVisible(selected_swatch.key == "custom")
        self._emit_live_update()

    def _on_hue_slider_changed(self, val: int):
        self._emit_live_update()

    def _set_style(self, style_val: str):
        self.btn_liquid.setChecked(style_val == "liquid")
        self.btn_glow.setChecked(style_val == "glow_only")
        self._emit_live_update()

    def _set_reactivity(self, react_val: str):
        for b in self.react_btns:
            b.setChecked(b.value == react_val)
        self._emit_live_update()

    def _get_active_theme(self) -> str:
        for sw in self.swatches:
            if sw.isChecked():
                return sw.key
        return "rainbow"

    def _get_active_style(self) -> str:
        return "glow_only" if self.btn_glow.isChecked() else "liquid"

    def _get_active_reactivity(self) -> str:
        for b in self.react_btns:
            if b.isChecked():
                return b.value
        return "normal"

    def _emit_live_update(self):
        """Immediately transmits live updates to the floating desktop orb."""
        settings = {
            "color_theme": self._get_active_theme(),
            "custom_hue": self.hue_slider.value(),
            "animation_style": self._get_active_style(),
            "scale_reactivity": self._get_active_reactivity(),
            "speed_pace": "balanced",
            "autostart": self.autostart_cb.isChecked(),
        }
        self.settings_changed.emit(settings)

    def _save_and_apply(self):
        new_settings = {
            "color_theme": self._get_active_theme(),
            "custom_hue": self.hue_slider.value(),
            "animation_style": self._get_active_style(),
            "scale_reactivity": self._get_active_reactivity(),
            "speed_pace": "balanced",
            "autostart": self.autostart_cb.isChecked(),
        }
        self.config.update(new_settings)
        save_config(self.config)
        set_autostart_registry(new_settings["autostart"])
        self.settings_changed.emit(new_settings)
        self.accept()

    def _on_quit_clicked(self):
        """Signals the application to terminate completely."""
        self.quit_app_requested.emit()
        self.accept()
