"""
Configuration Manager for Phonon-2 Dictation App
"""

import json
import os
import logging

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".phonon2_config.json")

DEFAULT_CONFIG = {
    "trigger_key": "ctrl+space",
    "push_to_talk": False,
    "prefer_paste": True,
    "mic_index": None,
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
                return {**DEFAULT_CONFIG, **data}
        except Exception as e:
            logger.error(f"Error loading config, using defaults: {e}")
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
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
