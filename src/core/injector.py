"""
Win32 Focus-Safe Text Injector

Provides strategies for typing text into any target application on Windows:
1. Fast Paste: Clipboard + Ctrl+V via SendInput (most reliable, works everywhere)
2. Quick Paste: Clipboard + Ctrl+V without save/restore (fast, for streaming)
3. Direct Unicode SendInput: KEYEVENTF_UNICODE for terminals
"""

import ctypes
from ctypes import wintypes
import time
import logging

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Win32 Constants
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_CONTROL = 0x11
VK_V = 0x56
VK_BACK = 0x08

# ── Win32 type annotations (critical for 64-bit pointer safety) ──
user32.OpenClipboard.restype = wintypes.BOOL
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.CloseClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.GetClipboardData.restype = ctypes.c_void_p
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]

kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalFree.restype = ctypes.c_void_p
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong),
    ]


class MOUSEINPUT(ctypes.Structure):
    """Required in the union so INPUT has the correct 40-byte size on 64-bit."""
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", INPUT_UNION),
    ]


# ── Clipboard helpers ──

def get_clipboard_text() -> str | None:
    """Safely retrieves current Unicode text from clipboard if present."""
    if not user32.OpenClipboard(None):
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str) -> bool:
    """Sets Unicode text into Windows clipboard."""
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        byte_len = (len(text) + 1) * ctypes.sizeof(ctypes.c_wchar)
        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, byte_len)
        if not h_mem:
            return False

        ptr = kernel32.GlobalLock(h_mem)
        if not ptr:
            kernel32.GlobalFree(h_mem)
            return False

        try:
            ctypes.memmove(ptr, ctypes.c_wchar_p(text), byte_len)
        finally:
            kernel32.GlobalUnlock(h_mem)

        if not user32.SetClipboardData(CF_UNICODETEXT, h_mem):
            kernel32.GlobalFree(h_mem)
            return False
        return True
    finally:
        user32.CloseClipboard()


# ── Key simulation helpers ──

def _simulate_ctrl_v():
    """Sends Ctrl + V keystroke via SendInput."""
    inputs = (INPUT * 4)()
    inputs[0].type = INPUT_KEYBOARD
    inputs[0].u.ki = KEYBDINPUT(VK_CONTROL, 0, 0, 0, 0)
    inputs[1].type = INPUT_KEYBOARD
    inputs[1].u.ki = KEYBDINPUT(VK_V, 0, 0, 0, 0)
    inputs[2].type = INPUT_KEYBOARD
    inputs[2].u.ki = KEYBDINPUT(VK_V, 0, KEYEVENTF_KEYUP, 0, 0)
    inputs[3].type = INPUT_KEYBOARD
    inputs[3].u.ki = KEYBDINPUT(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0, 0)
    user32.SendInput(4, ctypes.byref(inputs), ctypes.sizeof(INPUT))


# ── Injection strategies ──

def type_via_clipboard(text: str, restore_delay_sec: float = 0.5) -> bool:
    """
    Inserts text by saving existing clipboard, setting new text, simulating Ctrl+V,
    and restoring original clipboard after a brief delay.
    """
    if not text:
        return True

    original_text = get_clipboard_text()
    success = set_clipboard_text(text)
    if not success:
        logger.warning("Failed to set clipboard text, falling back to Unicode SendInput.")
        type_unicode_chars(text)
        return True

    _simulate_ctrl_v()
    time.sleep(restore_delay_sec)

    if original_text is not None:
        set_clipboard_text(original_text)

    return True


def quick_paste(text: str):
    """
    Fast clipboard paste without save/restore. Used during streaming
    to minimize latency. Caller is responsible for clipboard management.
    Automatically uses Unicode SendInput if active window is a terminal.
    """
    if not text:
        return
    title = get_foreground_window_title().lower()
    is_terminal = any(term in title for term in ["cmd.exe", "powershell", "terminal", "bash", "wsl"])
    if is_terminal:
        type_unicode_chars(text)
    else:
        set_clipboard_text(text)
        time.sleep(0.02)  # Let clipboard settle
        _simulate_ctrl_v()
        time.sleep(0.08)  # Let target app process the paste


def type_unicode_chars(text: str):
    """
    Simulates direct keyboard typing using KEYEVENTF_UNICODE.
    Ideal for command prompts, PowerShell, and terminals.
    """
    if not text:
        return

    n_chars = len(text)
    inputs = (INPUT * (n_chars * 2))()

    for i, char in enumerate(text):
        char_code = ord(char)
        inputs[i * 2].type = INPUT_KEYBOARD
        inputs[i * 2].u.ki = KEYBDINPUT(0, char_code, KEYEVENTF_UNICODE, 0, 0)
        inputs[i * 2 + 1].type = INPUT_KEYBOARD
        inputs[i * 2 + 1].u.ki = KEYBDINPUT(0, char_code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)

    user32.SendInput(len(inputs), ctypes.byref(inputs), ctypes.sizeof(INPUT))


def get_foreground_window_title() -> str:
    """Returns the title of the currently focused foreground window."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buff = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buff, length + 1)
    return buff.value


def inject_text(text: str, prefer_paste: bool = True):
    """
    Main entry point for text injection into the active foreground control.
    Automatically checks terminal apps or user preference.
    """
    if not text:
        return

    title = get_foreground_window_title()
    logger.info(f"Injecting {len(text)} chars into: '{title}'")

    is_terminal = any(term in title.lower() for term in ["cmd.exe", "powershell", "terminal", "bash", "wsl"])

    if is_terminal or not prefer_paste:
        logger.info("Using Unicode SendInput method.")
        type_unicode_chars(text)
    else:
        logger.info("Using clipboard paste method.")
        type_via_clipboard(text)
    logger.info("Text injection complete.")
