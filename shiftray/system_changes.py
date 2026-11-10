"""Every change the app makes outside its own files, in one place: what it is, why, how risky,
whether it is in place now, and how to undo it.

Setup lists these before it asks for the password; Extra -> System changes shows them with an
Undo button; Diagnostics reports them. A new root step or desktop setting gets an entry here
first, so nothing the app does to a system is ever undocumented or impossible to take back.

The journal (~/.local/state/shiftray/changes.json) records what the app did and the values
it replaced, for the changes that cannot be seen on the system afterwards (a package swap) or that
need the old value back (KDE's power profile choice).
"""
import json
import os
import shutil
import time

from . import backend as hw
from . import conflicts
from . import hotkeys
from .i18n import tr
from .log import log

JOURNAL = os.path.expanduser("~/.local/state/shiftray/changes.json")
LOW, MEDIUM, HIGH = "low", "medium", "high"
UDEV_RULES = [f"/etc/udev/rules.d/{n}" for n in (hotkeys.RULE_NAME, hotkeys.LEGACY_RULE_NAME)]
SLEEP_GRUBS = ["/etc/default/grub.d/shiftray-sleep.cfg", "/etc/default/grub.d/asus-tso-helper-sleep.cfg"]
BOOST_FILES = [hw.BOOST_TMPFILES, hw.LEGACY_BOOST_TMPFILES]  # new name and the one asus-tso-helper used
AUTOSTART = os.path.expanduser("~/.config/autostart/shiftray.desktop")


# --- journal ----------------------------------------------------------------------------
def journal():
    try:
        with open(JOURNAL) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def record(key, **data):
    """The app made change `key` (with the values it replaced, if any)."""
    j = journal()
    j[key] = dict(data, at=time.strftime("%Y-%m-%d %H:%M"))
    _save(j)
    log.info(f"system change: {key} {data or ''}")


def forget(key):
    j = journal()
    if j.pop(key, None) is not None:
        _save(j)
        log.info(f"system change undone: {key}")


def _save(j):
    try:
        os.makedirs(os.path.dirname(JOURNAL), exist_ok=True)
        with open(JOURNAL, "w") as f:
            json.dump(j, f, indent=1)
    except OSError as err:
        log.warning(f"journal not saved: {err}")


# --- the changes -------------------------------------------------------------------------
class Change:
    """key: id. root: needs the password to apply / undo. applied(): True when in place now.
    undo_root: shell lines run as root. undo_user(): done as the user. manual: how to undo it by
    hand when the app should not do it itself (removing a package other things depend on)."""

    def __init__(self, key, title, why, risk, root, applied, undo_root=None, undo_user=None, manual=None):
        self.key, self.title, self.why, self.risk, self.root = key, title, why, risk, root
        self.applied, self.undo_root, self.undo_user, self.manual = applied, undo_root, undo_user, manual

    @property
    def can_undo(self):
        return bool(self.undo_root or self.undo_user)


def _kde_profiles_applied():
    """KDE's "leave unchanged" is an empty value; unset or a profile name is KDE's own choice."""
    for source in hw.POWERDEVIL_SOURCES:
        ok, value = hw.run("kreadconfig6", "--file", "powerdevilrc", "--group", source, "--group", "Performance",
                           "--key", "PowerProfile", "--default", "unset", timeout=5)
        if not ok or value != "":
            return False
    return True


def _terra_applied():
    try:
        return any(n.startswith("terra") for n in os.listdir("/etc/yum.repos.d"))
    except OSError:
        return False


def _rpm(name):
    return shutil.which("rpm") is not None and hw.run("rpm", "-q", name, timeout=5)[0]


def _unmask_profiles():
    names = journal().get("profiles_masked", {}).get("names") or ["power-profiles-daemon"]
    units = " ".join(n + ".service" for n in names)
    return f"systemctl unmask {units} && systemctl enable --now {units}"


