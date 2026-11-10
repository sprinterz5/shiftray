"""First-run setup: checks what the app needs and fixes it behind one password prompt.

G-Helper has no such window because Windows ships ASUS's services; on Linux they come from the
asus-linux project (asusd), Cardwire (GPU modes) and a few small tools. The fix is one root shell script
for the distribution's package manager, run through pkexec, with its output shown live.
"""
import importlib.util
import os
import shutil

from PySide6 import QtCore, QtGui, QtWidgets

from . import backend as hw
from . import hotkeys
from . import widgets as w
from .widgets import ECO, TURBO
from .i18n import tr
from . import system_changes
from . import conflicts

REQUIRED, RECOMMENDED, OPTIONAL, INFO = "required", "recommended", "optional", "info"
USER_STEPS = {"powerdevil", "tray"}  # done as the user, in the home folder: no password
OLD_COPR = "/etc/yum.repos.d/_copr:copr.fedorainfracloud.org:lukenukem:asus-linux.repo"
TERRA_INSTALL = ("dnf install -y --nogpgcheck --repofrompath "
                 "'terra,https://repos.fyralabs.com/terra$releasever' terra-release")
NVIDIA_HOWTO = "https://rpmfusion.org/Howto/NVIDIA"
CARDWIRE_RELEASES = "https://api.github.com/repos/OpenGamingCollective/cardwire/releases/latest"
CARDWIRE_DOCS = "https://opengamingcollective.github.io/cardwire/getting-started/installation.html"
ASUS_LINUX = "https://asus-linux.org/guides/"


def os_release():
    info = {}
    try:
        with open("/etc/os-release") as f:
            for line in f:
                key, _, value = line.strip().partition("=")
                info[key] = value.strip('"')
    except OSError:
        pass
    return info


def distro():
    """'fedora', 'arch', 'debian', 'suse' or '' (by ID and ID_LIKE)."""
    rel = os_release()
    ids = (rel.get("ID", "") + " " + rel.get("ID_LIKE", "")).split()
    for family in ("fedora", "arch", "debian", "suse"):
        if family in ids or (family == "debian" and "ubuntu" in ids) or (family == "suse" and any("suse" in i for i in ids)):
            return family
    return ""


def is_asus():
    return "asus" in (hw._read("/sys/class/dmi/id/sys_vendor") or "").lower()


def has_dgpu():
    """A discrete GPU for Eco / Standard (NVIDIA or AMD, or the dGPU switch in the firmware)."""
    return hw.has_dgpu()


def service_active(name):
    ok, out = hw.run("systemctl", "is-active", name, timeout=5)
    return out.strip() == "active"


def rpm_installed(name):
    return shutil.which("rpm") is not None and hw.run("rpm", "-q", name, timeout=5)[0]


def unit_state(name):
    """systemctl is-enabled: 'enabled', 'disabled', 'masked', 'static', ... or '' when not installed."""
    ok, out = hw.run("systemctl", "is-enabled", name + ".service", timeout=5)
    state = out.strip().splitlines()[-1] if out.strip() else ""
    return "" if state in ("not-found", "") or "No such file" in out else state


def profile_daemons():
    """Power profile daemons that would set the power mode next to asusd: running, or able to start
    (power-profiles-daemon also starts on D-Bus when KDE's PowerDevil asks for it, even disabled).
    TuneD on its own (without tuned-ppd) is a conflict row of its own (conflicts.py)."""
    found = [u for u in ("power-profiles-daemon", "tuned-ppd") if unit_state(u) not in ("", "masked")]
    if "tuned-ppd" in found and unit_state("tuned") not in ("", "masked"):
        found.append("tuned")
    return found


def check(key, title, ok, level, detail="", fix=True):
    return {"key": key, "title": tr(title), "ok": ok, "level": level, "detail": tr(detail) if detail else "",
            "fix": fix}


