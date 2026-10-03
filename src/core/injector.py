"""
Win32 Focus-Safe Text Injector

Strategies for putting text into the focused application on Windows:
1. Unicode SendInput (default): types characters directly. Leaves the clipboard untouched and
   works in terminals. Handles emoji (surrogate pairs) and newlines.
2. Clipboard paste (fallback for very long text or when explicitly requested): refuses to run
   when the clipboard holds non-text data (images/files) that it could not restore.
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
VK_RETURN = 0x0D
VK_TAB = 0x09

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
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]

kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalFree.restype = ctypes.c_void_p
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
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

# Clipboard formats that a text-only save/restore would destroy.
_CF_BITMAP, _CF_DIB, _CF_HDROP = 2, 8, 15

# A single SendInput batch is kept modest: some apps drop events from huge batches.
_SENDINPUT_BATCH_CHARS = 120
# Above this length a paste is far faster than typing (and the clipboard is restored).
AUTO_PASTE_THRESHOLD_CHARS = 600


def _clipboard_has_non_text() -> bool:
    return any(user32.IsClipboardFormatAvailable(f) for f in (_CF_BITMAP, _CF_DIB, _CF_HDROP))


def type_via_clipboard(text: str, restore_delay_sec: float = 0.15) -> bool:
    """
    Pastes text via the clipboard, restoring the previous text afterwards.
    Returns False (without touching anything) if the clipboard holds an image or files,
    so the caller can fall back to typing instead of destroying the user's clipboard.
    """
    if not text:
        return True
    if _clipboard_has_non_text():
        return False

    original_text = get_clipboard_text()
    if not set_clipboard_text(text):
        return False

    _simulate_ctrl_v()
    time.sleep(restore_delay_sec)

    # Only restore if nobody replaced the clipboard in the meantime.
    if get_clipboard_text() == text:
        if original_text is not None:
            set_clipboard_text(original_text)
        elif user32.OpenClipboard(None):
            user32.EmptyClipboard()
            user32.CloseClipboard()
    return True


def _text_to_key_events(text: str) -> list:
    """Converts text into (vk, scan, flags) events. Non-BMP characters become surrogate pairs."""
    events = []
    for ch in text:
        if ch == "\r":
            continue
        if ch == "\n":
            events.append((VK_RETURN, 0, 0))
            events.append((VK_RETURN, 0, KEYEVENTF_KEYUP))
        elif ch == "\t":
            events.append((VK_TAB, 0, 0))
            events.append((VK_TAB, 0, KEYEVENTF_KEYUP))
        else:
            data = ch.encode("utf-16-le")
            for i in range(0, len(data), 2):
                unit = int.from_bytes(data[i:i + 2], "little")
                events.append((0, unit, KEYEVENTF_UNICODE))
                events.append((0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    return events


def type_unicode_chars(text: str) -> bool:
    """
    Types text with KEYEVENTF_UNICODE events. Returns False if Windows rejected the input
    (typically because the target window is elevated and we are not — UIPI).
    """
    if not text:
        return True

    events = _text_to_key_events(text)
    step = _SENDINPUT_BATCH_CHARS * 2
    for i in range(0, len(events), step):
        batch = events[i:i + step]
        arr = (INPUT * len(batch))()
        for j, (vk, scan, flags) in enumerate(batch):
            arr[j].type = INPUT_KEYBOARD
            arr[j].u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
        sent = user32.SendInput(len(batch), ctypes.byref(arr), ctypes.sizeof(INPUT))
        if sent != len(batch):
            logger.warning(f"SendInput accepted {sent}/{len(batch)} events (blocked by the target window?).")
            return False
    return True


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


def get_foreground_hwnd() -> int:
    return int(user32.GetForegroundWindow() or 0)


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TOKEN_QUERY = 0x0008
_ERROR_ACCESS_DENIED = 5


def foreground_blocks_injection() -> bool:
    """
    Heuristic for UIPI: Windows silently discards injected input aimed at an elevated window
    when we are not elevated. A non-elevated process cannot open the token of an elevated one,
    so "token query denied" on the foreground window's process is the signal.
    """
    try:
        if ctypes.windll.shell32.IsUserAnAdmin():
            return False
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return False
        h_proc = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h_proc:
            return False
        try:
            advapi32 = ctypes.windll.advapi32
            advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
            h_tok = wintypes.HANDLE()
            if advapi32.OpenProcessToken(h_proc, _TOKEN_QUERY, ctypes.byref(h_tok)):
                kernel32.CloseHandle(h_tok)
                return False
            return ctypes.GetLastError() == _ERROR_ACCESS_DENIED
        finally:
            kernel32.CloseHandle(h_proc)
    except Exception:
        return False


def inject_text(text: str, mode: str = "auto") -> bool:
    """
    Main entry point for text injection into the focused control.

    mode: "auto"  — type; paste only for very long text
          "type"  — always type
          "paste" — paste (falls back to typing if the clipboard can't be safely restored)
    Returns True if the text was handed to the target.
    """
    if not text:
        return True

    if foreground_blocks_injection():
        logger.warning("Foreground window is elevated; Windows blocks injected text into it. "
                       "Run Phonon-2 as administrator to dictate there.")
        return False

    want_paste = mode == "paste" or (mode == "auto" and len(text) > AUTO_PASTE_THRESHOLD_CHARS)
    if want_paste and type_via_clipboard(text):
        return True
    return type_unicode_chars(text)
