"""Read the focused native control without activating or editing a window."""
import ctypes
from ctypes import wintypes
import threading

_tracker = None


class FocusTracker:
    """Observe focus moves, including virtual controls sharing one HWND.

    Out-of-context WinEvents run on a dedicated message-pump thread. This never
    reads field text or records keys, and doesn't block the audio callback on UIA.
    """
    def __init__(self):
        self.epoch = 0
        self._ready = threading.Event()
        self._thread_id = 0
        self._thread = threading.Thread(target=self._run, daemon=True, name="FieldFocusEvents")
        self._thread.start()
        self._ready.wait(1)

    def _run(self):
        user, kernel = ctypes.windll.user32, ctypes.windll.kernel32
        callback_type = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, wintypes.DWORD,
                                          wintypes.HWND, wintypes.LONG, wintypes.LONG,
                                          wintypes.DWORD, wintypes.DWORD)
        user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user.GetAncestor.restype = wintypes.HWND
        user.GetForegroundWindow.restype = wintypes.HWND

        def moved(hook, event, hwnd, object_id, child_id, thread_id, when):
            if hwnd and user.GetAncestor(hwnd, 2) == user.GetForegroundWindow():
                self.epoch += 1

        callback = callback_type(moved)
        user.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE,
                                        callback_type, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
        user.SetWinEventHook.restype = ctypes.c_void_p
        user.UnhookWinEvent.argtypes = [ctypes.c_void_p]
        hook = user.SetWinEventHook(0x8005, 0x8005, None, callback, 0, 0, 0)
        message = wintypes.MSG()
        # Create the thread's message queue before publishing its ID.
        user.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        self._thread_id = kernel.GetCurrentThreadId()
        self._ready.set()
        if not hook:
            return
        try:
            while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user.TranslateMessage(ctypes.byref(message))
                user.DispatchMessageW(ctypes.byref(message))
        finally:
            user.UnhookWinEvent(hook)

    def close(self):
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x12, 0, 0)
            self._thread.join(timeout=1)


def start_focus_tracking():
    global _tracker
    if _tracker is None:
        _tracker = FocusTracker()


def stop_focus_tracking():
    global _tracker
    tracker, _tracker = _tracker, None
    if tracker:
        tracker.close()


def focus_identity():
    class GUIInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD)] + [
            (name, wintypes.HWND) for name in
            ("active", "focus", "capture", "menu_owner", "move_size", "caret")
        ] + [("caret_rect", wintypes.RECT)]
    user = ctypes.windll.user32
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.c_void_p]
    user.GetGUIThreadInfo.restype = wintypes.BOOL
    foreground = user.GetForegroundWindow()
    if not foreground:
        return None
    info = GUIInfo()
    info.cbSize = ctypes.sizeof(info)
    thread = user.GetWindowThreadProcessId(foreground, None)
    if not user.GetGUIThreadInfo(thread, ctypes.byref(info)):
        return (int(foreground), 0, _tracker.epoch if _tracker else 0)
    # Browser DOM fields can share this handle; this is a conservative native
    # control guard, not proof of text-range ownership or caret immutability.
    return (int(foreground), int(info.focus or 0), _tracker.epoch if _tracker else 0)