def checks():
    """What the app needs, as rows for the window and input for script()."""
    family = distro()
    rows = []
    if not is_asus():
        rows.append(check("asus", "ASUS laptop", False, REQUIRED,
                          "This does not look like an ASUS laptop; most controls will not work.", fix=False))
        return rows
    if family == "fedora":
        terra = any(n.startswith("terra") for n in os.listdir("/etc/yum.repos.d"))
        rows.append(check("terra", "ASUS Linux repository (Terra)", terra, RECOMMENDED,
                          "The old COPR repository no longer gets updates; moves to Terra."
                          if os.path.exists(OLD_COPR) else "Where asusctl and Cardwire come from on Fedora."))
    asusd = shutil.which("asusctl") is not None and service_active("asusd")
    rows.append(check("asusd", "asusd (modes, fans, keyboard, battery)", asusd, REQUIRED,
                      "" if asusd else "asusctl is missing or its service is not running."))
    if has_dgpu():
        bpf = hw.bpf_lsm()
        bpf_soon = not bpf and system_changes.bpf_lsm_pending()  # set up, waiting for the restart
        if not bpf:
            fixable = hw.bpf_lsm_fixable()
            rows.append(check("bpf_lsm", "BPF LSM (kernel option for Cardwire)", bpf_soon, RECOMMENDED,
                              "Set up: works after the next restart." if bpf_soon else
                              "Cardwire blocks the NVIDIA GPU through it. Ubuntu and Debian build it into the "
                              "kernel but leave it off: adds bpf to the lsm= boot option. Works after a restart."
                              if fixable else
                              tr("The kernel runs without BPF LSM, which Cardwire needs: see {url}", url=CARDWIRE_DOCS),
                              fix=fixable))
            bpf = fixable or bpf_soon  # Cardwire can be installed now and starts after the restart
        old = service_active("supergfxd")
        gfx = shutil.which("cardwire") is not None and service_active("cardwired") and not old
        if bpf_soon and shutil.which("cardwire") is not None:
            gfx = True  # installed; cardwired starts once the kernel has BPF LSM
        detail = ("" if gfx else
                  "Replaces supergfxd, which asus-linux retired: Eco and Standard switch at once, "
                  "without a logout." if old else
                  "Needed for Eco / Standard. Installed from Cardwire's own release page (.deb)."
                  if family == "debian" else "Needed for Eco / Standard.")
        if not bpf:
            detail = tr("The kernel runs without BPF LSM, which Cardwire needs: see {url}", url=CARDWIRE_DOCS)
        rows.append(check("cardwire", "Cardwire (GPU modes)", gfx, RECOMMENDED, detail, fix=bpf))
    daemons = profile_daemons()
    if daemons:  # asusctl's guide: let asusd manage the profiles, mask the external daemon
        row = check("ppd", "Power profiles: asusd only", False, RECOMMENDED,
                    tr("{names} sets the power mode too, and races asusd over the platform profile and CPU EPP "
                       "(asusctl's guide). Turns it off and masks it, so KDE cannot start it again. KDE's "
                       "battery applet then has no power profile slider: the app's modes take its place.",
                       names=", ".join(daemons)))
        row["units"] = daemons
        rows.append(row)
    rows.append(check("keys", "Laptop keys (Fn+F4 and others)",
                      any(os.path.exists(f"{d}/{name}") for d in ("/etc/udev/rules.d", "/usr/lib/udev/rules.d")
                          for name in (hotkeys.RULE_NAME, hotkeys.LEGACY_RULE_NAME))
                      and hotkeys.find_device() is not None,  # the packages ship the rule in /usr/lib
                      RECOMMENDED, "Lets the app read the ASUS hotkeys; nothing else from the keyboard."))
    if hw.cpu_boost() is not None:
        rows.append(check("boost", "CPU Boost switch", hw.boost_writable(), OPTIONAL,
                          "Lets the app turn CPU boost on or off per mode (Fans → Advanced)."))
    if hw.music_possible():
        rows.append(check("music", "Music keyboard effect", hw.music_writable(), OPTIONAL,
                          "Lets the keyboard color follow the sound the laptop plays (Extra → Laptop Backlight)."))
    if not all(importlib.util.find_spec(m) for m in ("evdev", "dbus")):
        rows.append(check("python", "Python modules (evdev, dbus)", False, RECOMMENDED,
                          "For key bindings and keyboard shortcuts."))
    rows.append(check("swayidle", "swayidle", shutil.which("swayidle") is not None, OPTIONAL,
                      "For the keyboard backlight timeout."))
    nvidia_gpu = any(hw._read(v) == "0x10de" for v in __import__("glob").glob("/sys/bus/pci/devices/*/vendor"))
    if nvidia_gpu:
        driver = os.path.exists("/sys/module/nvidia") or hw.run("modinfo", "nvidia", timeout=5)[0]
        rows.append(check("nvidia", "NVIDIA driver", driver, INFO,
                          "" if driver else "Not installed: needed for games on the NVIDIA GPU and its "
                          "temperature. Install it from your distribution (Fedora: RPM Fusion).", fix=False))
    # other tools changing the same things: Set up stops the services; the rest say how to remove them
    for c in conflicts.found():
        row = {"key": "conflict:" + c.key, "title": (tr("{name} is also running", name=c.title) if c.service
                          else tr("{name} is also installed", name=c.title)), "ok": False,
               "level": RECOMMENDED, "fix": bool(c.service), "conflict": c,
               "detail": c.why + (" " + tr("Set up stops it (Undo starts it again).") if c.service else "")}
        rows.append(row)
    if hw.powerdevil_ddcutil_crash():
        rows.append(check("powerdevil", "KDE power management in Eco",
                          any(os.path.exists(p) for p in hw.POWERDEVIL_DROPINS), RECOMMENDED,
                          "PowerDevil crashes at start while Eco hides the NVIDIA GPU (a ddcutil bug, fixed in "
                          "ddcutil 3.0): no battery icon, dimming or lid action. Starts it without ddcutil; "
                          "only external monitors' brightness over DDC is lost. No password."))
    tray = hw.gnome_tray()
    if tray is not None:
        rows.append(check("tray", "Tray icon on GNOME", tray in ("on", "login"), RECOMMENDED,
                          "Shown after the next login." if tray == "login" else
                          "GNOME shows no tray icons by itself; the AppIndicator extension (the one Ubuntu "
                          "ships) adds them. Turned on for you; a new install shows up after the next login."))
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    if "KDE" not in desktop.upper():
        rows.append(check("kde", "KDE Plasma", False, INFO,
                          tr("Running on {desktop}: windows open where {desktop} places them, not at the tray icon.",
                             desktop=desktop or tr("another desktop")), fix=False))
    return rows


