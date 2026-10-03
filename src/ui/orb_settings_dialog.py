"""
Professional Dark Stealth Orb Customizer & Dynamics Panel for Phonon-2.

Features:
- Pure dark obsidian / stealth black palette (no deep blue or purple tints).
- Compact, space-efficient layout fitting comfortably on all display sizes.
- Zero emojis: professional vector SVG icons and refined typography.
- Real-time live updates: dragging sliders or toggling swatches updates the desktop orb instantly.
"""

import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QWidget, QSlider, QCheckBox
)
from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QLinearGradient, QBrush, QPen, QMouseEvent

from src.config import save_config, set_autostart_registry, get_autostart_registry
from src.ui.icons import get_svg_icon, get_svg_pixmap


class ColorSwatch(QPushButton):
    """Visual circular color swatch with glowing active ring."""
    def __init__(self, key: str, title: str, brush: QBrush, parent=None):
        super().__init__(parent)
        self.key = key
        self.setToolTip(title)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(36, 36)
        self.brush = brush

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(4, 4, -4, -4)

        # Draw base circle
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.brush)
        painter.drawEllipse(rect)

        # Draw ring if selected
        if self.isChecked():
            pen = QPen(QColor(255, 255, 255), 2.0)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))
        elif self.underMouse():
            pen = QPen(QColor(255, 255, 255, 90), 1.5)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self.rect().adjusted(2, 2, -2, -2))