def _cardwire_applied():
    return hw.run("systemctl", "is-enabled", "--quiet", "cardwired", timeout=5)[0]


def bpf_lsm_pending():
    """lsm= with bpf is set for the next boot (GRUB drop-in, or grubby's kernel entries)."""
    if os.path.exists(hw.BPF_LSM_GRUB):
        return True
    if "bpf_lsm" not in journal() or not shutil.which("grubby"):
        return False
    ok, out = hw.run("grubby", "--info=DEFAULT", timeout=5)
    return ok and any("lsm=" in a and "bpf" in a for a in out.split())


def _kde_profiles_undo():
    """KDE's own power profile choice for AC and battery comes back (the value it had before)."""
    before = journal().get("kde_profiles", {}).get("before", {})
    for source in hw.POWERDEVIL_SOURCES:
        key = ("--file", "powerdevilrc", "--group", source, "--group", "Performance", "--key", "PowerProfile")
        value = before.get(source)
        if value in (None, "unset"):
            hw.run("kwriteconfig6", *key, "--delete", timeout=5)
        else:
            hw.run("kwriteconfig6", *key, value, timeout=5)
    hw._powerdevil_reload()
    forget("kde_profiles")
    return True, ""


def _clamshell_undo():
    for source in ("AC", "Battery", "LowBattery"):
        hw.run("kwriteconfig6", *hw._clamshell_key(source), "--delete", timeout=5)
    hw._powerdevil_reload()
    return True, ""


def _gnome_tray_undo():
    forget("gnome_tray")
    return hw.set_gnome_appindicator(False)


def _desktop_files_undo():
    for path in (AUTOSTART, hw.DESKTOP_FILE, hw.ICON_FILE):
        try:
            os.remove(path)
        except OSError:
            pass
    hw.run("busctl", "--user", "call", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting",
           "unloadScript", "s", "shiftray-place", timeout=5)
    return True, ""


def _deep_sleep_applied():
    return ("mem_sleep_default=deep" in (hw._read("/proc/cmdline") or "") or any(os.path.exists(p) for p in SLEEP_GRUBS)
            or "deep_sleep" in journal())


DEEP_SLEEP_UNDO = ("echo s2idle > /sys/power/mem_sleep 2>/dev/null; "
                   "if command -v grubby >/dev/null; then grubby --update-kernel=ALL --remove-args=mem_sleep_default; "
                   f"elif ls {' '.join(SLEEP_GRUBS)} >/dev/null 2>&1; then rm -f {' '.join(SLEEP_GRUBS)} && update-grub; fi")


