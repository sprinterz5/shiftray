"""Shared helpers: reading sysfs, running commands (logged), asusd's D-Bus properties."""
import glob
import os
import shutil
import subprocess
import time

from ..log import log


PROFILE_SYSFS = "/sys/firmware/acpi/platform_profile"
ARMOURY = "/sys/class/firmware-attributes/asus-armoury/attributes"
KBD_LED = "/sys/class/leds/asus::kbd_backlight"
# platform_profile name -> asusctl name
PROFILES = {"quiet": "Quiet", "balanced": "Balanced", "performance": "Performance"}
KBD_LEVELS = ["off", "low", "med", "high"]


def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def _read_int(path, default=None):
    value = _read(path)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# Ubuntu: asusctl's guide installs it with Homebrew, whose bin is not on PATH when the app starts
# from autostart or the menu
BREW_BIN = "/home/linuxbrew/.linuxbrew/bin"
if os.path.isdir(BREW_BIN) and BREW_BIN not in os.environ.get("PATH", "").split(":"):
    os.environ["PATH"] = os.environ.get("PATH", "/usr/bin") + ":" + BREW_BIN


def run(*cmd, timeout=15):
    """Run a command, return (ok, combined output). Every call is in the --debug log."""
    if not shutil.which(cmd[0]):
        log.debug(f"run {cmd[0]}: not found")
        return False, f"{cmd[0]} not found"
    start = time.monotonic()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log.warning(f"run {' '.join(cmd)}: timed out after {timeout}s")
        return False, f"{cmd[0]} timed out"
    out = (p.stdout + p.stderr).strip()
    log.debug(f"run {' '.join(cmd)} -> {p.returncode} in {time.monotonic() - start:.2f}s"
              + (f": {out[:300]}" if out else ""))
    if cmd[0] == "pkexec":  # a root step: always worth a line
        log.info(f"root step -> {p.returncode}: {out[-500:]}")
    return p.returncode == 0, out


_found = {}  # what _cached found: key -> (path or None, when it looked)


def _cached(key, find, retry=60):
    """A sysfs path that takes a scan to find (a hwmon by name, the dGPU's PCI device). It is kept
    while it exists, since hwmon numbers and PCI addresses hold until a reboot; when there was none,
    the scan is repeated only after retry seconds. The 2-second refresh calls these every time."""
    path, at = _found.get(key, (None, None))
    if path and os.path.exists(path):
        return path
    if path is None and at is not None and time.monotonic() - at < retry:
        return None
    path = find()
    _found[key] = (path, time.monotonic())
    return path


def _hwmon(name):
    def find():
        for path in glob.glob("/sys/class/hwmon/hwmon*"):
            if _read(os.path.join(path, "name")) == name:
                return path
        return None
    return _cached(("hwmon", name), find)


def _bus(*args):
    return run("busctl", "--system", *args, timeout=5)


def _bus_get(path, iface, prop):
    ok, out = _bus("get-property", "xyz.ljones.Asusd", path, iface, prop)
    if not ok:
        return None
    kind, _, value = out.partition(" ")
    if kind == "b":
        return value == "true"
    if kind in ("i", "u", "y"):
        return int(value)
    return value


def platform_get(prop):
    """asusd's xyz.ljones.Platform property (charge limit, EPP per profile, ...)."""
    return _bus_get("/xyz/ljones", "xyz.ljones.Platform", prop)


def platform_set(prop, kind, value):
    if kind == "b":
        value = "true" if value else "false"
    return _bus("set-property", "xyz.ljones.Asusd", "/xyz/ljones", "xyz.ljones.Platform", prop, kind, str(value))
