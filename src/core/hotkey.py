"""
Global Windows Keyboard Hook & Hotkey Manager

Uses a low-level keyboard hook (WH_KEYBOARD_LL) to intercept keydown and keyup events
system-wide without requiring the application to have focus.

Supports:
- Combo Toggle: Press Ctrl+Space to start dictation, press again to stop.
- Push-to-Talk: Hold a key to dictate, release to commit.
- Single Key Toggle: Press a key once to start, press again to stop.
"""

import ctypes
from ctypes import wintypes
import threading
import time
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Win32 Hook Constants
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

# Virtual Key Codes
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_CONTROL = 0x11
VK_SPACE = 0x20

VK_MAP = {
    "capslock": 0x14,
    "ralt": 0xA5,
    "lalt": 0xA4,
    "rctrl": 0xA3,
    "lctrl": 0xA2,
    "ctrl+space": "combo_ctrl_space",
    "f8": 0x77,
    "f9": 0x78,
    "space": 0x20,
    "pause": 0x13,
}

HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong),
    ]


class GlobalHotkeyManager:
    def __init__(
        self,
        trigger_key: str = "ctrl+space",
        push_to_talk: bool = False,
        on_start: Optional[Callable[[], None]] = None,
        on_stop: Optional[Callable[[], None]] = None,
    ):
        self._trigger_key_name = trigger_key.lower()
        self._is_combo = VK_MAP.get(self._trigger_key_name) == "combo_ctrl_space"
        self.trigger_vk = None if self._is_combo else VK_MAP.get(self._trigger_key_name, 0x14)
        self.push_to_talk = push_to_talk
        self.on_start = on_start
        self.on_stop = on_stop

        self._hook = None
        self._hook_proc = None
        self._thread = None
        self._thread_id = None
        self._is_active = False
        self._is_recording = False

        # State tracking for debounce and autorepeat suppression
        self._ctrl_held = False
        self._space_down = False
        self._single_key_down = False
        self._last_toggle_time = 0.0

    def set_trigger_key(self, key_name: str):
        self._trigger_key_name = key_name.lower()
        self._is_combo = VK_MAP.get(self._trigger_key_name) == "combo_ctrl_space"
        self.trigger_vk = None if self._is_combo else VK_MAP.get(self._trigger_key_name, 0x14)
        logger.info(f"Trigger key updated to: {key_name}")

    def set_mode(self, push_to_talk: bool):
        self.push_to_talk = push_to_talk
        self._is_recording = False

    def start(self):
        """Starts the global hook in a dedicated Windows message pump thread."""
        if self._is_active:
            return
        self._is_active = True
        self._thread = threading.Thread(target=self._run_message_loop, daemon=True, name="Win32HookThread")
        self._thread.start()

    def stop(self):
        """Unhooks and shuts down the message pump."""
        self._is_active = False
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _fire_toggle(self):
        """Toggle recording state and fire the appropriate callback."""
        self._is_recording = not self._is_recording
        state_str = "ON" if self._is_recording else "OFF"
        logger.info(f"Hotkey toggle triggered -> Recording is {state_str}")
        callback = self.on_start if self._is_recording else self.on_stop
        if callback:
            threading.Thread(target=callback, daemon=True).start()

    def _hook_callback(self, nCode, wParam, lParam):
        if nCode >= 0:
            kb = KBDLLHOOKSTRUCT.from_address(lParam)
            is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            is_up = wParam in (WM_KEYUP, WM_SYSKEYUP)
            vk = kb.vkCode

            if self._is_combo:
                # ── Ctrl+Space combo detection ──
                if vk in (VK_LCONTROL, VK_RCONTROL, VK_CONTROL):
                    self._ctrl_held = is_down

                elif vk == VK_SPACE:
                    if is_down:
                        ctrl_active = self._ctrl_held or bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
                        if ctrl_active:
                            if not self._space_down:
                                self._space_down = True
                                now = time.time()
                                if now - self._last_toggle_time > 0.3:
                                    self._last_toggle_time = now
                                    self._fire_toggle()
                            # Always suppress Space when Ctrl is held so Space never leaks to target app!
                            return 1
                    elif is_up:
                        self._space_down = False

            else:
                # ── Single-key mode (push-to-talk or toggle) ──
                if vk == self.trigger_vk:
                    if self.push_to_talk:
                        if is_down and not self._is_recording:
                            self._is_recording = True
                            if self.on_start:
                                threading.Thread(target=self.on_start, daemon=True).start()
                        elif is_up and self._is_recording:
                            self._is_recording = False
                            if self.on_stop:
                                threading.Thread(target=self.on_stop, daemon=True).start()
                    else:
                        if is_down:
                            if not self._single_key_down:
                                self._single_key_down = True
                                now = time.time()
                                if now - self._last_toggle_time > 0.3:
                                    self._last_toggle_time = now
                                    self._fire_toggle()
                        elif is_up:
                            self._single_key_down = False

                    # Suppress CapsLock toggle
                    if self.trigger_vk == 0x14:
                        return 1

        return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

    def _run_message_loop(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        self._hook_proc = HOOKPROC(self._hook_callback)

        # ── Win32 type annotations (critical for 64-bit pointer safety) ──
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p  # HHOOK
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HMODULE, wintypes.DWORD]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]

        h_module = kernel32.GetModuleHandleW(None)
        self._hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            self._hook_proc,
            h_module,
            0
        )

        if not self._hook:
            logger.error("Failed to install WH_KEYBOARD_LL hook!")
            return

        trigger_desc = self._trigger_key_name.upper()
        mode_desc = "push-to-talk" if self.push_to_talk else "toggle"
        logger.info(f"Global keyboard hook installed: [{trigger_desc}] ({mode_desc})")

        # Standard Win32 Message Pump
        msg = wintypes.MSG()
        while self._is_active:
            res = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if res <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
