"""Keyboard backlight level, Aura effects and the idle timeout."""
import glob
import os
import re

from .common import KBD_LED, KBD_LEVELS, _read, _read_int, run


def get_kbd_level():
    value = _read_int(os.path.join(KBD_LED, "brightness"))
    if value is None:
        return None
    return KBD_LEVELS[max(0, min(value, 3))]


def set_kbd_level(level):
    return run("asusctl", "leds", "set", level)


def set_aura(effect, colour, speed="med"):
    """effect: static | breathe | pulse (ASUS "Strobe") | rainbow-cycle. colour: 'rrggbb'."""
    if effect == "rainbow-cycle":
        return run("asusctl", "aura", "effect", "rainbow-cycle", "--speed", speed)
    if effect == "breathe":
        return run("asusctl", "aura", "effect", "breathe", "--colour", colour,
                   "--colour2", "000000", "--speed", speed)
    if effect == "pulse":
        return run("asusctl", "aura", "effect", "pulse", "-c", colour)
    return run("asusctl", "aura", "effect", "static", "-c", colour)


AURA_SPEEDS = ["low", "med", "high"]
# asusd's AuraModeNum values for the effects the app offers
AURA_MODE_NUMS = {"static": 0, "breathe": 1, "rainbow-cycle": 2, "pulse": 10}
_aura_modes = []


def aura_modes():
    """The effects asusd allows on this laptop (its SupportedBasicModes, from aura_support.ron),
    or None when it cannot be asked. A model missing from that file gets Static only, and asusd
    ignores the other effects, so they are not offered. Asked once: it does not change while asusd runs."""
    if _aura_modes:
        return _aura_modes[0]
    ok, out = run("busctl", "--system", "--list", "tree", "xyz.ljones.Asusd", timeout=5)
    paths = re.findall(r"/xyz/ljones/aura/\S+", out) if ok else []
    if not paths:
        return None
    ok, out = run("busctl", "--system", "get-property", "xyz.ljones.Asusd", paths[0], "xyz.ljones.Aura",
                  "SupportedBasicModes", timeout=5)
    m = re.match(r"au \d+((?: \d+)*)", out.strip()) if ok else None
    if not m:
        return None
    nums = {int(n) for n in m.group(1).split()}
    _aura_modes.append([k for k, n in AURA_MODE_NUMS.items() if n in nums] or ["static"])
    return _aura_modes[0]


def _aura_config():
    for path in sorted(glob.glob("/etc/asusd/aura_*.ron")):
        text = _read(path)
        if text:
            return text
    return ""


def get_aura_power():
    """Keyboard zone power states from asusd's config -> dict(boot, awake, sleep, shutdown)."""
    text = _aura_config()
    m = re.search(r"zone:\s*Keyboard,(.*?)\)", text, re.S)
    if not m:
        return None
    return {k: v == "true" for k, v in re.findall(r"(boot|awake|sleep|shutdown):\s*(true|false)", m.group(1))}


def set_aura_power(awake, boot, sleep):
    flag = lambda b: "true" if b else "false"
    return run("asusctl", "aura", "power-tuf", "--awake", flag(awake), "--keyboard",
               "--boot", flag(boot), "--sleep", flag(sleep))


def get_aura_speed():
    """Speed of the current aura mode ('low' | 'med' | 'high')."""
    text = _aura_config()
    mode = re.search(r"current_mode:\s*(\w+)", text)
    if not mode:
        return None
    block = re.search(r"\b" + mode.group(1) + r":\s*\((.*?)direction", text, re.S)
    speed = re.search(r"speed:\s*(\w+)", block.group(1)) if block else None
    return speed.group(1).lower() if speed else None


KBD_IDLE_FILE = os.path.expanduser("~/.cache/shiftray/kbd-level")


def kbd_idle(state):
    """Called by swayidle: 'off' remembers the backlight level and turns it off, 'on' restores it."""
    if state == "off":
        level = get_kbd_level()
        if level and level != "off":
            os.makedirs(os.path.dirname(KBD_IDLE_FILE), exist_ok=True)
            with open(KBD_IDLE_FILE, "w") as f:
                f.write(level)
            set_kbd_level("off")
    elif state == "on":
        level = _read(KBD_IDLE_FILE)
        if level in KBD_LEVELS:
            set_kbd_level(level)
        try:
            os.remove(KBD_IDLE_FILE)
        except OSError:
            pass