def todo_changes(todo):
    """Setup rows to fix -> the system_changes keys they make."""
    keys = {k for k in todo if k in ("terra", "cardwire", "keys", "boost", "bpf_lsm", "music")}
    if "ppd" in todo:
        keys.add("profiles_masked")
    if "cardwire" in todo and distro() == "debian":
        keys.add("cardwire_deb")
    if "tray" in todo:
        keys.add("gnome_tray")
    keys |= {"stopped:" + k.split(":", 1)[1] for k in todo if k.startswith("conflict:")}
    if todo & {"asusd", "python", "swayidle"}:
        keys.add("packages")
    return keys


def tray_package(todo):
    """The AppIndicator extension, when the tray row is fixed and it is not installed yet."""
    return [hw.APPINDICATOR_PACKAGE] if "tray" in todo and not hw.appindicator_installed() else []


def packages_of(todo):
    names = {"asusd": ["asusctl"], "python": ["python3-evdev", "python3-dbus"], "swayidle": ["swayidle"]}
    return [p for k in sorted(todo) for p in names.get(k, [])]


def cardwire_deb_lines():
    """Debian and Ubuntu have no Cardwire package: the .deb of its latest stable release, from
    Cardwire's own GitHub page (as its guide says), installed with apt so apt can remove it."""
    return [
        "if ! command -v cardwire >/dev/null; then",
        "  fetch() { curl -fsSL \"$1\" 2>/dev/null || wget -qO- \"$1\"; }",
        f"  url=$(fetch {CARDWIRE_RELEASES} | grep -o \"https://[^\\\"]*_$(dpkg --print-architecture)\\.deb\" | head -1)",
        "  tmp=$(mktemp -d)",
        "  if [ -n \"$url\" ] && echo \"$url\" && fetch \"$url\" > \"$tmp/cardwire.deb\" && [ -s \"$tmp/cardwire.deb\" ]; then",
        "    chmod 755 \"$tmp\"; chmod 644 \"$tmp/cardwire.deb\"  # apt's _apt user reads it",
        "    apt-get install -y \"$tmp/cardwire.deb\" || exit 1",
        "  else",
        f"    echo 'Cardwire could not be downloaded: install the .deb from its releases page, see {CARDWIRE_DOCS}'",
        "  fi",
        "  rm -rf \"$tmp\"",
        "fi",
    ]


