"""'Extra Settings' window: the G-Helper Extra form (app/Extra.Designer.cs, app/Extra.cs).

Only the parts that have a Linux backend on this hardware are shown, the same way G-Helper
hides sections a model does not support. Backlight timeout and hibernation are not ported yet.
"""
import os

from PySide6 import QtCore, QtGui, QtWidgets

from . import backend as hw
from . import diagnostics
from . import i18n
from . import selfupdate
from . import hotkeys
from . import modes
from . import system_changes
from . import widgets as w
from .widgets import ECO, STANDARD
from .window_games import GAME_DEFAULTS, KEEP
from .config import HOMEPAGE
from .i18n import tr

ISSUE_URL = HOMEPAGE + "/issues/new?template=bug_report.md"
# keyboard effects (Laptop Backlight; Fn+F4's "Toggle Aura" walks through them)
AURA = [("static", tr("Static")), ("breathe", tr("Breathe")), ("rainbow-cycle", tr("Rainbow")), ("pulse", tr("Strobe")),
        ("music", tr("Music"))]


def aura_choices():
    """AURA without the effects asusd does not allow on this model (all when it cannot be asked)."""
    allowed = hw.aura_modes()
    return [(k, label) for k, label in AURA
            if (hw.music_possible() if k == "music" else allowed is None or k in allowed)]


SPEEDS = [("low", tr("Slow")), ("med", tr("Normal")), ("high", tr("Fast"))]


class HeaderPanel(QtWidgets.QWidget):
    """panel*Header: ButtonSecond strip, Padding(11, 5, 11, 5), icon at 21, bold title at 56. With
    fold() a click on the strip opens and closes the section under it (a chevron shows which)."""

    def __init__(self, icon_name, title):
        super().__init__()
        self.icon_name = icon_name
        self.body = None
        self.setObjectName("header")
        self.setAttribute(QtCore.Qt.WA_StyledBackground)
        self.setFixedHeight(25)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(28, 0, 6, 0)
        lay.setSpacing(0)
        self.title = QtWidgets.QLabel(title)
        f = self.title.font()
        f.setBold(True)
        self.title.setFont(f)
        lay.addWidget(self.title)
        lay.addStretch()
        self.right = lay

    def fold(self, body, key, cfg, changed):
        """body opens and closes; cfg["extra_closed"] remembers the closed ones by key."""
        self.body, self.key, self.cfg, self.changed = body, key, cfg, changed
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.right.setContentsMargins(44, 0, 6, 0)
        body.setVisible(key not in cfg.get("extra_closed", []))

    def mousePressEvent(self, event):
        if self.body is None or event.button() != QtCore.Qt.LeftButton:
            return super().mousePressEvent(event)
        closed = set(self.cfg.get("extra_closed", []))
        opened = self.key in closed
        closed.symmetric_difference_update({self.key})
        self.cfg["extra_closed"] = sorted(closed)
        self.body.setVisible(opened)
        self.update()
        self.changed()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), w.THEME.button_second)
        x = 10
        if self.body is not None:
            chevron = "chevron-down" if self.body.isVisibleTo(self.parentWidget()) else "chevron-right"
            p.drawPixmap(QtCore.QPointF(8, (self.height() - 14) / 2), w.icon(chevron, 14))
            x = 26
        p.drawPixmap(QtCore.QPointF(x, (self.height() - 16) / 2), w.icon(self.icon_name))


def check(text, left=8):
    c = QtWidgets.QCheckBox(text)
    c.setFixedHeight(21)
    c.setContentsMargins(left, 0, 0, 0)
    c.setStyleSheet(f"QCheckBox {{ padding-left: {left}px; }}")
    return c