def changes():
    kde = "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    rows = [
        Change("keys", tr("Laptop keys rule"),
               tr("A udev rule lets you (not other users) read the ASUS hotkey device, so Fn keys work. "
                  "Normal typing is a different device and stays private."),
               LOW, True, lambda: any(os.path.exists(p) for p in UDEV_RULES),
               undo_root=f"rm -f {' '.join(UDEV_RULES)} && udevadm control --reload && udevadm trigger -s input"),
        Change("boost", tr("CPU Boost switch permission"),
               tr("Your user group may turn CPU boost on and off, so each mode can have its own."),
               LOW, True, lambda: any(os.path.exists(p) for p in BOOST_FILES),
               undo_root=f"rm -f {' '.join(BOOST_FILES)} && chown root:root {hw.BOOST} && chmod 0644 {hw.BOOST}"),
        Change("music", tr("Keyboard color permission (Music effect)"),
               tr("A udev rule lets your user group set the keyboard color directly, so the Music effect "
                  "can change it many times a second without asusd writing its config file each time."),
               LOW, True, lambda: os.path.exists(hw.MUSIC_RULE), undo_root=hw.MUSIC_UNDO),
        Change("deep_sleep", tr("Deep sleep (kernel option)"),
               tr("Adds mem_sleep_default=deep to the kernel command line, so the laptop sleeps in S3."),
               HIGH, True, _deep_sleep_applied, undo_root=DEEP_SLEEP_UNDO),
        Change("terra", tr("Terra repository"),
               tr("Fedora: asusctl and Cardwire come from Terra (Fyra Labs). Installed the way Terra's own "
                  "guide does it."),
               MEDIUM, True, _terra_applied,
               manual="sudo dnf remove terra-release"),
        Change("profiles_masked", tr("Power profile daemons masked (asusd only)"),
               tr("asusd manages the power mode itself; power-profiles-daemon or TuneD next to it race over "
                  "the platform profile and CPU EPP, so they are stopped and masked, as asusctl's guide says. "
                  "Undo starts them again (KDE's power profile slider comes back with them)."),
               MEDIUM, True, lambda: "profiles_masked" in journal(),
               undo_root=_unmask_profiles()),
        Change("ogc", tr("OpenGamingCollective repository (Arch)"),
               tr("Arch: asusctl and Cardwire come from OpenGamingCollective's pacman repository, added the way "
                  "asusctl's guide does it ([ogc] in /etc/pacman.conf and its signing key)."),
               MEDIUM, True, lambda: "ogc" in journal(),
               manual="sudo sed -i '/^\\[ogc\\]/,/^Server/d' /etc/pacman.conf && "
                      "sudo pacman-key --delete F79100EF8C802DAB81C323BB8EEA5962FE510E19"),
        Change("tuatara", tr("Tuatara repository (openSUSE)"),
               tr("openSUSE Tumbleweed: asusctl comes from Tuatara (Fyra Labs), added the way asusctl's guide does it."),
               MEDIUM, True, lambda: "tuatara" in journal(),
               manual="sudo zypper remove tuatara-release"),
        Change("ppd", tr("power-profiles-daemon instead of tuned-ppd"),
               tr("Older versions of the app swapped Fedora's tuned-ppd for power-profiles-daemon. asusctl now "
                  "recommends neither next to asusd: Setup can mask it. Undo brings tuned-ppd back."),
               MEDIUM, True, lambda: "ppd" in journal(),
               undo_root="dnf swap -y power-profiles-daemon tuned-ppd --allowerasing && systemctl enable --now tuned",
               manual="sudo dnf swap power-profiles-daemon tuned-ppd --allowerasing"),
        Change("cardwire", tr("Cardwire instead of supergfxd"),
               tr("GPU modes are switched by Cardwire; supergfxd is turned off so the two never fight over "
                  "the GPU. On Fedora Cardwire also replaces switcheroo-control."),
               MEDIUM, True, _cardwire_applied,
               manual="sudo systemctl disable --now cardwired && sudo systemctl enable --now supergfxd"),
        Change("bpf_lsm", tr("BPF LSM (kernel option)"),
               tr("Adds bpf to the lsm= option on the kernel command line (the security modules the kernel "
                  "already ran stay as they were), so Cardwire can block the NVIDIA GPU in Eco. Ubuntu and "
                  "Debian build BPF LSM into the kernel but leave it off. Takes effect after a restart."),
               HIGH, True, lambda: "bpf_lsm" in journal() or bpf_lsm_pending(), undo_root=hw.BPF_LSM_UNDO),
        Change("cardwire_deb", tr("Cardwire package (.deb)"),
               tr("Debian and Ubuntu have no Cardwire package: Setup installs the .deb of its latest release "
                  "from Cardwire's GitHub page, with apt. It does not update with the system: Setup or the "
                  "releases page has newer ones."),
               MEDIUM, True, lambda: "cardwire_deb" in journal() and shutil.which("cardwire") is not None,
               manual="sudo apt remove cardwire"),
        Change("packages", tr("Installed packages"),
               tr("asusctl, python3-evdev / python3-dbus, swayidle, GNOME's AppIndicator extension, as needed. Nothing else depends on the app."),
               LOW, True, lambda: "packages" in journal(),
               manual=lambda: ("sudo dnf remove " if shutil.which("dnf") else "sudo pacman -R " if shutil.which("pacman")
                              else "sudo apt remove ") + " ".join(journal().get("packages", {}).get("names", []))),
    ]
    if kde:
        rows += [
            Change("kde_profiles", tr("KDE power profile on plug / unplug"),
                   tr("KDE's own switch is set to \"leave unchanged\", so it does not fight the mode this app "
                      "remembers per power source."),
                   LOW, False, _kde_profiles_applied, undo_user=_kde_profiles_undo),
            Change("powerdevil", tr("KDE power management without ddcutil"),
                   tr("PowerDevil crashed at start while Eco hides the NVIDIA GPU (a ddcutil bug, fixed in "
                      "ddcutil 3.0), leaving no battery icon, dimming or lid action. It now starts without "
                      "ddcutil; only external monitors' brightness over DDC is lost."),
                   LOW, False, lambda: any(os.path.exists(p) for p in hw.POWERDEVIL_DROPINS),
                   undo_user=lambda: hw.set_powerdevil_ddcutil(False)),
            Change("clamshell", tr("Keep working with the lid closed"),
                   tr("KDE setting you can turn on in Extra."),
                   LOW, False, lambda: bool(hw.clamshell()) and "clamshell" in journal(), undo_user=_clamshell_undo),
        ]
    if hw.is_gnome():
        rows.append(Change("gnome_tray", tr("Tray icons on GNOME"),
                           tr("GNOME's AppIndicator extension is turned on, so the app (and others) can show "
                              "an icon in the top bar."),
                           LOW, False, lambda: "gnome_tray" in journal() and hw.gnome_tray() in ("on", "login"),
                           undo_user=_gnome_tray_undo))
    # services Setup stopped because they fight asusd / Cardwire (conflicts.py)
    for c in conflicts.CONFLICTS:
        if c.service:
            rows.append(Change("stopped:" + c.key, tr("{name} stopped", name=c.title), c.why, LOW, True,
                               lambda c=c: "stopped:" + c.key in journal(),
                               undo_root=f"systemctl enable --now {c.service}"))
    rows.append(Change("desktop", tr("Autostart, menu entry and window placement script"),
                       tr("In your home folder only: start with the session, an app menu entry (AppImage) and a "
                          "KWin script that opens windows at the tray icon."),
                       LOW, False, lambda: any(os.path.exists(p) for p in (AUTOSTART, hw.DESKTOP_FILE)),
                       undo_user=_desktop_files_undo))
    return rows


