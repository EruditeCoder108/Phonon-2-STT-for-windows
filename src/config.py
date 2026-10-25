"""
Configuration Manager for Phonon-2 Dictation App
"""

import json
import os
import logging

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".phonon2_config.json")

DEFAULT_PHONON_MODEL = "phonon-2.5-en"
PHONON_MODELS = {
    "phonon-2.5-en": "Phonon-2.5 English",
    "phonon-2.5": "Phonon-2.5 • 25 languages",
    "phonon-2": "Phonon-2 • legacy",
}
PHONON_MODEL_REPOS = {
    "phonon-2.5-en": "FermionResearch/Phonon-2.5-English",
    "phonon-2.5": "FermionResearch/Phonon-2.5",
    "phonon-2": "FermionResearch/Phonon-2",
}

DEFAULT_CONFIG = {
    "trigger_key": "ctrl+space",
    "push_to_talk": False,
    "injection_mode": "auto",    # "auto" (type; paste only very long text), "type", "paste"
    "sound_theme": "glass",      # "glass", "wood", "air"
    "sound_volume": 60,          # 0 - 100
    "pause_ms": 700,             # silence that ends an utterance (400 - 1500)
    "context_stitch": True,      # decode with the previous phrase as audio context (fixes "Period. Capital." at pauses)
    "voice_gate": True,          # treat speech much quieter than yours (video, people nearby) as background
    "remove_fillers": True,      # drop "uh"/"um" the model writes inside sentences
    "save_history": True,        # persist dictated text to ~/.phonon2_history.json
    "engine_threads": None,      # speech-server CPU threads (None = server default)
    "speech_engine": "phonon",   # "phonon" or "whistle"; existing installs retain Phonon
    "phonon_model": DEFAULT_PHONON_MODEL,
    "live_typing": True,         # recognize previews while speech is in progress
    "dictation_mode": "accurate",  # accurate: commit final phrases; fast: provisional words
    "recognition_terms": [],     # optional Phonon recognition bias, separate from replacements
    "show_preview": True,        # partial words preview; only complete phrases type by default
    "strip_outer_quotes": True,  # remove quotation marks wrapping a whole dictated phrase
    "mic_name": None,            # chosen microphone by name (None = system default)
    "port": 8010,
    "color_theme": "rainbow",    # "rainbow", "cyan_blue", "gold_fire", "emerald", "violet", "custom"
    "custom_hue": 195,           # 0 - 360
    "animation_style": "liquid", # "liquid", "glow_only"
    "scale_reactivity": "normal",# "none", "subtle", "normal", "high"
    "speed_pace": "balanced",    # "relaxed", "balanced", "fast"
    "orb_base_size": 70,         # 45 - 100 (% scale)
    "orb_opacity": 100,          # 20 - 100 (% opacity)
    "autostart": False,
}


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("Configuration must be a JSON object")
                config = {**DEFAULT_CONFIG, **data}
                if "dictation_mode" not in data:
                    # Older profiles typed partials directly, so their default
                    # hidden preview would leave complete-phrase mode without
                    # feedback. Apply once; explicit preferences in new profiles
                    # remain authoritative on subsequent loads.
                    config["show_preview"] = True
                if not isinstance(config["phonon_model"], str) or config["phonon_model"] not in PHONON_MODELS:
                    config["phonon_model"] = DEFAULT_PHONON_MODEL
                if config.get("dictation_mode") not in ("accurate", "fast"):
                    config["dictation_mode"] = "accurate"
                config["recognition_terms"] = recognition_terms(config.get("recognition_terms"))
                return config
        except Exception as e:
            logger.error(f"Error loading config, using defaults: {e}")
    return DEFAULT_CONFIG.copy()


def recognition_terms(value):
    """Normalize a bounded immutable request snapshot; tolerate old/bad profiles."""
    if not isinstance(value, (list, tuple)):
        return []
    result, seen = [], set()
    for term in value:
        if not isinstance(term, str):
            continue
        term = " ".join(term.split())[:80]
        if term and term.casefold() not in seen:
            result.append(term)
            seen.add(term.casefold())
        if len(result) == 25:
            break
    return result


def save_config(cfg: dict):
    """Writes atomically so a crash mid-save can never leave a truncated config."""
    tmp_path = CONFIG_PATH + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp_path, CONFIG_PATH)
    except Exception as e:
        logger.error(f"Error saving config: {e}")


# ── Windows Autostart Registry Management ──
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_REG_NAME = "Phonon2Dictation"


def set_autostart_registry(enable: bool) -> bool:
    """Configures the current user Run registry key for automatic Windows startup."""
    import winreg
    import sys

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                venv_dir = os.path.dirname(sys.executable)
                pythonw = os.path.join(venv_dir, "pythonw.exe")
                if not os.path.exists(pythonw):
                    pythonw = sys.executable
                script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "run.py"))
                cmd = f'"{pythonw}" "{script_path}"'
                winreg.SetValueEx(key, APP_REG_NAME, 0, winreg.REG_SZ, cmd)
                logger.info(f"Registered Windows autostart: {cmd}")
            else:
                try:
                    winreg.DeleteValue(key, APP_REG_NAME)
                    logger.info("Unregistered Windows autostart.")
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        logger.error(f"Error setting autostart registry: {e}")
        return False


def get_autostart_registry() -> bool:
    """Checks whether the application is registered for Windows startup."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_REG_NAME)
            return True
    except (FileNotFoundError, OSError):
        return False