class ExtraWindow(QtWidgets.QWidget):
    def __init__(self, main):
        super().__init__(main, QtCore.Qt.Window)
        self.main = main
        self.cfg = main.cfg
        self.setWindowTitle(tr("Extra Settings"))
        self.setObjectName("root")
        self.setAttribute(QtCore.Qt.WA_StyledBackground)
        self.setFixedWidth(506)  # ClientSize 1013 at 192 DPI
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(7, 7, 8, 8)  # Padding(15)
        root.setSpacing(0)

        # Key Bindings
        self.bind_head = HeaderPanel("keyboard", tr("Key Bindings"))
        add_key = w.RButton(tr("Add"), secondary=True, radius=2)
        add_key.setFixedSize(60, 20)
        add_key.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        add_key.clicked.connect(lambda: self.on_add_key(add_key))
        self.bind_head.right.addWidget(add_key)
        self.bind_head.right.addSpacing(4)
        self.add_key = add_key
        reset_keys = w.RButton(tr("Reset"), secondary=True, radius=2)
        reset_keys.setFixedSize(75, 20)
        reset_keys.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        reset_keys.clicked.connect(self.on_reset_bindings)
        self.bind_head.right.addWidget(reset_keys)
        self.bind_head.right.addSpacing(6)
        help_icon = QtWidgets.QLabel()  # pictureHelp
        help_icon.setPixmap(w.icon("circle-help"))
        help_icon.setCursor(QtCore.Qt.PointingHandCursor)
        help_icon.setToolTip(tr("How Key Bindings work"))
        help_icon.mousePressEvent = lambda _: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl("https://github.com/sprinterz5/shiftray#key-bindings"))
        self.bind_head.right.addWidget(help_icon)
        self.help_icon = help_icon
        root.addWidget(self.bind_head)
        self.bindings = QtWidgets.QWidget()
        self.bind_grid = QtWidgets.QGridLayout(self.bindings)
        self.bind_grid.setContentsMargins(8, 4, 6, 4)
        self.bind_grid.setHorizontalSpacing(6)
        self.bind_grid.setVerticalSpacing(4)
        self.bind_grid.setColumnStretch(0, 1)
        self.bind_grid.setColumnStretch(1, 2)
        self.bind_grid.setColumnStretch(2, 2)
        self.bind_grid.setColumnStretch(3, 0)
        root.addWidget(self.bindings)
        self.bind_head.fold(self.bindings, "bindings", self.cfg, self.folded)
        self.bind_rows = {}

        # Laptop Backlight
        root.addSpacing(3)  # closed sections stay apart, not one strip
        backlight_head = head = HeaderPanel("sunrise", tr("Laptop Backlight"))
        self.brightness = w.Slider(0, 3, 1)
        self.brightness.setFixedWidth(250)
        self.brightness.released.connect(self.on_brightness)
        head.right.addWidget(self.brightness)
        root.addWidget(head)

        body = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(body)
        bl.setContentsMargins(2, 2, 0, 2)
        bl.setSpacing(0)
        # in G-Helper's main window; set once, so it lives here with the rest of the lighting
        effect_row = QtWidgets.QHBoxLayout()
        effect_row.setContentsMargins(6, 4, 6, 4)
        effect_row.addWidget(QtWidgets.QLabel(tr("Effect")))
        effect_row.addStretch()
        self.aura_combo = w.RComboBox()
        self.aura_combo.setFixedWidth(146)
        self.aura_combo.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        for key, label in aura_choices():
            self.aura_combo.addItem(label, key)
        self.aura_combo.activated.connect(self.on_effect)
        effect_row.addWidget(self.aura_combo)
        self.colour_button = w.ColorButton(tr("Color"), "#" + self.cfg.get("aura_colour", "ffffff"), radius=2)
        self.colour_button.setFixedSize(90, 20)
        self.colour_button.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.colour_button.clicked.connect(self.pick_colour)
        effect_row.addWidget(self.colour_button)
        bl.addLayout(effect_row)
        if [k for k, _ in aura_choices() if k != "music"] == ["static"]:
            note = QtWidgets.QLabel(tr("asusd allows only Static on this laptop: {model} is not in its list of "
                                       "keyboard effects (aura_support.ron) yet.", model=hw._read("/sys/class/dmi/id/board_name") or "?"))
            note.setObjectName("tip")
            note.setWordWrap(True)
            note.setContentsMargins(6, 0, 6, 2)
            bl.addWidget(note)
        self.show_aura()
        self.awake = check(tr("Awake"))
        self.boot = check(tr("Boot/Shutdown"))
        self.sleep = check(tr("Sleep"))
        for c in (self.awake, self.boot, self.sleep):
            c.toggled.connect(self.on_power)
            bl.addWidget(c)
        speed_row = QtWidgets.QHBoxLayout()
        speed_row.setContentsMargins(6, 6, 6, 2)
        speed_label = QtWidgets.QLabel(tr("Animation Speed"))
        self.speed = w.RComboBox()
        self.speed.setFixedWidth(146)
        self.speed.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        for key, label in SPEEDS:
            self.speed.addItem(label, key)
        self.speed.activated.connect(self.on_speed)
        speed_row.addWidget(speed_label)
        speed_row.addStretch()
        speed_row.addWidget(self.speed)
        bl.addLayout(speed_row)

        # Extra.panelBacklightExtra: "Timeout when plugged / on battery"
        timeout_row = QtWidgets.QHBoxLayout()
        timeout_row.setContentsMargins(6, 4, 6, 2)
        self.timeout_label = QtWidgets.QLabel(tr("Backlight timeout plugged / on battery"))
        timeout_row.addWidget(self.timeout_label)
        timeout_row.addStretch()
        self.timeout_ac = QtWidgets.QSpinBox()
        self.timeout_bat = QtWidgets.QSpinBox()
        for box, key in ((self.timeout_ac, "kbd_timeout_ac"), (self.timeout_bat, "kbd_timeout_bat")):
            box.setRange(0, 3600)
            box.setSuffix(tr(" sec"))
            box.setToolTip(tr("0 = backlight always on"))
            box.setFixedSize(70, 20)
            box.setValue(int(self.cfg.get(key, 0)))
            box.editingFinished.connect(self.on_timeout)
            timeout_row.addWidget(box)
        bl.addLayout(timeout_row)
        note_row = QtWidgets.QHBoxLayout()
        note_row.setContentsMargins(6, 0, 6, 0)
        self.timeout_note = QtWidgets.QLabel(tr("Backlight timeout needs the small swayidle tool."))
        self.timeout_note.setObjectName("tip")
        self.install_idle = w.RButton(tr("Install"), secondary=True, radius=2)
        self.install_idle.setFixedSize(70, 20)
        self.install_idle.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.install_idle.clicked.connect(self.main.install_idle)
        note_row.addWidget(self.timeout_note)
        note_row.addStretch()
        note_row.addWidget(self.install_idle)
        bl.addLayout(note_row)
        root.addWidget(body)
        backlight_head.fold(body, "backlight", self.cfg, self.folded)

        # Other
        root.addSpacing(3)
        other_head = HeaderPanel("settings", tr("Other"))
        root.addWidget(other_head)
        other = QtWidgets.QWidget()
        ol = QtWidgets.QVBoxLayout(other)
        ol.setContentsMargins(10, 2, 5, 2)  # Padding(21, 5, 11, 5)
        ol.setSpacing(0)
        self.situations = check(tr("Situations (experimental): one tile sets the mode, GPU, screen and keyboard"), 2)
        self.situations.setChecked(bool(self.cfg.get("situations_on")))
        self.situations.toggled.connect(self.main.set_situations)
        ol.addWidget(self.situations)
        self.topmost = check(tr("Keep app window always on top"), 2)
        self.topmost.setChecked(bool(self.cfg.get("topmost")))
        self.topmost.toggled.connect(self.on_topmost)
        ol.addWidget(self.topmost)
        # on Wayland only KWin (through the app's script) keeps a window on top; GNOME ignores the hint
        self.topmost.setVisible(not os.environ.get("WAYLAND_DISPLAY")
                                or "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper())
        self.no_overdrive = None
        if hw.get_overdrive() is not None:
            self.no_overdrive = check(tr("Disable screen overdrive"), 2)
            self.no_overdrive.setChecked(bool(self.cfg.get("no_overdrive")))
            self.no_overdrive.toggled.connect(self.on_no_overdrive)
            ol.addWidget(self.no_overdrive)
        if main.optimized:
            self.usbc = check(tr("Keep GPU disabled on USB-C charger in Optimized mode"), 2)
            self.usbc.setChecked(bool(self.cfg.get("optimized_usbc")))
            self.usbc.toggled.connect(self.on_usbc)
            ol.addWidget(self.usbc)
            # Extra.checkGpuApps: Eco blocks new users of the dGPU; running ones keep it awake
            self.gpu_apps = check(tr("Stop all apps using GPU when switching to Eco"), 2)
            self.gpu_apps.setChecked(bool(self.cfg.get("kill_gpu_apps")))
            self.gpu_apps.setToolTip(tr("Programs already using the NVIDIA GPU keep it powered on until they "
                                     "quit. Without this option you are asked first."))
            self.gpu_apps.toggled.connect(lambda on: (self.cfg.__setitem__("kill_gpu_apps", on), self.main.save()))
            ol.addWidget(self.gpu_apps)
        powerd = hw.platform_get("DisableNvidiaPowerdOnBattery")
        if powerd is not None and os.path.exists("/usr/lib/systemd/system/nvidia-powerd.service"):
            self.powerd = check(tr("Stop NVIDIA power daemon on battery"), 2)
            self.powerd.setChecked(powerd)
            self.powerd.setToolTip(tr("nvidia-powerd runs Dynamic Boost, which the firmware turns off on "
                                   "battery anyway; stopping it there saves a little power (asusd)"))
            self.powerd.toggled.connect(lambda on: self.main.bg(
                hw.platform_set, "DisableNvidiaPowerdOnBattery", "b", on,
                then=lambda r: self.main.report(r, tr("NVIDIA power daemon"))))
            ol.addWidget(self.powerd)
        self.build_game_mode(ol)
        hints = check(tr("Tell me when a game runs in Eco or Silent"), 2)
        hints.setChecked(self.cfg.get("game_hints", True))
        hints.toggled.connect(lambda on: (self.cfg.__setitem__("game_hints", on), self.main.save()))
        ol.addWidget(hints)
        tips = check(tr("Battery tips: sleep drain, NVIDIA awake on battery, low battery"), 2)
        tips.setChecked(self.cfg.get("battery_tips", True))
        tips.setToolTip(tr("Notifications only; nothing changes unless you press their button"))
        tips.toggled.connect(lambda on: (self.cfg.__setitem__("battery_tips", on), self.main.save()))
        ol.addWidget(tips)
        deep = hw.deep_sleep()
        if deep is not None:
            self.deep = check(tr("Deep sleep (S3): uses almost no battery while asleep"), 2)
            self.deep.setChecked(deep)
            self.deep.setToolTip(tr("Instead of Modern Standby (s2idle), which can drain the battery "
                                    "overnight. Waking up takes a few seconds longer."))
            self.deep.toggled.connect(self.on_deep_sleep)
            ol.addWidget(self.deep)
        lid = hw.clamshell()
        if lid is not None:
            self.clamshell = check(tr("Keep working with the lid closed on an external display"), 2)
            self.clamshell.setChecked(lid)
            self.clamshell.setToolTip(tr("Clamshell mode: closing the lid does not sleep while a monitor is "
                                         "connected (KDE's power settings, all power sources)"))
            self.clamshell.toggled.connect(self.on_clamshell)
            ol.addWidget(self.clamshell)
        # rows that exist only when this laptop's firmware has the setting (asus-armoury)
        if hw.armoury_range("boot_sound") is not None or hw._read(f"{hw.ARMOURY}/boot_sound/current_value"):
            self.boot_sound = check(tr("Boot Sound"), 2)
            self.boot_sound.setChecked(hw._read(f"{hw.ARMOURY}/boot_sound/current_value") == "1")
            self.boot_sound.toggled.connect(lambda on: self.main.bg(
                hw.run, "asusctl", "armoury", "set", "boot_sound", "1" if on else "0",
                then=lambda r: self.main.report(r, tr("Boot sound"))))
            ol.addWidget(self.boot_sound)
        apu = hw.armoury_choices("apu_mem")
        if apu:
            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(2, 4, 0, 2)
            row.addWidget(QtWidgets.QLabel(tr("Memory Assigned to GPU")))
            row.addStretch()
            self.apu = w.RComboBox()
            self.apu.setFixedWidth(146)
            self.apu.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            for value in apu:  # the driver's index: 0 is the firmware's Auto (its minimum), then index + 1 GB
                self.apu.addItem(tr("Auto") if value == "0" else f"{int(value) + 1} GB", value)
            self.apu.setCurrentIndex(max(0, self.apu.findData(hw._read(f"{hw.ARMOURY}/apu_mem/current_value"))))
            self.apu.activated.connect(lambda _: self.main.bg(
                hw.run, "asusctl", "armoury", "set", "apu_mem", self.apu.currentData(),
                then=lambda r: (self.main.report(r, tr("GPU memory")), QtWidgets.QMessageBox.information(
                    self, tr("Memory Assigned to GPU"), tr("Takes effect after a restart.")))))
            row.addWidget(self.apu)
            ol.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(2, 4, 0, 2)
        row.addWidget(QtWidgets.QLabel(tr("Language")))
        row.addStretch()
        self.language = w.RComboBox()
        self.language.setFixedWidth(146)
        self.language.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.language.addItem(tr("Auto"), "auto")
        for code, name in i18n.LANGUAGES.items():
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(0, self.language.findData(self.cfg.get("language", "auto"))))
        self.language.activated.connect(self.on_language)
        row.addWidget(self.language)
        ol.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(2, 4, 0, 2)
        row.addWidget(QtWidgets.QLabel(tr("Theme")))
        row.addStretch()
        self.theme = w.RComboBox()
        self.theme.setFixedWidth(146)
        self.theme.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        for key, label in (("auto", tr("Auto")), ("dark", tr("Dark")), ("light", tr("Light"))):
            self.theme.addItem(label, key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(self.cfg.get("theme", "auto"))))
        self.theme.setToolTip(tr("Auto follows the desktop's light / dark setting"))
        self.theme.activated.connect(lambda _: self.set_look("theme", self.theme.currentData()))
        row.addWidget(self.theme)
        ol.addLayout(row)
        self.accent = check(tr("Accent colour from the desktop"), 2)
        self.accent.setChecked(bool(self.cfg.get("desktop_accent")))
        self.accent.setToolTip(tr("Replaces the blue of Balanced, Standard, checkboxes and sliders; "
                                  "Silent, Turbo and Eco keep their colours"))
        self.accent.toggled.connect(lambda on: self.set_look("desktop_accent", on))
        ol.addWidget(self.accent)
        root.addWidget(other)
        other_head.fold(other, "other", self.cfg, self.folded)

        # Services: what runs, and Start when something does not
        services = QtWidgets.QWidget()
        sl = QtWidgets.QHBoxLayout(services)
        sl.setContentsMargins(10, 8, 0, 6)
        sl.setSpacing(4)
        self.services_icon = QtWidgets.QLabel()
        self.services_label = QtWidgets.QLabel("")
        f = self.services_label.font()
        f.setBold(True)
        self.services_label.setFont(f)
        sl.addWidget(self.services_icon)
        sl.addWidget(self.services_label)
        sl.addStretch()
        self.services_start = w.RButton(tr("Start"), secondary=True, radius=2)  # buttonServices
        self.services_start.setFixedSize(128, 26)
        self.services_start.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.services_start.clicked.connect(lambda: (self.main.start_services(), QtCore.QTimer.singleShot(3000, self.refresh)))
        sl.addWidget(self.services_start)
        root.addWidget(services)

        # one row of equal buttons at the very bottom (Credits came from the main window)
        bottom = QtWidgets.QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(6)
        self.setup_button = w.RButton(tr("Setup"), "cog", secondary=True, radius=2)
        self.setup_button.setToolTip(tr("Check and install what this laptop needs (one password)"))
        self.setup_button.clicked.connect(self.main.on_setup)
        self.diag_button = w.RButton(tr("Diagnostics"), secondary=True, radius=2)
        self.diag_button.setToolTip(tr("Report a problem on GitHub, or save the report: versions, "
                                    "settings and logs, without serial numbers or the computer's name"))
        self.diag_button.clicked.connect(self.on_diagnostics)
        self.changes_button = w.RButton(tr("Changes"), secondary=True, radius=2)
        self.changes_button.setToolTip(tr("What the app changed on the system, and Undo"))
        self.changes_button.clicked.connect(self.main.on_changes)
        credits = w.RButton(tr("Credits"), "heart", secondary=True, radius=2)
        credits.clicked.connect(self.main.on_credits)
        for b in (self.setup_button, self.diag_button, self.changes_button, credits):
            b.setFixedHeight(26)
            bottom.addWidget(b, 1)
        root.addLayout(bottom)

        self.root = root
        self.build_bindings()
        self.refresh()

    def folded(self):
        """A section opened or closed: remember it, and fit the window to what is open."""
        self.main.save()
        QtCore.QTimer.singleShot(0, lambda: self.setFixedHeight(self.root.sizeHint().height()))

    def set_look(self, key, value):
        self.cfg[key] = value
        self.main.save()
        self.main.apply_theme()

    def on_language(self, _index):
        """Like G-Helper, the language is read at start: restart to apply it."""
        self.cfg["language"] = self.language.currentData()
        self.main.save()
        answer = QtWidgets.QMessageBox.question(self, tr("Language"), tr("Restart the app now to change the language?"))
        if answer == QtWidgets.QMessageBox.Yes:
            selfupdate.restart()
            QtWidgets.QApplication.exit(0)  # not quit(): the main window would veto it (see ui.quit_app)

    def on_diagnostics(self):
        menu = QtWidgets.QMenu(self)
        menu.addAction(tr("Report a problem on GitHub…"), self.report_problem)
        menu.addAction(tr("Save the report to a file…"), self.save_diagnostics)
        menu.exec(self.diag_button.mapToGlobal(QtCore.QPoint(0, self.diag_button.height())))

    def report_problem(self):
        """The report goes to the clipboard (not into the link: it is long and personal enough), then
        a new issue opens with the template that says where to paste it."""
        self.diag_button.setEnabled(False)

        def done(text):
            self.diag_button.setEnabled(True)
            if not text:
                self.main.report((False, tr("Could not make the report")), tr("Diagnostics"))
                return
            QtWidgets.QApplication.clipboard().setText(text)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(ISSUE_URL))
            QtWidgets.QMessageBox.information(
                self, tr("Report a problem"),
                tr("The report is copied. In the page that opened, say what happened and paste the "
                   "report under it (Ctrl+V).") + "\n\n"
                + tr("Have a look before posting: it has no serial numbers or computer name, but it "
                     "does have your settings and the app's log."))
        self.main.bg(lambda: diagnostics.report(self.cfg, self.main.version), then=done)

    def save_diagnostics(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, tr("Save diagnostics"), os.path.expanduser("~/shiftray-report.txt"), tr("Text (*.txt)"))
        if not path:
            return
        self.diag_button.setEnabled(False)

        def job():
            try:
                text = diagnostics.report(self.cfg, self.main.version)
                with open(path, "w") as f:
                    f.write(text)
            except Exception as err:  # noqa: BLE001 - shown to the user instead of a dead button
                return False, str(err)
            return True, ""

        def done(result):
            self.diag_button.setEnabled(True)
            if result[0]:
                QtWidgets.QMessageBox.information(self, tr("Diagnostics"), tr("Saved to {path}.", path=path) + "\n\n"
                                                  + tr("Have a look before sharing it, then attach it to the issue."))
            else:
                self.main.report(result, tr("Diagnostics"))
        self.main.bg(job, then=done)

    # -- key bindings (Extra.SetKeyCombo) -------------------------------------------
    def actions(self):
        """hotkeys.ACTIONS plus one action per mode (G-Helper's keybind_profile_N)."""
        out = []
        for action in hotkeys.ACTIONS:
            out.append(action)
            if action[0] == "performance":
                out += [(f"mode:{key}", tr("{mode} Mode", mode=label)) for key, label, _ in modes.all_modes(self.cfg)]
        return out

    def build_bindings(self):
        """FN+F4 always, G-Helper's other rows once seen, then the keys the user added."""
        while self.bind_grid.count():
            item = self.bind_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.bind_rows = {}
        state = getattr(self.main, "hotkeys_state", "")
        rows = []
        if state not in ("no-device", "no-access", "no-evdev"):
            seen = set(self.cfg.get("seen_keys", [])) | {"fnf4"}
            rows += [(key, label, False) for key, (label, _) in hotkeys.KEYS.items() if key in seen]
            rows += [(key, info["label"], True) for key, info in self.cfg.get("custom_keys", {}).items()]
        rows += [(sid, info.get("trigger") or "(no keys set)", True)
                 for sid, info in self.cfg.get("shortcuts", {}).items()]

        r = 0
        if state in ("no-access", "no-evdev"):
            note = QtWidgets.QLabel(
                tr("Laptop keys need permission to read the ASUS keys (one password prompt).")
                if state == "no-access" else tr("Laptop keys need the python3-evdev package."))
            note.setObjectName("tip")
            note.setWordWrap(True)
            self.bind_grid.addWidget(note, r, 0, 1, 2)
            if state == "no-access":
                enable = w.RButton(tr("Enable"), secondary=True, radius=2)
                enable.setFixedHeight(22)
                enable.clicked.connect(self.main.enable_hotkeys)
                self.bind_grid.addWidget(enable, r, 2, 1, 2)
            r += 1
        for key, label, removable in rows:
            name = QtWidgets.QLabel(label + ":")
            combo = w.RComboBox()
            for action, text in self.actions():
                combo.addItem(text, action)
            stored = self.cfg.get(f"key_{key}")
            current = "" if stored == "none" else (stored or hotkeys.DEFAULT_ACTION.get(key, ""))
            combo.setCurrentIndex(max(0, combo.findData(current)))
            command = QtWidgets.QLineEdit(self.cfg.get(f"key_{key}_cmd", ""))
            command.setPlaceholderText(tr("action"))
            command.setToolTip(tr("Command to run when the action is Custom"))
            command.setFixedHeight(20)
            command.setEnabled(current == "custom")
            combo.activated.connect(lambda _=0, k=key: self.on_binding(k))
            command.editingFinished.connect(lambda k=key: self.on_binding(k))
            self.bind_grid.addWidget(name, r, 0)
            self.bind_grid.addWidget(combo, r, 1)
            self.bind_grid.addWidget(command, r, 2)
            if removable:
                remove = w.RButton("", "circle-minus", secondary=True, radius=2)
                remove.setFixedSize(20, 20)
                remove.setToolTip(tr("Remove this key"))
                remove.clicked.connect(lambda _=False, k=key: self.on_remove_key(k))
                self.bind_grid.addWidget(remove, r, 3)
            self.bind_rows[key] = (combo, command)
            r += 1
        self.add_key.setEnabled(state != "no-device" or bool(self.main.portal and self.main.portal.ok))
        self.fit()

    def fit(self):
        self.setFixedHeight(self.root.sizeHint().height())

    def on_binding(self, key):
        combo, command = self.bind_rows[key]
        action = combo.currentData()
        command.setEnabled(action == "custom")
        default = hotkeys.DEFAULT_ACTION.get(key, "")
        if action == default:
            self.cfg.pop(f"key_{key}", None)
        else:
            self.cfg[f"key_{key}"] = action or "none"
        self.cfg[f"key_{key}_cmd"] = command.text().strip()
        self.main.save()

    def on_add_key(self, button):
        menu = QtWidgets.QMenu(self)
        laptop = menu.addAction(tr("Laptop key (Fn + ...)"))
        laptop.setEnabled(getattr(self.main, "hotkeys_state", "") == "running")
        laptop.triggered.connect(self.main.capture_laptop_key)
        combo = menu.addAction(tr("Keyboard shortcut (any keys) ..."))
        combo.setEnabled(bool(self.main.portal and self.main.portal.ok))
        combo.triggered.connect(self.main.add_shortcut)
        menu.exec(button.mapToGlobal(QtCore.QPoint(0, button.height())))

    def on_remove_key(self, key):
        self.main.remove_key(key)

    def on_reset_bindings(self):
        for key in list(hotkeys.KEYS) + list(self.cfg.get("custom_keys", {})) + list(self.cfg.get("shortcuts", {})):
            self.cfg.pop(f"key_{key}", None)
            self.cfg.pop(f"key_{key}_cmd", None)
        self.main.save()
        self.build_bindings()

    def on_timeout(self):
        self.cfg["kbd_timeout_ac"] = self.timeout_ac.value()
        self.cfg["kbd_timeout_bat"] = self.timeout_bat.value()
        self.main.save()
        self.main.apply_idle()

    def show_idle_state(self, available):
        for box in (self.timeout_ac, self.timeout_bat):
            box.setEnabled(available)
        self.timeout_note.setVisible(not available)
        self.install_idle.setVisible(not available)
        self.fit()

    def refresh(self):
        self.help_icon.setPixmap(w.icon("circle-help"))
        self.show_idle_state(self.main.idle_available)
        self.services_icon.setPixmap(w.icon("cog"))
        level = hw.get_kbd_level()
        if level in hw.KBD_LEVELS:
            self.brightness.set_value(hw.KBD_LEVELS.index(level))
        state = hw.get_aura_power() or {}
        for c, key in ((self.awake, "awake"), (self.boot, "boot"), (self.sleep, "sleep")):
            c.blockSignals(True)
            c.setChecked(state.get(key, True))
            c.blockSignals(False)
        idx = self.speed.findData(hw.get_aura_speed() or self.cfg.get("aura_speed", "med"))
        self.speed.setCurrentIndex(max(idx, 0))
        running = hw.services_running()
        self.services_label.setText(tr("asusd / cardwired running: {count}", count=len(running)))
        self.services_start.setVisible(len(running) < 2)
        colour = (ECO if len(running) == 2 else STANDARD).name()
        self.services_label.setStyleSheet(f"color: {colour};")

    def showEvent(self, e):
        self.refresh()
        super().showEvent(e)

    # -- actions ----------------------------------------------------------------
    def on_brightness(self, value):
        self.main.bg(hw.set_kbd_level, hw.KBD_LEVELS[value],
                     then=lambda r: self.main.report(r, tr("Keyboard backlight")))

    def on_power(self):
        self.main.bg(hw.set_aura_power, self.awake.isChecked(), self.boot.isChecked(),
                     self.sleep.isChecked(), then=lambda r: self.main.report(r, tr("Backlight power")))

    def show_aura(self):
        """The effect in cfg, also after Fn+F4 changed it."""
        self.aura_combo.setCurrentIndex(max(0, self.aura_combo.findData(self.cfg.get("aura_effect", "static"))))

    def on_effect(self, _index):
        self.cfg["aura_effect"] = self.aura_combo.currentData()
        self.main.apply_aura()

    def pick_colour(self):
        colour = QtWidgets.QColorDialog.getColor(
            QtGui.QColor("#" + self.cfg.get("aura_colour", "ffffff")), self, tr("Color"))
        if colour.isValid():
            self.cfg["aura_colour"] = colour.name()[1:]
            self.colour_button.set_swatch(colour)
            self.main.apply_aura()

    def on_speed(self):
        self.cfg["aura_speed"] = self.speed.currentData()
        self.main.apply_aura()

    def on_deep_sleep(self, on):
        if on:
            answer = QtWidgets.QMessageBox.question(
                self, tr("Deep sleep"),
                tr("This adds mem_sleep_default=deep to the kernel command line (asks for your password). "
                   "On a few laptops S3 does not wake up properly: if that happens, hold the power button, "
                   "start again and turn this off, or undo it in Extra → System changes.\n\nTurn it on?"))
            if answer != QtWidgets.QMessageBox.Yes:
                self.deep.blockSignals(True)
                self.deep.setChecked(False)
                self.deep.blockSignals(False)
                return

        def done(result):
            if result[0]:
                (system_changes.record if on else system_changes.forget)("deep_sleep")
            else:
                self.deep.blockSignals(True)
                self.deep.setChecked(not on)
                self.deep.blockSignals(False)
            self.main.report(result, tr("Sleep mode"))
        self.main.bg(hw.set_deep_sleep, on, then=done)

    def on_clamshell(self, on):
        system_changes.record("clamshell", on=on)
        self.main.bg(hw.set_clamshell, on, then=lambda r: self.main.report(r, tr("Clamshell mode")))

    def build_game_mode(self, ol):
        """"When a game runs, switch the mode": on the charger [mode], on battery [mode] (window_games)."""
        self.game_mode = check(tr("When a game runs, switch the mode"), 2)
        self.game_mode.setChecked(bool(self.cfg.get("game_mode_on")))
        self.game_mode.setToolTip(tr("Steam, Proton, Wine, Lutris and Heroic games. When the last one quits, "
                                     "the mode you had comes back. Picking a mode during a game wins."))
        self.game_mode.toggled.connect(self.on_game_mode)
        ol.addWidget(self.game_mode)
        self.game_combos = {}
        for key, label in (("game_mode_ac", tr("On the charger")), ("game_mode_bat", tr("On battery"))):
            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(24, 2, 0, 2)
            row.addWidget(QtWidgets.QLabel(label))
            row.addStretch()
            combo = w.RComboBox()
            combo.setFixedWidth(146)
            combo.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            combo.addItem(tr("Don't change"), KEEP)
            for mode, name, _ in modes.all_modes(self.cfg):
                combo.addItem(name, mode)
            combo.setCurrentIndex(max(0, combo.findData(self.cfg.get(key) or GAME_DEFAULTS[key])))
            combo.activated.connect(lambda _, k=key, c=combo: (self.cfg.__setitem__(k, c.currentData()),
                                                              self.main.save()))
            row.addWidget(combo)
            ol.addLayout(row)
            self.game_combos[key] = combo
        self.show_game_mode()

    def show_game_mode(self):
        for combo in self.game_combos.values():
            combo.setEnabled(self.game_mode.isChecked())

    def on_game_mode(self, on):
        self.cfg["game_mode_on"] = on
        self.main.save()
        self.show_game_mode()
        self.main.apply_game_mode()

    def on_topmost(self, on):
        self.cfg["topmost"] = on
        self.main.save()
        self.main.apply_topmost()

    def on_no_overdrive(self, on):
        self.cfg["no_overdrive"] = on
        self.main.save()
        if on and hw.get_overdrive():
            self.main.bg(hw.set_overdrive, False, then=lambda r: (self.main.report(r, tr("Overdrive")),
                                                                  self.main.refresh_slow()))
        else:
            self.main.refresh_slow()

    def on_usbc(self, on):
        self.cfg["optimized_usbc"] = on
        self.main.save()
        if self.cfg.get("gpu_auto"):
            self.main.apply_gpu_auto()