def manual_text(change):
    return change.manual() if callable(change.manual) else change.manual


def undo(keys):
    """Undo these changes: the user's ones directly, the root ones in one password prompt.
    -> (ok, output)."""
    rows = [c for c in changes() if c.key in keys and c.can_undo]
    out, ok = [], True
    for c in rows:
        if c.undo_user:
            r_ok, r_out = c.undo_user()
            ok, out = ok and r_ok, out + [r_out] if r_out else out
    root = [c for c in rows if c.undo_root]
    if root:
        script = "set -u\n" + "\n".join(f"echo '== {c.key}'\n{c.undo_root} || exit 1" for c in root) + "\n"
        r_ok, r_out = hw.run("pkexec", "sh", "-c", script, timeout=600)
        ok = ok and r_ok
        out.append(r_out)
        if r_ok:
            for c in root:
                forget(c.key)
    return ok, "\n".join(o for o in out if o)


def report():
    """Lines for Diagnostics."""
    lines = []
    for c in changes():
        try:
            state = "applied" if c.applied() else "-"
        except Exception as err:  # a report must not fail
            state = f"? ({err})"
        lines.append(f"{c.key}: {state}")
    j = journal()
    if j:
        lines.append("journal: " + json.dumps(j, sort_keys=True))
    return lines


def available():
    """Root undo needs pkexec; KDE ones need kwriteconfig6."""
    return bool(shutil.which("pkexec"))
