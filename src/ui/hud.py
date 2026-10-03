"""
Floating Interactive Uiverse Animated Orb HUD (Luminous & Ultra-Reactive)

Features:
- Pure HSL Color System: Vividly journeys through Golden Amber -> Lush Emerald Green ->
  Electric Turquoise -> Radiant Cobalt Blue -> Royal Violet -> Crimson Rose!
- Customizable Themes: Rainbow Chroma Flow, Electric Cyan/Ice Blue, Solar Gold, Emerald, Violet, Custom.
- Animation Styles: Dynamic Liquid Molten vs Calm Solid Glow.
- Booting State: Matte grey, dormant, zero liquid movement until speech engine is ready.
- Bloom to Life: Smoothly transitions into full vibrant glowing color once engine loads.
- Floating Ready Pill Badge: Clean, sleek in-app notification that gently slides in and fades out.
- Gesture Control:
  - Left-Click: Toggle start/stop dictation
  - Right-Click: Toggle pause/resume (ice blue shimmer)
  - Long-Press (0.5s): Opens Quick Orb Customizer Settings Dialog!
  - Drag: Move anywhere across screens
- 100% Win32 Focus-Safe: Non-activating (WS_EX_NOACTIVATE) window never steals typing cursor focus.
"""

import ctypes
from ctypes import wintypes
import logging

from PySide6.QtCore import Qt, QPoint, Signal, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QWidget, QVBoxLayout, QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010

ANIMATION_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}
body {
  margin: 0;
  background: transparent;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  height: 100vh;
  overflow: hidden;
  user-select: none;
  cursor: pointer;
  font-family: 'Segoe UI Variable Text', 'Segoe UI', system-ui, sans-serif;
}

/*
 * Base orange colors are identical to the original Uiverse component.
 * All color shifting is done via filter:hue-rotate() on .loader so that
 * box-shadow, ::before, and .box all shift together — guaranteed match.
 */
.loader {
  --time-animation: 2s;
  --size: 0.70;
  position: relative;
  border-radius: 50%;
  transform: scale(var(--size));
  box-shadow:
    0 0 25px 0 #ffbf4880,
    0 20px 50px 0 #bf4a1d80;
}

.loader::before {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  width: 100px;
  height: 100px;
  border-radius: 50%;
  border-top: solid 1.5px #ffbf48;
  border-bottom: solid 1.5px #be4a1d;
  background: linear-gradient(180deg, #ffbf4740, #bf4a1d80);
  box-shadow:
    inset 0 10px 10px 0 #ffbf4780,
    inset 0 -10px 10px 0 #bf4a1d80;
}

.loader .box {
  width: 100px;
  height: 100px;
  background: linear-gradient(180deg, #ffbf48 30%, #be4a1d 70%);
  mask: url(#clipping);
  -webkit-mask: url(#clipping);
}

/* Calm solid glow mode — remove liquid mask, keep circle shape */
.loader.no-liquid .box {
  mask: none !important;
  -webkit-mask: none !important;
  border-radius: 50%;
}

.loader svg {
  position: absolute;
}
.loader svg #clipping {
  filter: contrast(15);
  animation: roundness calc(var(--time-animation) / 2) linear infinite;
}
.loader svg #clipping polygon {
  filter: blur(7px);
}
.loader svg #clipping polygon:nth-child(1) {
  transform-origin: 75% 25%;
  transform: rotate(90deg);
}
.loader svg #clipping polygon:nth-child(2) {
  transform-origin: 50% 50%;
  animation: rotation var(--time-animation) linear infinite reverse;
}
.loader svg #clipping polygon:nth-child(3) {
  transform-origin: 50% 60%;
  animation: rotation var(--time-animation) linear infinite;
  animation-delay: calc(var(--time-animation) / -3);
}
.loader svg #clipping polygon:nth-child(4) {
  transform-origin: 40% 40%;
  animation: rotation var(--time-animation) linear infinite reverse;
}
.loader svg #clipping polygon:nth-child(5) {
  transform-origin: 40% 40%;
  animation: rotation var(--time-animation) linear infinite reverse;
  animation-delay: calc(var(--time-animation) / -2);
}
.loader svg #clipping polygon:nth-child(6) {
  transform-origin: 60% 40%;
  animation: rotation var(--time-animation) linear infinite;
}
.loader svg #clipping polygon:nth-child(7) {
  transform-origin: 60% 40%;
  animation: rotation var(--time-animation) linear infinite;
  animation-delay: calc(var(--time-animation) / -1.5);
}

