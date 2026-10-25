"""
Microphone discovery and selection.

Problems this module exists to solve:
  * PortAudio lists every device once per Windows audio API (MME, DirectSound, WASAPI, WDM-KS),
    so one microphone appeared four or five times, next to "PC Speaker", "Stereo Mix" and the
    "Sound Mapper". We list real capture endpoints once, from a single API (WASAPI preferred).
  * PortAudio only enumerates devices when it is initialised, so a USB microphone plugged in
    while the app runs never showed up. refresh() re-initialises it (≈30 ms) on demand.
  * Saving a device INDEX is fragile: indices shift whenever hardware is plugged in. The choice
    is stored by NAME and resolved to a device at open time.
"""

import json
import logging
import os
import subprocess
import sys
import threading
from typing import Callable, List, Optional, Tuple

import sounddevice as sd

logger = logging.getLogger(__name__)

# Not microphones, even though Windows exposes them as capture devices.
_NOT_A_MIC = ("stereo mix", "microsoft sound mapper", "primary sound capture driver", "what u hear", "pc speaker")

_lock = threading.RLock()
stream_lock = _lock  # all stream creation/closure must serialize with global PortAudio refresh
_closers: List[Callable[[], None]] = []


def add_stream_closer(fn: Callable[[], None]):
    """Registers something that must close its audio stream before PortAudio is re-initialised."""
    if fn not in _closers:
        _closers.append(fn)


def remove_stream_closer(fn):
    if fn in _closers:
        _closers.remove(fn)


def refresh():
    """Re-scans devices. Closes registered streams first; the caller must have closed its own."""
    with _lock:
        for fn in list(_closers):
            try:
                fn()
            except Exception:
                logger.debug("stream closer failed", exc_info=True)
        try:
            # A previous failed initialize can leave the library uninitialized.
            # Calling terminate again would prevent the next recovery attempt.
            if getattr(sd, "_initialized", 1) > 0:
                sd._terminate()
            sd._initialize()
        except Exception as e:
            logger.error(f"Could not refresh the audio device list: {e}")
            raise


def fresh_scan() -> Optional[Tuple[List[str], Optional[str]]]:
    """(microphone names, default mic name) as Windows reports them RIGHT NOW (~0.4 s).

    Runs in a throwaway process because PortAudio caches its device list for the life of a
    process and re-initialising it would interrupt the live microphone stream. Returns None if
    the scan fails, so callers never act on a failed scan.
    """
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    code = ("import json,sys;sys.path.insert(0,sys.argv[1]);from src.core import devices;"
            "print(json.dumps([devices.list_microphones(),devices.default_microphone_name()]))")
    executable = sys.executable
    if os.path.basename(executable).lower() == "pythonw.exe":
        console_python = os.path.join(os.path.dirname(executable), "python.exe")
        if os.path.exists(console_python):
            executable = console_python  # pythonw can leave sys.stdout unset, even with capture_output
    try:
        out = subprocess.run([executable, "-c", code, root], capture_output=True, text=True,
                             timeout=15, creationflags=0x08000000)
        names, default = json.loads(out.stdout.strip().splitlines()[-1])
        return names, default
    except Exception as e:
        logger.debug(f"fresh device scan failed: {e}")
        return None


def _api_index(*needles: str) -> Optional[int]:
    for i, h in enumerate(sd.query_hostapis()):
        if any(n in h["name"] for n in needles):
            return i
    return None


def _is_mic(dev: dict) -> bool:
    name = dev["name"].lower()
    return dev["max_input_channels"] > 0 and not any(bad in name for bad in _NOT_A_MIC)


def list_microphones() -> List[str]:
    """Distinct, user-meaningful microphone names (full names), best audio API first."""
    with _lock:
        devices = sd.query_devices()
        names: List[str] = []
        for api_name in ("WASAPI", "DirectSound", "MME"):
            api = _api_index(api_name)
            if api is None:
                continue
            for d in devices:
                if (d["hostapi"] == api and _is_mic(d)
                        and not any(d["name"].casefold() == n.casefold()
                                    or (api_name == "MME" and n.casefold().startswith(d["name"].casefold()))
                                    for n in names)):
                    names.append(d["name"])
        return names


def default_microphone_name() -> Optional[str]:
    with _lock:
        for api_name in ("WASAPI", "DirectSound", "MME"):
            api = _api_index(api_name)
            if api is not None:
                idx = sd.query_hostapis(api)["default_input_device"]
                if idx >= 0:
                    return sd.query_devices(idx)["name"]
        return None


def input_candidates(name: Optional[str]) -> List[Tuple[Optional[int], object]]:
    """(device index, extra_settings) options to try, best first, for opening a 16 kHz mono stream.

    name=None means "the system default microphone". WASAPI (with automatic rate conversion) is
    tried first, then the legacy APIs, which resample on their own; the final entry lets
    PortAudio pick whatever it considers default.
    """
    with _lock:
        devices = sd.query_devices()
        wasapi = _api_index("WASAPI")
        out: List[Tuple[Optional[int], object]] = []
        extra = sd.WasapiSettings(auto_convert=True) if wasapi is not None else None

        if name is not None:
            wanted = name.lower()
            for api_name in ("WASAPI", "DirectSound", "MME"):
                api = _api_index(api_name)
                if api is None:
                    continue
                matches = []
                for i, d in enumerate(devices):
                    if d["hostapi"] != api or d["max_input_channels"] <= 0:
                        continue
                    dn = d["name"].lower()
                    # MME truncates names to 31 characters, so also accept a prefix match
                    if dn == wanted or (api_name == "MME" and wanted.startswith(dn[:28])):
                        matches.append(i)
                if len(matches) > 1:
                    raise RuntimeError(f"Multiple microphones are named '{name}'. Rename them in Windows Sound settings.")
                if matches:
                    out.append((matches[0], extra if api_name == "WASAPI" else None))
        # Defaults are explicit candidates too, so the app can report the real
        # fallback endpoint rather than claiming the selected USB mic opened.
        for api_name in ("WASAPI", "DirectSound", "MME"):
            api = _api_index(api_name)
            if api is not None:
                idx = sd.query_hostapis(api)["default_input_device"]
                if idx >= 0 and not any(i == idx for i, _ in out):
                    out.append((idx, extra if api_name == "WASAPI" else None))
        out.append((None, None))
        return out
