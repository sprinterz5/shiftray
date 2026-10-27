"""The app's name, version and links, and its settings file (~/.config/shiftray/config.json)."""
import json
import os

from PySide6 import QtWidgets

APP = "shiftray"  # id: paths, the .desktop file, the window class
NAME = "Shiftray"  # what people see
VERSION = "0.13.1"
HOMEPAGE = "https://github.com/sprinterz5/shiftray"
DONATE_URL = ""  # PayPal donation link; Credits shows a support link once it is set
CONFIG_DIR = os.path.join(os.path.expanduser("~/.config"), APP)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
AUTOSTART = os.path.expanduser(f"~/.config/autostart/{APP}.desktop")


def quit_app():
    """Ends the app. QApplication.quit() would not: in Qt 6 it first asks every window to close,
    and the main window refuses (it hides to the tray), which cancels the quit silently."""
    QtWidgets.QApplication.exit(0)


def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
