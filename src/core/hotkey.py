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
    "f8": 0x77,
    "f9": 0x78,
    "space": 0x20,
    "pause": 0x13,
}
VK_MAP.update({f"f{i}": 0x6F + i for i in range(1, 25)})
VK_MAP.update({chr(i).lower(): i for i in range(ord('A'), ord('Z') + 1)})
VK_MAP.update({str(i): 0x30 + i for i in range(10)})
VK_MAP.update({"enter": 0x0D, "tab": 0x09, "escape": 0x1B, "backspace": 0x08,
               "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
               "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28})
MODIFIER_KEYS = {
    "ctrl": {0x11, 0xA2, 0xA3}, "alt": {0x12, 0xA4, 0xA5},
    "shift": {0x10, 0xA0, 0xA1}, "win": {0x5B, 0x5C},
}
LLKHF_INJECTED = 0x10


def parse_hotkey(name: str):
    """Validate the same combinations the recorder offers; never silently substitute a key."""
    parts = name.lower().split("+")
    if (not parts or parts[-1] not in VK_MAP or len(set(parts)) != len(parts)
            or any(p not in MODIFIER_KEYS for p in parts[:-1])):
        raise ValueError(f"Unsupported hotkey: {name}")
    return frozenset(parts[:-1]), VK_MAP[parts[-1]]

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
        is_active: Optional[Callable[[], bool]] = None,
        on_toggle: Optional[Callable[[], None]] = None,
    ):
        self._trigger_key_name = trigger_key.lower()
        self._modifiers, self.trigger_vk = parse_hotkey(trigger_key)
        self.push_to_talk = push_to_talk
        self.on_start = on_start
        self.on_stop = on_stop
        # The app owns the real dictation state (the orb, tray or an error can change it behind
        # our back); asking it avoids a stale local flag making the first keypress a no-op.
        self.is_active = is_active
        self.on_toggle = on_toggle

        self._hook = None
        self._hook_proc = None
        self._thread = None
        self._thread_id = None
        self._is_active = False
        self._is_recording = False

        # Track physical holds so each press fires once while rapid distinct presses still work.
        self._held_keys = set()
        self._trigger_down = False
        self._suppressed_keys = set()
        self._ptt_held = False

    def set_trigger_key(self, key_name: str):
        modifiers, vk = parse_hotkey(key_name)
        if key_name.lower() == self._trigger_key_name:
            return
        self._release_ptt()
        self._trigger_key_name = key_name.lower()
        self._modifiers, self.trigger_vk = modifiers, vk
        self._trigger_down = False
        logger.info(f"Trigger key updated to: {key_name}")

    def set_mode(self, push_to_talk: bool):
        if self.push_to_talk == push_to_talk:
            return
        self._release_ptt()
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
        self._release_ptt()
        self._is_active = False
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)

    def _fire_toggle(self):
        """Toggle recording state and fire the appropriate callback."""
        if self.on_toggle:
            self._dispatch(self.on_toggle)
            return
        current = self.is_active() if self.is_active else self._is_recording
        self._is_recording = not current
        state_str = "ON" if self._is_recording else "OFF"
        logger.info(f"Hotkey toggle triggered -> Recording is {state_str}")
        callback = self.on_start if self._is_recording else self.on_stop
        self._dispatch(callback)

    @staticmethod
    def _dispatch(callback):
        # App callbacks emit queued Qt signals: ordered, non-blocking, and no thread per press.
        if callback:
            callback()

    def _release_ptt(self):
        if self._ptt_held:
            self._ptt_held = False
            self._is_recording = False
            self._dispatch(self.on_stop)

    def _handle_key(self, vk: int, is_down: bool, is_up: bool) -> bool:
        """Pure key-state transition. True means suppress this event in the target app."""
        if is_down:
            self._held_keys.add(vk)
        elif is_up:
            self._held_keys.discard(vk)
        else:
            return False
        active_modifiers = {name for name, keys in MODIFIER_KEYS.items() if keys & self._held_keys}

        # Release of either part of a held combination ends push-to-talk.
        if self._ptt_held and (vk == self.trigger_vk and is_up
                              or not self._modifiers <= active_modifiers):
            self._release_ptt()
        if is_up and vk in self._suppressed_keys:
            self._suppressed_keys.discard(vk)
            self._trigger_down = False
            return True
        if vk != self.trigger_vk:
            return False
        if is_down and vk in self._suppressed_keys:
            return True                      # autorepeat even after a modifier was released
        ignored = {name for name, keys in MODIFIER_KEYS.items() if self.trigger_vk in keys}
        matches = active_modifiers - ignored == self._modifiers
        if is_down and matches and not self._trigger_down:
            self._trigger_down = True
            self._suppressed_keys.add(vk)
            if self.push_to_talk:
                self._ptt_held = True
                self._is_recording = True
                self._dispatch(self.on_start)
            else:
                self._fire_toggle()
            return True
        if is_up:
            self._trigger_down = False
        return False

    def _hook_callback(self, nCode, wParam, lParam):
        if nCode >= 0:
            kb = KBDLLHOOKSTRUCT.from_address(lParam)
            is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            is_up = wParam in (WM_KEYUP, WM_SYSKEYUP)
            vk = kb.vkCode

            if not kb.flags & LLKHF_INJECTED and self._handle_key(vk, is_down, is_up):
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
            self._is_active = False
            logger.error("Failed to install WH_KEYBOARD_LL hook!")
            return

        self._held_keys = {vk for keys in MODIFIER_KEYS.values() for vk in keys if vk not in {0x10, 0x11, 0x12}
                           if user32.GetAsyncKeyState(vk) & 0x8000}

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
        self._is_active = False