def needs_setup(rows):
    return any(not r["ok"] and r["level"] in (REQUIRED, RECOMMENDED) for r in rows)


def script(rows):
    """One root shell script for everything fixable; each step reports itself."""
    family = distro()
    todo = {r["key"] for r in rows if not r["ok"] and r["fix"]}
    lines = ["set -u", 'step() { echo; echo "== $1"; }']
    if family == "fedora":
        pkgs = []
        if "terra" in todo:
            lines += ["step 'Terra repository'", f"{TERRA_INSTALL} || exit 1"]
            if os.path.exists(OLD_COPR):
                lines += [f"rm -f '{OLD_COPR}'", "dnf upgrade -y asusctl"]
        if "asusd" in todo:
            pkgs.append("asusctl")
        if "python" in todo:
            pkgs += ["python3-evdev", "python3-dbus"]
        pkgs += [p for p in ("swayidle",) if p in todo] + tray_package(todo)
        if pkgs:
            lines += [f"step 'Installing {' '.join(pkgs)}'", f"dnf install -y {' '.join(pkgs)}"]
        if "cardwire" in todo:
            # Cardwire also answers switcheroo-control's D-Bus API, so it replaces that package
            lines += ["step 'Cardwire'", "dnf install -y --allowerasing cardwire || exit 1"]
    elif family == "arch":
        pkgs = (["asusctl"] if "asusd" in todo else []) + (["python-evdev", "python-dbus"] if "python" in todo else [])
        pkgs += [p for p in ("swayidle",) if p in todo] + tray_package(todo)
        if pkgs:
            lines += [f"step 'Installing {' '.join(pkgs)}'", f"pacman -S --needed --noconfirm {' '.join(pkgs)}"]
        if "cardwire" in todo:
            lines += ["step 'Cardwire'", "command -v cardwire >/dev/null || { pacman -Si cardwire >/dev/null 2>&1 && "
                      "pacman -S --needed --noconfirm cardwire; } || echo 'Cardwire is in the AUR "
                      f"(yay -S cardwire): see {CARDWIRE_DOCS}'"]
    elif family in ("debian", "suse"):
        tool = "apt-get install -y" if family == "debian" else "zypper --non-interactive install"
        pkgs = (["python3-evdev", "python3-dbus"] if "python" in todo else []) + [p for p in ("swayidle",) if p in todo]
        pkgs += tray_package(todo)
        if pkgs:
            lines += [f"step 'Installing {' '.join(pkgs)}'", f"{tool} {' '.join(pkgs)}"]
        if "asusd" in todo:
            lines += [f"echo 'asusctl is not packaged here: see {ASUS_LINUX}'"]
        if "cardwire" in todo and family == "debian":
            lines += ["step 'Cardwire'", *cardwire_deb_lines()]
    if "bpf_lsm" in todo:
        lines += ["step 'BPF LSM (boot option)'", hw.bpf_lsm_script() + " || exit 1"]
    # restart, not just start: after an upgrade the old daemon would keep running
    if todo & {"asusd", "terra"}:
        lines += ["step 'asusd'", "systemctl enable asusd 2>/dev/null; systemctl restart asusd"]
    if "cardwire" in todo:
        # supergfxd and Cardwire must not both manage the dGPU. supergfxd goes back to Hybrid
        # first, so the dGPU is on the bus with its driver, then it is turned off for good.
        lines += ["step 'cardwired'",
                  "if systemctl is-active --quiet supergfxd; then",
                  "  supergfxctl -g | grep -q Hybrid || supergfxctl -m Hybrid",
                  "  sed -i -E 's/(\"no_logind\": *)true/\\1false/' /etc/supergfxd.conf 2>/dev/null",
                  "  systemctl disable --now supergfxd",
                  "  echo 1 > /sys/bus/pci/rescan",
                  "fi",
                  "command -v cardwire >/dev/null && { systemctl enable cardwired; systemctl restart cardwired; }"]
        if not hw.bpf_lsm():
            lines += ["echo 'Cardwire starts after the next restart, once the kernel runs BPF LSM.'"]
    if "keys" in todo:
        lines += ["step 'Laptop keys'", hotkeys.rule_script().rstrip("\n")]
    units = next((r.get("units") for r in rows if r["key"] == "ppd" and "ppd" in todo), None)
    if units:
        lines += ["step 'Power profiles: asusd only'", f"systemctl mask --now {' '.join(u + '.service' for u in units)}"]
    if "music" in todo:
        lines += ["step 'Music keyboard effect'", hw.music_access_script().rstrip("\n")]
    if "boost" in todo:
        lines += ["step 'CPU Boost switch'", hw.boost_access_script().rstrip("\n")]
    stop = [r["conflict"] for r in rows if r["key"] in todo and r.get("conflict")]
    if stop:
        lines += ["step 'Stopping " + ", ".join(c.title for c in stop) + "'", *conflicts.stop_script(stop)]
    lines += ["echo", "echo '== Done'"]
    return "\n".join(lines) + "\n"