class SegmentedPill(QPushButton):
    """Modern dark segmented toggle pill with crisp typography."""
    def __init__(self, text: str, value: str, parent=None):
        super().__init__(text, parent)
        self.value = value
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        self.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #8E8E98;
                border: none;
                border-radius: 5px;
                font-size: 11px;
                font-weight: 500;
                padding: 4px 8px;
            }
            QPushButton:hover {
                color: #FFFFFF;
                background-color: rgba(255, 255, 255, 0.05);
            }
            QPushButton:checked {
                background-color: #0078D4;
                color: #FFFFFF;
                font-weight: 600;
            }
        """)


class OrbSettingsDialog(QDialog):
    settings_changed = Signal(dict)
    quit_app_requested = Signal()

    def __init__(self, current_config: dict, parent=None):
        super().__init__(parent)
        self.config = dict(current_config)

        # ── Frameless Acrylic Window ──
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(410, 530)

        self._drag_pos = None
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # Main Card Container (Pure Stealth Black)
        card = QFrame()
        card.setObjectName("mainCard")
        card.setStyleSheet("""
            QFrame#mainCard {
                background-color: #0D0D10;
                border: 1px solid #202026;
                border-radius: 16px;
            }
            QLabel {
                color: #D4D4DC;
                font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, sans-serif;
            }
            QSlider#hueSlider::groove:horizontal {
                height: 5px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ff0055, stop:0.17 #ff9900, stop:0.33 #00f076,
                    stop:0.50 #00d2ff, stop:0.67 #0066ff, stop:0.83 #aa00ff, stop:1 #ff0055);
                border-radius: 2px;
            }
            QSlider#hueSlider::handle:horizontal {
                background: #FFFFFF;
                border: 2px solid #0078D4;
                width: 14px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 7px;
            }
            QSlider#sizeSlider::groove:horizontal, QSlider#opacitySlider::groove:horizontal {
                height: 5px;
                background: #1C1C22;
                border-radius: 2px;
            }
            QSlider#sizeSlider::sub-page:horizontal, QSlider#opacitySlider::sub-page:horizontal {
                background: #0078D4;
                border-radius: 2px;
            }
            QSlider#sizeSlider::handle:horizontal, QSlider#opacitySlider::handle:horizontal {
                background: #FFFFFF;
                border: 1px solid #444450;
                width: 14px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 7px;
            }
            QCheckBox {
                color: #B0B0BC;
                font-size: 12px;
                font-weight: 500;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid #33333E;
                background: #15151A;
            }
            QCheckBox::indicator:checked {
                background-color: #0078D4;
                border-color: #0078D4;
            }
        """)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        # ── 1. Header with Close Button ──
        header = QHBoxLayout()
        header.setSpacing(10)

        icon_lbl = QLabel()
        icon_path = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "icon.png")
        if os.path.exists(icon_path):
            icon_lbl.setPixmap(QIcon(icon_path).pixmap(20, 20))
        else:
            icon_lbl.setPixmap(get_svg_pixmap("sliders", "#0078D4", 20))
        header.addWidget(icon_lbl)

        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("Orb Customizer")
        title.setStyleSheet("font-size: 14px; font-weight: 700; color: #FFFFFF;")
        sub = QLabel("Desktop appearance, dynamics, and responsiveness")
        sub.setStyleSheet("font-size: 11px; color: #727280;")
        title_box.addWidget(title)
        title_box.addWidget(sub)
        header.addLayout(title_box)
        header.addStretch()

        close_btn = QPushButton()
        close_btn.setIcon(get_svg_icon("x", "#9E9EA8", 14))
        close_btn.setFixedSize(26, 26)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                background: #18181E;
                border: 1px solid #282832;
                border-radius: 13px;
            }
            QPushButton:hover {
                background: #C42B1C;
                border-color: #C42B1C;
            }
        """)
        close_btn.clicked.connect(self.reject)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # ── 2. Color Themes ──
        c_title = QLabel("COLOR THEME")
        c_title.setStyleSheet("font-size: 10px; font-weight: 700; color: #60606C; letter-spacing: 0.5px;")
        layout.addWidget(c_title)

        swatch_row = QHBoxLayout()
        swatch_row.setSpacing(8)

        rainbow_grad = QLinearGradient(0, 0, 36, 36)
        rainbow_grad.setColorAt(0.0, QColor("#ff0077"))
        rainbow_grad.setColorAt(0.35, QColor("#ffaa00"))
        rainbow_grad.setColorAt(0.65, QColor("#00d2ff"))
        rainbow_grad.setColorAt(1.0, QColor("#9900ff"))

        cyan_grad = QLinearGradient(0, 0, 36, 36)
        cyan_grad.setColorAt(0.0, QColor("#00d2ff"))
        cyan_grad.setColorAt(1.0, QColor("#0055ff"))

        gold_grad = QLinearGradient(0, 0, 36, 36)
        gold_grad.setColorAt(0.0, QColor("#ffbb33"))
        gold_grad.setColorAt(1.0, QColor("#cc4400"))

        emerald_grad = QLinearGradient(0, 0, 36, 36)
        emerald_grad.setColorAt(0.0, QColor("#00f076"))
        emerald_grad.setColorAt(1.0, QColor("#007733"))

        violet_grad = QLinearGradient(0, 0, 36, 36)
        violet_grad.setColorAt(0.0, QColor("#d946ef"))
        violet_grad.setColorAt(1.0, QColor("#4c1d95"))

        custom_grad = QLinearGradient(0, 0, 36, 36)
        custom_grad.setColorAt(0.0, QColor("#FFFFFF"))
        custom_grad.setColorAt(0.5, QColor("#A0A0A0"))
        custom_grad.setColorAt(1.0, QColor("#404040"))

        self.swatches = [
            ColorSwatch("rainbow", "Rainbow Chroma Flow", QBrush(rainbow_grad)),
            ColorSwatch("cyan_blue", "Electric Cyan & Ice Blue", QBrush(cyan_grad)),
            ColorSwatch("gold_fire", "Solar Amber & Gold", QBrush(gold_grad)),
            ColorSwatch("emerald", "Emerald Neon Green", QBrush(emerald_grad)),
            ColorSwatch("violet", "Royal Violet", QBrush(violet_grad)),
            ColorSwatch("custom", "Custom 360° Hue", QBrush(custom_grad)),
        ]

        active_theme = self.config.get("color_theme", "rainbow")
        for sw in self.swatches:
            sw.setChecked(sw.key == active_theme)
            sw.clicked.connect(lambda _, s=sw: self._on_swatch_clicked(s))
            swatch_row.addWidget(sw)

        layout.addLayout(swatch_row)

        # Custom Hue Slider
        self.slider_box = QWidget()
        s_layout = QVBoxLayout(self.slider_box)
        s_layout.setContentsMargins(0, 2, 0, 0)
        s_layout.setSpacing(2)

        self.hue_slider = QSlider(Qt.Orientation.Horizontal)
        self.hue_slider.setObjectName("hueSlider")
        self.hue_slider.setRange(0, 360)
        self.hue_slider.setValue(self.config.get("custom_hue", 195))
        self.hue_slider.valueChanged.connect(self._on_hue_slider_changed)
        s_layout.addWidget(self.hue_slider)
        layout.addWidget(self.slider_box)
        self.slider_box.setVisible(active_theme == "custom")

        # ── 3. Two-Column Row: Animation Style & Motion Pace ──
        grid_row = QHBoxLayout()
        grid_row.setSpacing(10)

        # Style column
        col_style = QVBoxLayout()
        col_style.setSpacing(4)
        lbl_style = QLabel("STYLE")
        lbl_style.setStyleSheet("font-size: 10px; font-weight: 700; color: #60606C; letter-spacing: 0.5px;")
        col_style.addWidget(lbl_style)

        style_seg = QFrame()
        style_seg.setStyleSheet("background: #141418; border: 1px solid #1E1E24; border-radius: 6px; padding: 2px;")
        style_layout = QHBoxLayout(style_seg)
        style_layout.setContentsMargins(2, 2, 2, 2)
        style_layout.setSpacing(2)

        self.btn_liquid = SegmentedPill("Liquid", "liquid")
        self.btn_glow = SegmentedPill("Solid Glow", "glow_only")

        cur_style = self.config.get("animation_style", "liquid")
        self.btn_liquid.setChecked(cur_style == "liquid")
        self.btn_glow.setChecked(cur_style == "glow_only")

        self.btn_liquid.clicked.connect(lambda: self._set_style("liquid"))
        self.btn_glow.clicked.connect(lambda: self._set_style("glow_only"))

        style_layout.addWidget(self.btn_liquid)
        style_layout.addWidget(self.btn_glow)
        col_style.addWidget(style_seg)
        grid_row.addLayout(col_style, 1)

        # Pace column
        col_pace = QVBoxLayout()
        col_pace.setSpacing(4)
        lbl_pace = QLabel("MOTION PACE")
        lbl_pace.setStyleSheet("font-size: 10px; font-weight: 700; color: #60606C; letter-spacing: 0.5px;")
        col_pace.addWidget(lbl_pace)

        pace_seg = QFrame()
        pace_seg.setStyleSheet("background: #141418; border: 1px solid #1E1E24; border-radius: 6px; padding: 2px;")
        pace_layout = QHBoxLayout(pace_seg)
        pace_layout.setContentsMargins(2, 2, 2, 2)
        pace_layout.setSpacing(2)

        self.pace_btns = [
            SegmentedPill("Gentle", "relaxed"),
            SegmentedPill("Balanced", "balanced"),
            SegmentedPill("Brisk", "fast"),
        ]
        cur_pace = self.config.get("speed_pace", "balanced")
        for pb in self.pace_btns:
            pb.setChecked(pb.value == cur_pace)
            pb.clicked.connect(lambda _, b=pb: self._set_pace(b.value))
            pace_layout.addWidget(pb)

        col_pace.addWidget(pace_seg)
        grid_row.addLayout(col_pace, 1)
        layout.addLayout(grid_row)

        # ── 4. Compact Dual-Slider Card: Size & Opacity ──
        dim_card = QFrame()
        dim_card.setStyleSheet("""
            QFrame {
                background: #141418;
                border: 1px solid #1E1E24;
                border-radius: 8px;
                padding: 6px 10px;
            }
        """)
        dim_layout = QVBoxLayout(dim_card)
        dim_layout.setContentsMargins(8, 6, 8, 6)
        dim_layout.setSpacing(6)

        # Size slider
        row_size = QHBoxLayout()
        s_title = QLabel("Base Size")
        s_title.setStyleSheet("font-size: 11px; font-weight: 600; color: #B0B0BC;")
        self.size_val_lbl = QLabel(f"{self.config.get('orb_base_size', 70)}%")
        self.size_val_lbl.setStyleSheet("font-size: 11px; font-weight: 700; color: #0078D4;")
        row_size.addWidget(s_title)
        row_size.addStretch()
        row_size.addWidget(self.size_val_lbl)
        dim_layout.addLayout(row_size)

        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setObjectName("sizeSlider")
        self.size_slider.setRange(45, 100)
        self.size_slider.setValue(self.config.get("orb_base_size", 70))
        self.size_slider.valueChanged.connect(self._on_size_slider_changed)
        dim_layout.addWidget(self.size_slider)

        # Opacity slider
        row_op = QHBoxLayout()
        o_title = QLabel("Opacity")
        o_title.setStyleSheet("font-size: 11px; font-weight: 600; color: #B0B0BC;")
        self.opacity_val_lbl = QLabel(f"{self.config.get('orb_opacity', 100)}%")
        self.opacity_val_lbl.setStyleSheet("font-size: 11px; font-weight: 700; color: #0078D4;")
        row_op.addWidget(o_title)
        row_op.addStretch()
        row_op.addWidget(self.opacity_val_lbl)
        dim_layout.addLayout(row_op)

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setObjectName("opacitySlider")
        self.opacity_slider.setRange(25, 100)
        self.opacity_slider.setValue(self.config.get("orb_opacity", 100))
        self.opacity_slider.valueChanged.connect(self._on_opacity_slider_changed)
        dim_layout.addWidget(self.opacity_slider)

        layout.addWidget(dim_card)

        # ── 5. Voice Size Reactivity ──
        react_title = QLabel("VOICE SIZE REACTIVITY")
        react_title.setStyleSheet("font-size: 10px; font-weight: 700; color: #60606C; letter-spacing: 0.5px;")
        layout.addWidget(react_title)

        react_seg = QFrame()
        react_seg.setStyleSheet("background: #141418; border: 1px solid #1E1E24; border-radius: 6px; padding: 2px;")
        react_layout = QHBoxLayout(react_seg)
        react_layout.setContentsMargins(2, 2, 2, 2)
        react_layout.setSpacing(2)

        self.react_btns = [
            SegmentedPill("Off", "none"),
            SegmentedPill("Subtle (10%)", "subtle"),
            SegmentedPill("Normal (20%)", "normal"),
            SegmentedPill("High (35%)", "high"),
        ]

        cur_react = self.config.get("scale_reactivity", "normal")
        for b in self.react_btns:
            b.setChecked(b.value == cur_react)
            b.clicked.connect(lambda _, btn=b: self._set_reactivity(btn.value))
            react_layout.addWidget(b)

        layout.addWidget(react_seg)

        # ── 6. Windows Autostart Row ──
        auto_row = QHBoxLayout()
        self.autostart_cb = QCheckBox("Start automatically with Windows")
        self.autostart_cb.setChecked(get_autostart_registry() or self.config.get("autostart", False))
        auto_row.addWidget(self.autostart_cb)
        layout.addLayout(auto_row)

        layout.addStretch()

        # ── 7. Action Bar ──
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(10)

        quit_btn = QPushButton("Quit App")
        quit_btn.setIcon(get_svg_icon("power", "#E05252", 14))
        quit_btn.setFixedHeight(34)
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setStyleSheet("""
            QPushButton {
                background: #1A1414;
                color: #E05252;
                border: 1px solid #382020;
                border-radius: 6px;
                font-weight: 600;
                font-size: 12px;
                padding: 0 16px;
            }
            QPushButton:hover {
                background: #C42B1C;
                color: #FFFFFF;
                border-color: #C42B1C;
            }
        """)
        quit_btn.clicked.connect(self._on_quit_clicked)
        bottom_bar.addWidget(quit_btn)

        save_btn = QPushButton("Save && Apply")
        save_btn.setIcon(get_svg_icon("check", "#FFFFFF", 14))
        save_btn.setFixedHeight(34)
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet("""
            QPushButton {
                background: #0078D4;
                color: #FFFFFF;
                border: 1px solid #1084D9;
                border-radius: 6px;
                font-weight: 600;
                font-size: 12px;
                padding: 0 20px;
            }
            QPushButton:hover {
                background: #1084D9;
            }
        """)
        save_btn.clicked.connect(self._save_and_apply)
        bottom_bar.addWidget(save_btn, 1)

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

    def _set_pace(self, pace_val: str):
        for b in self.pace_btns:
            b.setChecked(b.value == pace_val)
        self._emit_live_update()

    def _on_size_slider_changed(self, val: int):
        self.size_val_lbl.setText(f"{val}%")
        self._emit_live_update()

    def _on_opacity_slider_changed(self, val: int):
        self.opacity_val_lbl.setText(f"{val}%")
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

    def _get_active_pace(self) -> str:
        for b in self.pace_btns:
            if b.isChecked():
                return b.value
        return "balanced"

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
            "speed_pace": self._get_active_pace(),
            "orb_base_size": self.size_slider.value(),
            "orb_opacity": self.opacity_slider.value(),
            "autostart": self.autostart_cb.isChecked(),
        }
        self.settings_changed.emit(settings)

    def _save_and_apply(self):
        new_settings = {
            "color_theme": self._get_active_theme(),
            "custom_hue": self.hue_slider.value(),
            "animation_style": self._get_active_style(),
            "scale_reactivity": self._get_active_reactivity(),
            "speed_pace": self._get_active_pace(),
            "orb_base_size": self.size_slider.value(),
            "orb_opacity": self.opacity_slider.value(),
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