@keyframes rotation {
  0%   { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}
@keyframes roundness {
  0%,  60%, 100% { filter: contrast(15); }
  20%, 40%       { filter: contrast(3);  }
}
</style>
</head>
<body>
  <div class="loader" id="loader">
    <svg width="100" height="100" viewBox="0 0 100 100">
      <defs>
        <mask id="clipping">
          <polygon points="0,0 100,0 100,100 0,100" fill="black"></polygon>
          <polygon points="25,25 75,25 50,75" fill="white"></polygon>
          <polygon points="50,25 75,75 25,75" fill="white"></polygon>
          <polygon points="35,35 65,35 50,65" fill="white"></polygon>
          <polygon points="35,35 65,35 50,65" fill="white"></polygon>
          <polygon points="35,35 65,35 50,65" fill="white"></polygon>
          <polygon points="35,35 65,35 50,65" fill="white"></polygon>
        </mask>
      </defs>
    </svg>
    <div class="box"></div>
  </div>

  <script>
    const el = document.getElementById('loader');

    // ── Appearance state ──
    let colorTheme    = "rainbow";
    let customHue     = 195;
    let animationStyle  = "liquid";
    let scaleMultiplier = 0.14;
    let speedMultiplier = 1.0;

    // Hue cycling (all in 0–360 degrees)
    let currentHue      = 38;
    let targetHueSpeed  = 14;
    let currentHueSpeed = 14;

    // Animation playback rate
    let targetRate  = 0.38;
    let currentRate = 0.38;

    // Scale (driven by volume)
    let targetScale  = 0.70;
    let currentScale = 0.70;

    // Bloom: grayscale & brightness lerp from booting → ready
    // These are JS-driven every frame so the whole filter stays consistent.
    let targetGray    = 1.0;   // 1 = fully grey (booting), 0 = full color
    let currentGray   = 1.0;
    let targetBright  = 0.6;
    let currentBright = 0.6;

    let isPaused      = false;
    let isListening   = false;
    let isEngineReady = false;
    let lastTime      = performance.now();

    // ── Public API called from Python ──

    window.setEngineReady = function(ready) {
      isEngineReady = ready;
      if (ready) {
        targetGray   = 0.0;
        targetBright = 1.0;
      } else {
        targetGray    = 1.0;
        targetBright  = 0.6;
        currentGray   = 1.0;
        currentBright = 0.6;
      }
    };

    window.setAppearanceSettings = function(theme, hue, animStyle, scaleMode, speedMode) {
      colorTheme  = theme || "rainbow";
      customHue   = (typeof hue === 'number') ? hue : 195;
      animationStyle = animStyle || "liquid";

      if (animationStyle === 'glow_only') {
        el.classList.add('no-liquid');
      } else {
        el.classList.remove('no-liquid');
      }

      if (scaleMode === 'none')        scaleMultiplier = 0.0;
      else if (scaleMode === 'subtle') scaleMultiplier = 0.08;
      else if (scaleMode === 'high')   scaleMultiplier = 0.24;
      else                             scaleMultiplier = 0.14;

      if (speedMode === 'relaxed')    speedMultiplier = 0.75;
      else if (speedMode === 'fast')  speedMultiplier = 1.35;
      else                            speedMultiplier = 1.0;
    };

    function setVisualState(listening, paused, volume) {
      isListening = listening;
      isPaused    = paused;
      volume      = Math.max(0, Math.min(1, volume));

      if (isPaused) {
        targetRate      = 0.18;
        targetScale     = 0.66;
        targetHueSpeed  = 0;
      } else if (isListening) {
        targetRate      = (0.55 + volume * 2.25) * speedMultiplier;
        targetScale     = 0.70 + volume * scaleMultiplier;
        targetHueSpeed  = (15  + volume * 40)    * speedMultiplier;
      } else {
        targetRate      = 0.38 * speedMultiplier;
        targetScale     = 0.70;
        targetHueSpeed  = 12   * speedMultiplier;
      }
    }

    /*
     * The base color is orange (hue ≈ 38°).
     * To reach any target hue H, apply hue-rotate(H - 38).
     * Because filter:hue-rotate() is applied to the whole .loader element,
     * box-shadow, ::before, and .box all shift identically — perfect match.
     */
    function hueRotateDeg(targetHue) {
      return ((targetHue - 38) % 360 + 360) % 360;
    }

    function loop(now) {
      const dt = Math.min((now - lastTime) / 1000, 0.08);
      lastTime = now;

      // ── Bloom transition (grayscale + brightness lerp) ──
      const bloomK = Math.min(1, dt * 2.2);
      currentGray   += (targetGray   - currentGray)   * bloomK;
      currentBright += (targetBright - currentBright)  * bloomK;

      // ── Determine hue rotation ──
      let hueRot = 0;
      if (isPaused && isEngineReady) {
        hueRot = hueRotateDeg(200);            // icy blue while paused
      } else if (isEngineReady) {
        if (colorTheme === "rainbow") {
          currentHueSpeed += (targetHueSpeed - currentHueSpeed) * Math.min(1, dt * 6);
          currentHue = (currentHue + currentHueSpeed * dt) % 360;
          hueRot = hueRotateDeg(currentHue);
        } else if (colorTheme === "cyan_blue") { hueRot = hueRotateDeg(195); }
        else if (colorTheme === "gold_fire")    { hueRot = 0;                }
        else if (colorTheme === "emerald")      { hueRot = hueRotateDeg(145);}
        else if (colorTheme === "violet")       { hueRot = hueRotateDeg(275);}
        else if (colorTheme === "custom")       { hueRot = hueRotateDeg(customHue); }
      }

      // ── Apply combined filter — one property, all children shift together ──
      el.style.filter =
        `grayscale(${currentGray.toFixed(3)}) ` +
        `brightness(${currentBright.toFixed(3)}) ` +
        `hue-rotate(${hueRot.toFixed(1)}deg)`;

      // ── Scale (voice reactivity) ──
      currentScale += (targetScale - currentScale) * Math.min(1, dt * 16);
      el.style.setProperty('--size', currentScale.toFixed(3));

      // ── Animation speed ──
      currentRate += (targetRate - currentRate) * Math.min(1, dt * 8);
      try {
        document.getAnimations().forEach(anim => {
          if (anim.updatePlaybackRate) anim.updatePlaybackRate(currentRate);
          else anim.playbackRate = currentRate;
        });
      } catch(e) {}

      requestAnimationFrame(loop);
    }

    // Start in booting state (grayscale), loop drives bloom on ready
    el.style.filter = 'grayscale(1) brightness(0.6) hue-rotate(0deg)';
    requestAnimationFrame(loop);
  </script>
</body>
</html>"""


class ClickOverlay(QWidget):
    """Transparent overlay intercepting mouse gestures over the WebEngine.

    Only events whose local position falls inside the circular orb area are
    treated as intentional — everything outside is silently ignored so that
    stray taps in the corners of the bounding box don't accidentally trigger
    dictation.
    """
    clicked = Signal()
    right_clicked = Signal(QPoint)
    long_pressed = Signal()

    # The orb is a 100 px circle, centred inside the 180 px widget, rendered
    # at CSS scale(0.70).  Visual radius ≈ 50 px.  We add a small margin so
    # the very edge of the orb remains easily clickable.
    _ORB_RADIUS = 54   # px in widget-local coords

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._is_dragging = False
        self._mouse_press_pos = None
        self._drag_start_pos = None
        self._press_was_inside = False

        # 500ms hold timer for long-press gesture
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.setInterval(500)
        self._long_press_timer.timeout.connect(self._on_long_press_timeout)
        self._long_press_triggered = False

    def _inside_orb(self, local_pos) -> bool:
        """Return True if *local_pos* (QPoint in widget coords) is within the orb circle."""
        cx = self.width() / 2
        cy = self.height() / 2
        dx = local_pos.x() - cx
        dy = local_pos.y() - cy
        return (dx * dx + dy * dy) <= (self._ORB_RADIUS * self._ORB_RADIUS)

    def _update_cursor(self, local_pos):
        if self._inside_orb(local_pos):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def _on_long_press_timeout(self):
        if not self._is_dragging and self._mouse_press_pos is not None:
            self._long_press_triggered = True
            logger.info("Long press detected on Floating Orb HUD.")
            self.long_pressed.emit()

    def mousePressEvent(self, event: QMouseEvent):
        local = event.position().toPoint()
        self._press_was_inside = self._inside_orb(local)

        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = False
            self._long_press_triggered = False
            self._mouse_press_pos = event.globalPosition().toPoint()
            self._drag_start_pos = self.window().frameGeometry().topLeft()
            if self._press_was_inside:
                self._long_press_timer.start()
        elif event.button() == Qt.MouseButton.RightButton:
            self._long_press_timer.stop()
            self._is_dragging = False
            self._mouse_press_pos = event.globalPosition().toPoint()
            self._drag_start_pos = self.window().frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        self._update_cursor(event.position().toPoint())
        if event.buttons() & Qt.MouseButton.LeftButton and self._mouse_press_pos:
            delta = event.globalPosition().toPoint() - self._mouse_press_pos
            if delta.manhattanLength() > 5:
                self._is_dragging = True
                self._long_press_timer.stop()
                self.window().move(self._drag_start_pos + delta)
            super().mouseMoveEvent(event)
        elif event.buttons() & Qt.MouseButton.RightButton and self._mouse_press_pos:
            delta = event.globalPosition().toPoint() - self._mouse_press_pos
            if delta.manhattanLength() > 5:
                self._is_dragging = True
                self.window().move(self._drag_start_pos + delta)
            super().mouseMoveEvent(event)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._long_press_timer.stop()
        if not self._is_dragging and not self._long_press_triggered and self._press_was_inside:
            if event.button() == Qt.MouseButton.LeftButton:
                self.clicked.emit()
            elif event.button() == Qt.MouseButton.RightButton:
                self.right_clicked.emit(event.globalPosition().toPoint())
        self._is_dragging = False
        self._long_press_triggered = False
        self._press_was_inside = False
        self._mouse_press_pos = None
        super().mouseReleaseEvent(event)



class CircularOrbHUD(QWidget):
    clicked = Signal()
    pause_toggled = Signal()
    long_pressed = Signal()
    context_menu_requested = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)

        # ── Frameless, Transparent, Non-Activating Window ──
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # 180px accommodates the floating ready badge at the top
        self.widget_size = 180
        self.resize(self.widget_size, self.widget_size)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Embedded Chromium WebEngineView for 100% exact Uiverse CSS/SVG
        self.web_view = QWebEngineView(self)
        self.web_view.page().setBackgroundColor(Qt.GlobalColor.transparent)
        self.web_view.setStyleSheet("background: transparent;")
        self.web_view.setHtml(ANIMATION_HTML)
        layout.addWidget(self.web_view)

        # Click & Drag Overlay
        self.overlay = ClickOverlay(self)
        self.overlay.resize(self.widget_size, self.widget_size)
        self.overlay.clicked.connect(self.clicked.emit)
        self.overlay.right_clicked.connect(self.context_menu_requested.emit)
        self.overlay.long_pressed.connect(self.long_pressed.emit)

        # State: "booting" | "idle" | "listening" | "paused"
        self.state = "booting"
        self.is_ready = False
        self.current_volume = 0.0
        self.smoothed_volume = 0.0

        self._last_sent_vol = -1.0
        self._last_listening = None
        self._last_paused = None

        # 40 FPS snappy interpolation timer for voice modulation
        self._lerp_timer = QTimer(self)
        self._lerp_timer.timeout.connect(self._on_lerp_tick)
        self._lerp_timer.start(25)

        # Position at bottom-right of primary screen
        self._position_default()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.overlay.resize(self.size())

    def apply_appearance_settings(self, cfg: dict):
        """Dispatches appearance preferences into the live Chromium WebEngine."""
        theme = cfg.get("color_theme", "rainbow")
        hue = cfg.get("custom_hue", 195)
        style = cfg.get("animation_style", "liquid")
        scale = cfg.get("scale_reactivity", "normal")
        speed = cfg.get("speed_pace", "balanced")

        js = f"window.setAppearanceSettings('{theme}', {hue}, '{style}', '{scale}', '{speed}');"
        self.web_view.page().runJavaScript(js)

    def set_engine_ready(self, ready: bool, message: str = ""):
        """Transitions orb from dormant grey into full vibrant living color."""
        self.is_ready = ready
        if ready and self.state == "booting":
            self.state = "idle"
        js = f"window.setEngineReady({'true' if ready else 'false'});"
        self.web_view.page().runJavaScript(js)

    def set_state(self, state: str):
        """Sets orb state: 'idle', 'listening', or 'paused'."""
        self.state = state
        self._sync_visuals(force=True)

    def set_volume(self, level: float):
        """Called by audio engine with normalized volume (0.0 to 1.0)."""
        self.current_volume = max(0.0, min(1.0, float(level)))

    def _on_lerp_tick(self):
        target = self.current_volume
        if target > self.smoothed_volume:
            self.smoothed_volume += (target - self.smoothed_volume) * 0.45
        else:
            self.smoothed_volume += (target - self.smoothed_volume) * 0.22
        self._sync_visuals()

    def _sync_visuals(self, force: bool = False):
        is_listening = (self.state == "listening")
        is_paused = (self.state == "paused")
        vol = self.smoothed_volume if is_listening else 0.0

        if (force or 
            abs(vol - self._last_sent_vol) > 0.015 or 
            is_listening != self._last_listening or 
            is_paused != self._last_paused):
            
            self._last_sent_vol = vol
            self._last_listening = is_listening
            self._last_paused = is_paused

            js = f"setVisualState({'true' if is_listening else 'false'}, {'true' if is_paused else 'false'}, {vol:.2f});"
            self.web_view.page().runJavaScript(js)

    def show_hud(self):
        self.set_state("listening")
        self.show()
        self._apply_win32_styles()

    def hide_hud(self):
        self.set_state("idle")
        self.current_volume = 0.0
        self.smoothed_volume = 0.0
        self._sync_visuals(force=True)

    def set_status(self, text: str):
        pass

    def _apply_win32_styles(self):
        """Enforces WS_EX_NOACTIVATE so clicking never steals keyboard focus."""
        try:
            hwnd = int(self.winId())
            ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(
                hwnd,
                GWL_EXSTYLE,
                ex_style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
            )
            user32.SetWindowPos(
                hwnd,
                HWND_TOPMOST,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
            )
        except Exception as e:
            logger.debug(f"Win32 style error: {e}")

    def _position_default(self):
        """Positions the orb in the bottom-right corner, comfortably above taskbar."""
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        x = geo.x() + geo.width() - self.width() - 25
        y = geo.y() + geo.height() - self.height() - 25
        self.move(x, y)


# Safe alias for cross-module compatibility
FloatingHUD = CircularOrbHUD