# --- window ---------------------------------------------------------------------------
class SetupWindow(QtWidgets.QWidget):
    """Checklist in G-Helper's style: a mark, the item and what it is for; one button to fix all."""

    finished = QtCore.Signal()

    def __init__(self, main):
        super().__init__(main, QtCore.Qt.Window)
        self.main = main
        self.proc = None
        self.setWindowTitle(tr("Setup"))
        self.setObjectName("root")
        self.setAttribute(QtCore.Qt.WA_StyledBackground)
        self.setFixedWidth(506)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(6)
        head = QtWidgets.QLabel(tr("A few things this laptop needs. One password sets them all up."))
        head.setWordWrap(True)
        f = head.font()
        f.setBold(True)
        head.setFont(f)
        root.addWidget(head)
        self.list = QtWidgets.QGridLayout()
        self.list.setHorizontalSpacing(8)
        self.list.setVerticalSpacing(4)
        self.list.setColumnStretch(1, 1)
        root.addLayout(self.list)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(150)
        self.log.hide()
        root.addWidget(self.log)
        # one line instead of a confirmation box: the list above already says what each step does
        undo = QtWidgets.QLabel(tr("Everything here can be undone later:") + " "
                                f"<a style='color:{w.STANDARD.name()}' href='changes'>{tr('Extra → Changes')}</a>")
        undo.setObjectName("tip")
        undo.linkActivated.connect(lambda _: self.main.on_changes())
        root.addWidget(undo)
        buttons = QtWidgets.QHBoxLayout()
        self.links = QtWidgets.QLabel(f"<a style='color:{w.STANDARD.name()}' href='{ASUS_LINUX}'>asus-linux.org guides</a>")
        self.links.setOpenExternalLinks(True)
        buttons.addWidget(self.links)
        buttons.addStretch()
        self.later = w.RButton(tr("Later"), secondary=True, radius=2)
        self.later.setFixedSize(90, 26)
        self.later.clicked.connect(self.on_later)
        self.go = w.RButton(tr("Set up"), radius=2, accent=ECO)
        self.go.setFixedSize(110, 26)
        self.go.clicked.connect(self.run)
        self.go.set_activated(True)
        for b in (self.later, self.go):
            b.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            buttons.addWidget(b)
        root.addLayout(buttons)
        self.rows = []
        self.refresh()

    def refresh(self):
        self.rows = checks()
        while self.list.count():
            item = self.list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, r in enumerate(self.rows):
            mark = QtWidgets.QLabel("✓" if r["ok"] else ("•" if r["level"] in (OPTIONAL, INFO) else "✗"))
            colour = ECO if r["ok"] else (QtGui.QColor("gray") if r["level"] in (OPTIONAL, INFO) else TURBO)
            mark.setStyleSheet(f"color: {colour.name()}; font-weight: bold;")
            mark.setFixedWidth(14)
            mark.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)
            text = f"<b>{r['title']}</b>"
            if r["level"] == OPTIONAL:
                text += f" <span style='color:gray'>({tr('optional')})</span>"
            if r["detail"] and not r["ok"]:
                text += f"<br><span style='color:gray'>{r['detail']}</span>"
            c = r.get("conflict")
            if c and c.manual and not r["ok"]:
                text += "<br><span style='color:gray'>" + tr("To remove it:") + f"</span> {c.manual}"
                if c.link:
                    text += f" <a style='color:{w.STANDARD.name()}' href='{c.link}'>{tr('Details')}</a>"
            if r["key"] == "nvidia" and not r["ok"]:
                text += f" <a style='color:{w.STANDARD.name()}' href='{NVIDIA_HOWTO}'>{tr('How to install')}</a>"
            label = QtWidgets.QLabel(text)
            label.setWordWrap(True)
            label.setOpenExternalLinks(True)
            self.list.addWidget(mark, i, 0)
            self.list.addWidget(label, i, 1)
        fixable = any(not r["ok"] and r["fix"] for r in self.rows)
        self.go.setEnabled(fixable and self.proc is None)
        self.go.setText(tr("Set up") if fixable else tr("All set"))
        QtCore.QTimer.singleShot(0, self.fit)

    def fit(self):
        """adjustSize() takes the wrapped details at one line each; the layout's height for this
        width counts them all."""
        self.resize(self.width(), max(self.layout().heightForWidth(self.width()), self.minimumSizeHint().height()))

    def run(self):
        """One password for everything in the list (each row already says what it does); the steps in
        the home folder first, without one."""
        self.todo = {r["key"] for r in self.rows if not r["ok"] and r["fix"]}
        self.go.setEnabled(False)
        self.later.setEnabled(False)
        self.log.clear()
        self.log.show()
        if "powerdevil" in self.todo:
            ok, out = hw.set_powerdevil_ddcutil(True)
            self.log.appendPlainText("== " + tr("KDE power management in Eco") + "\n" + (out or ("OK" if ok else "")))
        self.packages = packages_of(self.todo) + tray_package(self.todo)  # before the install
        if "tray" in self.todo:  # GNOME takes the setting before the extension is installed
            ok, out = hw.set_gnome_appindicator(True)
            self.log.appendPlainText("== " + tr("Tray icon on GNOME") + "\n" + (out or ("OK" if ok else "")))
        if not self.todo - USER_STEPS and not tray_package(self.todo):
            self.done(0, None)
            return
        self.proc = QtCore.QProcess(self)
        self.proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.proc.setStandardInputFile(QtCore.QProcess.nullDevice())  # a question must fail, not hang
        self.proc.readyRead.connect(lambda: self.log.appendPlainText(
            bytes(self.proc.readAll()).decode(errors="replace").rstrip()))
        self.proc.finished.connect(self.done)
        self.proc.start("pkexec", ["sh", "-c", script(self.rows)])

    def done(self, code, _status):
        self.proc = None
        if code == 0:  # into the journal, so Extra -> System changes can list and undo them
            for key in (todo_changes(self.todo) | ({"packages"} if self.packages else set())) - {"keys", "boost", "music"}:
                names = (self.packages if key == "packages" else
                         next((r.get("units", []) for r in self.rows if r["key"] == "ppd"), [])
                         if key == "profiles_masked" else None)
                system_changes.record(key, **({"names": names} if names is not None else {}))
            if "tray" in self.todo and hw.gnome_tray() != "on":
                self.log.appendPlainText("\n" + tr("Log out and back in once to see the icon in the top bar."))
            if "bpf_lsm" in self.todo:
                self.log.appendPlainText("\n" + tr("Restart the laptop once: the kernel runs BPF LSM from the "
                                                    "next boot, and Cardwire starts with it."))
        self.later.setEnabled(True)
        self.later.setText(tr("Close"))
        if code in (126, 127):
            self.log.appendPlainText("\n" + tr("The password prompt was cancelled."))
        self.refresh()
        self.main.after_setup()
        self.finished.emit()

    def on_later(self):
        self.main.cfg["setup_later"] = self.main.version
        self.main.save()
        self.close()
