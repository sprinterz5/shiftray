"""Main window and tray icon: the G-Helper (seerge/g-helper) layout, on top of asusd / Cardwire.

Panel order, sizes, texts and colours follow app/Settings.Designer.cs and app/Settings.cs.
"""
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6 import QtCore, QtDBus, QtGui, QtNetwork, QtWidgets

from . import backend as hw
from .i18n import tr
from .log import log, setup as logsetup
from .extra import ExtraWindow
from . import hotkeys
from . import modes
from . import shortcuts
from . import battery
from . import system_changes
from . import changes_window
from . import conflicts
from . import setup
from . import notifications
from . import situations
from .fans import FansWindow, fan_max, restore_after_calibration, snapshot_factory_curves, INADEQUATE_RPM
from . import widgets as w
from .config import APP, NAME, VERSION, HOMEPAGE, DONATE_URL, AUTOSTART, quit_app, load_config, save_config  # noqa: F401 (ui.VERSION etc. still work)
from .window_keys import KeysMixin
from .window_update import UpdateMixin
from .window_idle import IdleMixin, idle_available
from .window_games import GamesMixin
from .window_situations import SituationsMixin
from .widgets import ECO, STANDARD, TURBO, CUSTOM, GRAY


MODES = [("quiet", tr("Silent"), "bike", ECO),
         ("balanced", tr("Balanced"), "car", STANDARD),
         ("performance", tr("Turbo"), "rocket", TURBO)]
GPU_MODES = [("Integrated", tr("Eco"), "leaf", ECO, tr("iGPU only"),
              tr("Disables dGPU to save battery")),
             ("Hybrid", tr("Standard"), "flower-2", STANDARD, tr("iGPU + dGPU"),
              tr("Enables dGPU for standard use")),
             ("AsusMuxDgpu", tr("Ultimate"), "gamepad-2", TURBO, tr("dGPU exclusive"),
              tr("Routes laptop screen to dGPU, maximizing FPS"))]
# Each nvidia-smi wakes the dGPU, which then stays up ~17 s (FA507NV): asked on a timer, it would
# never sleep again. So it is asked only while an app (a game) keeps the dGPU awake anyway, every
# DGPU_TEMP_EVERY s. G-Helper falls back to the EC's GPU sensor, which Linux lacks.
DGPU_TEMP_EVERY = 12
DGPU_APPS_EVERY = 30
# in the tray, fan speeds and the keyboard light are read every TRAY_SLOW-th refresh only: each read
# is an ACPI call into the firmware (about 2 and 3.5 ms), and only the tooltip shows them
TRAY_SLOW = 5
# battery tips (Extra: "Battery tips"): a sleep that cost this much per hour gets a hint about deep sleep
SLEEP_DRAIN_HIGH = 1.5   # % per hour; Modern Standby that works costs about 0.5-1
SLEEP_REPORT_AFTER = 3600  # s asleep before the drain is worth a notification
DGPU_AWAKE_TELL = 120    # s on battery with apps keeping the dGPU awake before saying which
LOW_BATTERY = 20         # % at which Silent (and Eco) is offered once per discharge
SECTION_GAP = 18         # px from a block's buttons to the next block's title (G-Helper: 22 to 28)
TIP_LINE = 16            # the grey hover-tip line under the GPU and screen buttons


def duration(seconds):
    """'1h 07m', or '13m' under an hour."""
    seconds = int(seconds)
    if seconds < 3600:
        return tr("{m}m", m=seconds // 60)
    return tr("{h}h {m}m", h=seconds // 3600, m=f"{seconds % 3600 // 60:02d}")
SERVICE_TOOLS = {"asusd": "asusctl", "cardwired": "cardwire"}
OPTIMIZED_TIP = tr("Switch to Eco on battery and to Standard when plugged in")


def stylesheet():
    t = w.THEME
    return f"""
    QWidget#root, QWidget#panel {{ background: {t.form_back.name()}; }}
    QLabel {{ color: {t.fore.name()}; background: transparent; }}
    QLabel#tip {{ color: {t.gray_text.name()}; }}
    QLabel#dim {{ color: {t.control_dark.name()}; }}
    QLabel#version {{ color: rgba(128,128,128,128); text-decoration: underline; }}
    QCheckBox {{ color: {t.fore.name()}; spacing: 6px; background: transparent; }}
    QCheckBox::indicator {{ width: 12px; height: 12px; border-radius: 2px;
        border: 1px solid {t.control_dark.name()}; background: {t.form_back.name()}; }}
    QCheckBox::indicator:hover {{ border-color: {t.fore.name()}; }}
    QCheckBox::indicator:checked {{ background: {STANDARD.name()}; border-color: {STANDARD.name()};
        image: url({os.path.join(w.ASSETS, 'check.svg')}); }}
    QComboBox QAbstractItemView {{ background: {t.button_main.name()}; color: {t.fore.name()};
        selection-background-color: {t.border_main.name()}; border: 1px solid {t.border_main.name()};
        outline: 0; }}
    QMenu {{ background: {t.form_back.name()}; color: {t.fore.name()}; border: 1px solid {t.border_main.name()}; }}
    QMenu::item {{ padding: 4px 20px 4px 24px; }}
    QMenu::item:selected {{ background: {t.button_main.name()}; }}
    QMenu::item:disabled {{ color: {t.gray_text.name()}; }}
    QMenu::separator {{ height: 1px; background: {t.border_main.name()}; margin: 3px 0; }}
    QCheckBox#apply {{ background: {t.button_second.name()}; padding: 0 8px; border-radius: 2px; }}
    QLineEdit {{ background: {t.button_main.name()}; color: {t.fore.name()};
        border: 1px solid {t.border_main.name()}; border-radius: 2px; padding: 0 4px; }}
    QSpinBox, QKeySequenceEdit {{ background: {t.button_main.name()}; color: {t.fore.name()};
        border: 1px solid {t.border_main.name()}; border-radius: 2px; }}
    QLineEdit:disabled {{ color: {t.gray_text.name()}; }}
    QLabel#pending {{ color: {STANDARD.name()}; }}
    QLabel#banner {{ color: {TURBO.name()}; }}
    QToolTip {{ background: {t.button_main.name()}; color: {t.fore.name()}; border: 1px solid {t.border_main.name()}; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {t.border_main.name()}; border-radius: 4px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: {t.control_dark.name()}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
    """


def panel(left, top, right, bottom):
    p = QtWidgets.QWidget()
    p.setObjectName("panel")
    lay = QtWidgets.QVBoxLayout(p)
    lay.setContentsMargins(left, top, right, bottom)
    lay.setSpacing(0)
    return p, lay


def grid(columns, row_height, cell_margin=2):
    """TableLayoutPanel with equal Percent columns; each control has Margin(cell_margin)."""
    holder = QtWidgets.QWidget()
    g = QtWidgets.QGridLayout(holder)
    g.setContentsMargins(cell_margin, cell_margin, cell_margin, cell_margin)
    g.setHorizontalSpacing(2 * cell_margin)
    g.setVerticalSpacing(2 * cell_margin)
    for c in range(columns):
        g.setColumnStretch(c, 1)
    g.setRowMinimumHeight(0, row_height - 2 * cell_margin)
    return holder, g


class Bridge(QtCore.QObject):
    """Delivers worker-thread results back on the GUI thread."""
    done = QtCore.Signal(object, object)


class HoverTip(QtCore.QObject):
    """Settings.cs shows a button's explanation in the grey tip label while hovering it."""

    def __init__(self, label, texts):
        super().__init__()
        self.label, self.texts = label, texts
        self.rest = ""  # shown when not hovering (e.g. a pending GPU switch)

    def set_rest(self, text):
        self.rest = text
        self.label.setText(text)

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Enter and obj in self.texts:
            self.label.setText(self.texts[obj])
        elif event.type() == QtCore.QEvent.Leave and obj in self.texts:
            self.label.setText(self.rest)
        return False


class MainWindow(KeysMixin, UpdateMixin, IdleMixin, GamesMixin, SituationsMixin, QtWidgets.QWidget):
    def __init__(self, tray_available):
        super().__init__()
        self.cfg = load_config()
        self.tray_available = tray_available
        self.tray = None
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.bg_running = set()  # keys of the timers' jobs still running (bg)
        self.bridge = Bridge()
        self.bridge.done.connect(lambda cb, result: cb(result))
        self.last_ac = None
        self.dgpu_awake = False
        self.bat_time = None
        self.kbd_restoring = 0.0
        self.dgpu_temp = None
        self.dgpu_temp_at = 0.0
        self.ticks = 0
        self.last_fans = {}
        self.theme_state = None
        self.dgpu_was_awake = False
        self.dgpu_apps = []
        self.dgpu_apps_at = 0.0
        self.gpu_current = None
        self.gpu_busy = False
        self.battery_hover = False
        self.extra = None
        self.music = None  # the Music keyboard effect while it runs
        self.fans = None
        self.updates = None
        self.setup = None
        self.version = VERSION
        self.mode_busy = False
        self.last_profile = None
        self.full_hover = False

        model = hw.model_name()
        # G-Helper's title with the model on KDE (its script finds the window by it); elsewhere just the
        # name, which Qt does not repeat: GNOME's narrow title bar ran the long one under its close button
        self.setWindowTitle(f"{NAME} - {model}" if model and "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
                            else NAME)
        self.setWindowIcon(QtGui.QIcon(w.APP_ICON))
        self.setFixedWidth(425)  # ClientSize 849 at 192 DPI
        self.setObjectName("root")
        self.setAttribute(QtCore.Qt.WA_StyledBackground)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(5, 5, 6, 6)  # Padding(11)
        root.setSpacing(0)

        # shown only when asusd / cardwired are not running: one clear line instead of errors
        self.banner = QtWidgets.QLabel("")
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        self.banner.setCursor(QtCore.Qt.PointingHandCursor)
        self.banner.setContentsMargins(10, 6, 10, 2)
        self.banner.mousePressEvent = lambda _: self.start_services()
        self.banner.hide()
        self.services_missing = []
        root.addWidget(self.banner)
        # another tool changing the same settings (conflicts.py): checked at start and after Setup
        self.conflict_line = QtWidgets.QLabel("")
        self.conflict_line.setWordWrap(True)
        self.conflict_line.setCursor(QtCore.Qt.PointingHandCursor)
        self.conflict_line.setContentsMargins(10, 6, 10, 2)
        self.conflict_line.setStyleSheet(f"color: {CUSTOM.name()};")
        self.conflict_line.mousePressEvent = lambda _: self.on_setup()
        self.conflict_line.hide()
        root.addWidget(self.conflict_line)
        self.root_layout = root

        self.perf_panel = self.build_performance()
        self.gpu_panel = self.build_gpu()
        self.screen_panel = self.build_screen()
        root.addWidget(self.build_situations())
        for panel in (self.perf_panel, self.gpu_panel, self.screen_panel):
            root.addWidget(panel)
        root.addWidget(self.build_battery())
        root.addWidget(self.build_footer())
        self.apply_situations_layout()
        self.setFixedHeight(root.sizeHint().height())

        if self.cfg.get("calibration_backup"):
            # the app quit mid-calibration: undo the 100% Turbo curves first
            self.bg(restore_after_calibration, self.cfg,
                    then=lambda r: (self.cfg.pop("calibration_backup", None), self.save()))
        elif not self.cfg.get("factory_curves"):
            self.bg(snapshot_factory_curves, self.cfg, then=lambda saved: saved and self.save())
        self.hotkeys_state = ""
        self.hotkeys = hotkeys.Hotkeys(self.cfg)
        self.hotkeys.action.connect(self.on_hotkey)
        self.hotkeys.seen.connect(self.on_hotkey_seen)
        self.hotkeys.state.connect(self.on_hotkeys_state)
        self.hotkeys.captured.connect(self.on_key_captured)
        self.hotkeys.start()
        self.capture_box = None
        self.portal = shortcuts.PortalShortcuts()
        self.portal.activated.connect(self.on_shortcut)
        self.portal.bound.connect(self.on_shortcuts_bound)
        self.portal.failed.connect(lambda msg: self.portal_busy and QtWidgets.QMessageBox.warning(
            self, NAME, tr("Keyboard shortcut was not added: {msg}", msg=msg)))
        self.portal_busy = False
        if self.cfg.get("shortcuts") and self.portal.ok:
            self.portal.bind([(sid, self.shortcut_description(sid)) for sid in self.cfg["shortcuts"]])
        self.idle_available = idle_available()
        self.idle_proc = None
        QtWidgets.QApplication.instance().aboutToQuit.connect(self.stop_idle)
        QtWidgets.QApplication.instance().aboutToQuit.connect(self.stop_music)
        if self.aura_effect() == "music" and hw.music_writable():
            QtCore.QTimer.singleShot(1500, self.start_music)
        self.apply_theme()
        self.apply_topmost()
        hints = QtGui.QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda _: self.apply_theme())
        # GPU mode changed elsewhere (cardwire CLI, another app): Cardwire says so, no polling for it
        QtDBus.QDBusConnection.systemBus().connect(
            hw.CARDWIRE, hw.CARDWIRE_PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
            self, QtCore.SLOT("on_cardwire_changed(QDBusMessage)"))
        # a game was kept off the dGPU by Eco: offer Standard
        QtDBus.QDBusConnection.systemBus().connect(
            hw.CARDWIRE, hw.CARDWIRE_PATH, hw.CARDWIRE + ".Logger", "ProcessBlockedChanged",
            self, QtCore.SLOT("on_gpu_blocked(QDBusMessage)"))
        QtDBus.QDBusConnection.sessionBus().connect(
            "", "/org/freedesktop/Notifications", "org.freedesktop.Notifications", "ActionInvoked",
            self, QtCore.SLOT("on_notification_action(QDBusMessage)"))
        self.note_actions = {}      # notification id -> what its button does
        self.notifier = notifications.Notifier(NAME, APP)
        self.games_told = set()     # one hint per game and situation in a session
        self.init_games()           # Extra: a game switches the mode
        self.sleep_mark = None      # (time, Wh, on AC) when the laptop went to sleep
        self.dgpu_seen = {}         # app -> since when it keeps the dGPU awake on battery
        self.dgpu_told = {}         # app -> when the last "dGPU awake on battery" named it
        self.low_told = False       # the low battery tip came this discharge
        self.charge_limit = 100
        # back from sleep: firmware and asusd sometimes come back with other settings
        QtDBus.QDBusConnection.systemBus().connect(
            "org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
            "PrepareForSleep", self, QtCore.SLOT("on_sleep(QDBusMessage)"))
        QtDBus.QDBusConnection.systemBus().connect(
            "org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
            "PrepareForShutdown", self, QtCore.SLOT("on_shutdown(QDBusMessage)"))
        # the portal announces a theme change; Qt without the desktop's plugin (the AppImage) does not
        QtDBus.QDBusConnection.sessionBus().connect(
            "", "/org/freedesktop/portal/desktop", "org.freedesktop.portal.Settings", "SettingChanged",
            self, QtCore.SLOT("on_portal_setting(QDBusMessage)"))

        # battery health once a day, for the wear over months in the Battery window
        self.health_timer = QtCore.QTimer(self, interval=6 * 3600 * 1000, timeout=lambda: self.bg(hw.log_health))
        self.health_timer.start()
        self.fast = QtCore.QTimer(self, interval=2000, timeout=self.refresh_fast)
        self.slow = QtCore.QTimer(self, interval=5000, timeout=self.refresh_slow)
        self.fast.start()
        self.slow.start()
        # new app version: shortly after start, then once a day
        self.update_timer = QtCore.QTimer(self, interval=24 * 3600 * 1000, timeout=self.check_app_update)
        self.update_timer.start()
        QtCore.QTimer.singleShot(10000, self.check_app_update)
        # a monitor on the dGPU's port that KDE does not drive (it started in Eco): a few sysfs reads
        self.monitors_told = set()
        self.sound_told = set()  # the same for their sound: the NVIDIA sound card left root-only
        self.monitor_timer = QtCore.QTimer(self, interval=10000, timeout=self.check_monitors)
        if self.gpu_buttons:
            self.monitor_timer.start()
        self.refresh_fast()
        self.refresh_gpu()  # the tray icon shows the GPU mode; the rest comes when the window opens

    # -- layout -----------------------------------------------------------------
    def build_performance(self):
        # every block below starts SECTION_GAP under the buttons above it; the GPU and screen blocks
        # keep a line for their hover tips in that gap
        p, lay = panel(10, 10, 10, 0)
        self.perf_head = w.Header("gauge", tr("Mode"))
        lay.addWidget(self.perf_head)
        holder, g = grid(4, 64)
        self.mode_buttons = {}
        for col, (key, label, icon_name, colour) in enumerate(MODES):
            b = w.RButton(label, icon_name, accent=colour, vertical=True)
            b.setFixedHeight(60)
            b.clicked.connect(lambda _=False, k=key: self.on_profile(k))
            g.addWidget(b, 0, col)
            self.mode_buttons[key] = b
        self.fans_button = w.RButton(tr("Fans + Power"), "fan", secondary=True, accent=CUSTOM, vertical=True)
        self.fans_button.setFixedHeight(60)
        self.fans_button.clicked.connect(self.on_fans)
        g.addWidget(self.fans_button, 0, 3)
        lay.addWidget(holder)
        return p

    def build_gpu(self):
        p, lay = panel(10, SECTION_GAP - 2, 10, 0)
        self.gpu_head = w.Header("gpu", tr("GPU Mode"))
        lay.addWidget(self.gpu_head)
        holder, g = grid(4, 64)
        self.gpu_tip = QtWidgets.QLabel("")
        self.gpu_tip.setObjectName("tip")
        self.gpu_tip.setFixedHeight(TIP_LINE)
        self.gpu_tip.setWordWrap(True)
        self.gpu_tip.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        supported = hw.gpu_supported()  # none without a dGPU: the panel stays hidden
        self.gpu_buttons = {}
        tips = {}
        col = 0
        for key, label, icon_name, colour, _, tip in GPU_MODES:
            if key not in supported:
                continue
            b = w.RButton(label, icon_name, accent=colour, vertical=True)
            b.setFixedHeight(60)
            b.clicked.connect(lambda _=False, k=key: self.on_gpu(k))
            g.addWidget(b, 0, col)
            col += 1
            self.gpu_buttons[key] = b
            tips[b] = tip
        self.optimized = None
        if "Integrated" in supported and "Hybrid" in supported:
            self.optimized = w.RButton(tr("Optimized"), "sparkles", accent=ECO, vertical=True)
            self.optimized.setFixedHeight(60)
            self.optimized.clicked.connect(self.on_optimized)
            g.addWidget(self.optimized, 0, col)
            tips[self.optimized] = OPTIMIZED_TIP
        self.gpu_hover = HoverTip(self.gpu_tip, tips)
        self.gpu_target = None
        self.gpu_tip.mousePressEvent = lambda _: self.on_gpu_tip_clicked()
        for b in tips:
            b.installEventFilter(self.gpu_hover)
        lay.addWidget(holder)
        lay.addWidget(self.gpu_tip)
        return p

    def build_screen(self):
        p, lay = panel(10, SECTION_GAP - 2 - TIP_LINE, 10, 0)
        self.screen_head = w.Header("laptop", tr("Screen"))
        lay.addWidget(self.screen_head)
        holder, g = grid(3, 40)  # three choices, no empty fourth cell
        self.screen_tip = QtWidgets.QLabel("")
        self.screen_tip.setObjectName("tip")
        self.screen_tip.setFixedHeight(TIP_LINE)
        self.screen_tip.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        self.screen_auto = w.RButton(tr("Auto"), accent=GRAY)
        self.screen_60 = w.RButton(tr("60Hz"), accent=GRAY)
        self.screen_max = w.RButton(tr("Max"), accent=GRAY)
        for col, (b, key) in enumerate(((self.screen_auto, "auto"), (self.screen_60, "60"),
                                        (self.screen_max, "max"))):
            b.setFixedHeight(36)
            b.clicked.connect(lambda _=False, k=key: self.on_screen(k))
            g.addWidget(b, 0, col)
        self.screen_hover = HoverTip(self.screen_tip, {
            self.screen_auto: tr("Sets 60Hz to save battery, and back when plugged"),
            self.screen_60: tr("60Hz refresh rate to save battery"),
            self.screen_max: tr("Max refresh rate for lower latency")})
        for b in (self.screen_auto, self.screen_60, self.screen_max):
            b.installEventFilter(self.screen_hover)
        lay.addWidget(holder)
        lay.addWidget(self.screen_tip)
        return p

    def build_battery(self):
        """Below the modes, where G-Helper has it, but saying first what people look for: the charge
        and the time left. The limit is the slider's own caption."""
        p, lay = panel(10, SECTION_GAP - 2 - TIP_LINE, 10, 0)
        self.bat_panel = p
        p.installEventFilter(self)
        self.bat_head = w.Header("battery-charging", tr("Battery"), height=22)
        self.bat_history = history = w.IconButton("chart-line", size=18, nudge=(0.5, -0.5))
        history.setToolTip(tr("Battery history and health"))
        history.clicked.connect(self.on_battery)
        self.bat_head.right.setStretch(0, 0)
        self.bat_head.right.insertSpacing(1, 6)
        self.bat_head.right.insertWidget(2, history, 0, QtCore.Qt.AlignTop)  # level with the title text
        self.bat_head.right.insertStretch(3, 1)
        lay.addWidget(self.bat_head)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.limit_label = QtWidgets.QLabel("")
        self.limit_label.setObjectName("tip")
        self.limit_label.setContentsMargins(4, 0, 0, 0)
        self.limit_label.setFixedWidth(QtGui.QFontMetrics(self.font()).horizontalAdvance(
            tr("Limit {value}%", value=100)) + 10)
        self.limit_label.setToolTip(tr("Charging stops here, so the battery lasts more years"))
        self.limit_steps = hw.charge_limit_steps(hw.model_name())
        self.slider = w.Slider(self.limit_steps[0] if self.limit_steps else 40, 100, 5)
        self.slider.values = self.limit_steps
        self.slider.valueChanged.connect(self.show_limit)
        self.slider.released.connect(self.on_limit)
        self.full_button = w.RButton(tr("Full once"), secondary=True, radius=2, borderless=True)
        f = self.full_button.font()
        f.setPointSizeF(f.pointSizeF() * 7.125 / 9)
        f.setBold(True)
        self.full_button.setFont(f)
        self.full_button.text_colour = w.THEME.control_dark
        self.full_button.setFixedSize(QtGui.QFontMetrics(f).horizontalAdvance(self.full_button.text()) + 14, 18)
        self.full_button.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.full_button.clicked.connect(self.on_full_charge)
        self.full_button.installEventFilter(self)
        row.addWidget(self.limit_label)
        row.addWidget(self.slider, 1)
        row.addSpacing(4)
        row.addWidget(self.full_button, 0, QtCore.Qt.AlignVCenter)
        holder = QtWidgets.QWidget()
        holder.setLayout(row)
        holder.setFixedHeight(22)
        lay.addWidget(holder)
        # G-Helper's row under the slider: Run on Startup (set once, but people look for it here) and
        # a line: health while hovering the panel, what "Full once" does while hovering it, otherwise
        # who keeps the NVIDIA GPU awake on battery or why the limit does not hold
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(5, 0, 0, 0)
        self.startup = QtWidgets.QCheckBox(tr("Run on Startup"))
        self.startup.setChecked(os.path.exists(AUTOSTART))
        self.startup.toggled.connect(self.on_startup)
        self.charge_label = QtWidgets.QLabel("")
        self.charge_label.setObjectName("dim")
        self.charge_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.charge_label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        row.addWidget(self.startup)
        row.addWidget(self.charge_label, 1)
        holder = QtWidgets.QWidget()
        holder.setLayout(row)
        holder.setFixedHeight(25)
        lay.addWidget(holder)
        return p

    def build_footer(self):
        """One row: the version (a link, or the update when there is one), then Updates, Extra, Quit.
        Credits moved into Extra."""
        p = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(p)
        lay.setContentsMargins(12, 6, 10, 10)
        lay.setSpacing(4)
        version = QtWidgets.QLabel(tr("Version: {version}", version=VERSION))
        version.setObjectName("version")
        version.setCursor(QtCore.Qt.PointingHandCursor)
        version.mousePressEvent = lambda _: self.on_version_clicked()
        self.version_label = version
        self.release_url = HOMEPAGE
        self.latest_release = None
        lay.addWidget(version)
        lay.addStretch()
        updates = w.RButton(tr("Updates"), "download", secondary=True, radius=2)
        updates.clicked.connect(self.on_updates)
        self.extra_button = w.RButton(tr("Extra"), "settings", secondary=True, radius=2)
        self.extra_button.clicked.connect(self.on_extra)
        quit_b = w.RButton(tr("Quit"), "log-out", secondary=True, radius=2)
        quit_b.clicked.connect(quit_app)
        for b in (updates, self.extra_button, quit_b):
            b.setFixedSize(84, 24)
            b.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            lay.addWidget(b)
        return p

    # -- "you started a game in Eco / Silent" -------------------------------------
    def hint(self, key, body, action, then):
        """A notification with a button; each key is shown once a session (no nagging)."""
        if key in self.games_told or not self.cfg.get("game_hints", True):
            return
        self.games_told.add(key)
        self.notify("input-gaming", body, action, then)

    def tip(self, icon, body, action=None, then=None):
        """A battery tip (Extra: "Battery tips"): a notification, with a button when action is given."""
        if self.cfg.get("battery_tips", True):
            self.notify(icon, body, action, then)

    def notify(self, icon, body, action=None, then=None):
        """From the app's own D-Bus connection, so the desktop keeps the button (notifications.py);
        without dbus-python through busctl, where a click on the notification does it instead."""
        if self.notifier.send(icon, NAME, body, action, then):
            return
        if not action:
            self.bg(hw.notify, icon, NAME, body)
            return

        def shown(note_id):
            if note_id is not None:
                self.note_actions[note_id] = then
        self.bg(hw.notify_action, icon, NAME, body, action, then=shown)

    @QtCore.Slot(QtDBus.QDBusMessage)
    def on_notification_action(self, msg):
        args = msg.arguments()
        if len(args) == 2 and args[1] in hw.NOTIFY_ACTIONS:
            action = self.note_actions.pop(int(args[0]), None)
            if action:
                action()

    def report_sleep(self, ac):
        """How much battery the sleep cost; when it was a lot and deep sleep is off, where to change it."""
        mark, self.sleep_mark = self.sleep_mark, None
        energy = hw.battery_energy()
        if not mark or not energy or ac or mark[2]:  # charging before or after: nothing to tell
            return
        seconds = time.time() - mark[0]
        lost_wh = mark[1][0] - energy[0]
        if seconds < 60 or lost_wh < 0:
            return
        hours = seconds / 3600
        percent = lost_wh / energy[1] * 100
        log.info(f"sleep: {hours:.1f} h, -{percent:.1f}% ({lost_wh / hours:.2f} W)")
        if seconds < SLEEP_REPORT_AFTER:
            return
        body = tr("Asleep for {time}: the battery lost {percent}% ({watts} W).",
                  time=duration(seconds),
                  percent=f"{percent:.0f}", watts=f"{lost_wh / hours:.1f}")
        if percent / hours >= SLEEP_DRAIN_HIGH and hw.deep_sleep() is False:
            self.tip("battery-low", body + " " + tr("Deep sleep uses almost none."),
                     tr("Deep sleep settings"), self.on_extra)
        else:
            self.tip("battery", body)

    def check_dgpu_on_battery(self):
        """On battery, apps that kept the dGPU awake for DGPU_AWAKE_TELL s: one notification
        listing them all, with Eco. Each app is named again a day later at the earliest."""
        now = time.monotonic()
        on_battery = (self.last_ac is False and self.dgpu_was_awake
                      and self.gpu_current not in ("Integrated", "AsusMuxDgpu", None))
        apps = self.dgpu_apps if on_battery else []
        self.dgpu_seen = {a: self.dgpu_seen.get(a, now) for a in apps}
        due = [a for a, since in self.dgpu_seen.items()
               if now - since >= DGPU_AWAKE_TELL and now - self.dgpu_told.get(a, -86400) >= 86400]
        if not due or not self.cfg.get("battery_tips", True):
            return
        for a in apps:
            self.dgpu_told[a] = now

        def told(data):
            games, watts = data
            if games:  # playing on battery is a choice
                return
            body = tr("On battery, the NVIDIA GPU is kept awake by: {apps}.", apps=", ".join(apps))
            if watts:
                body += " " + tr("It draws about {watts} W; Eco turns it off.", watts=f"{watts:.0f}")
            self.tip("video-display", body, tr("Switch to Eco"), lambda: self.on_gpu("Integrated"))
        self.bg(lambda: (hw.dgpu_games(), hw.dgpu_watts()), then=told)

    def check_low_battery(self):
        """At LOW_BATTERY % on battery in a heavier mode: Silent (and Eco), once per discharge."""
        bat = self.bat
        if not bat or bat["percent"] is None:
            return
        if bat["status"] != "Discharging":
            if bat["status"] in ("Charging", "Full"):
                self.low_told = False
            return
        if self.low_told or bat["percent"] > LOW_BATTERY or self.gpu_current is None:  # GPU not read yet
            return
        self.low_told = True
        silent = self.last_profile == "quiet" and not self.cfg.get("active_mode")
        eco = self.gpu_current == "Hybrid" and hw.cardwire_mode() is not None
        if silent and not eco:
            return

        # no "N min more in Silent": the week's average per mode mixes in what was done in each mode
        # (Silent often averages more than Balanced), so it is not the mode's cost
        if eco and not silent:
            body = tr("Battery at {percent}%. Silent and Eco make it last longer.", percent=bat["percent"])
            action = tr("Switch to Silent and Eco")
        elif eco:
            body = tr("Battery at {percent}%. Eco makes it last longer.", percent=bat["percent"])
            action = tr("Switch to Eco")
        else:
            body = tr("Battery at {percent}%. Silent makes it last longer.", percent=bat["percent"])
            action = tr("Switch to Silent")

        def switch():
            if not silent:
                self.on_profile("quiet", notify=True)
            if eco:
                self.on_gpu("Integrated")
        self.tip("battery-caution", body, action, switch)

    @QtCore.Slot(QtDBus.QDBusMessage)
    def on_gpu_blocked(self, _msg):
        def job():
            last = hw.cardwire_last_blocked()
            return last and hw.game_name(last[0])
        self.bg(job, then=self.game_blocked)

    def game_blocked(self, game):
        if game and self.gpu_current == "Integrated":
            self.hint(("eco", game), tr("{game} can't use the NVIDIA GPU in Eco. Switch to Standard? "
                                        "Restart the game afterwards.", game=game),
                      tr("Switch to Standard"), lambda: self.on_gpu("Hybrid"))

    def check_games(self):
        """The dGPU woke up or its users changed: a game in Silent gets a hint."""
        if hw.get_profile() != "quiet" or self.cfg.get("active_mode"):
            return
        self.bg(hw.dgpu_games, then=lambda games: games and self.hint(
            ("silent", games[0]), tr("{game} is running in Silent mode. Switch to Turbo?", game=games[0]),
            tr("Switch to Turbo"), lambda: self.on_profile("performance", notify=True)))

    @QtCore.Slot(QtDBus.QDBusMessage)
    def on_sleep(self, msg):
        if msg.arguments() and msg.arguments()[0]:   # True: going to sleep, read it now (sysfs, quick)
            energy = hw.battery_energy()
            self.sleep_mark = (time.time(), energy, hw.ac_online()) if energy else None
        elif msg.arguments():                        # False: resumed
            QtCore.QTimer.singleShot(3000, self.after_resume)  # let the firmware settle first

    @QtCore.Slot(QtDBus.QDBusMessage)
    def on_shutdown(self, msg):
        """Shutting down: the charge and limit now, to see at the next start whether the battery
        charged past the limit while the laptop was off (check_charged_while_off)."""
        bat = hw.battery_status() if msg.arguments() and msg.arguments()[0] else None
        if bat and bat.get("percent") is not None:
            self.cfg["off_mark"] = {"time": time.time(), "percent": bat["percent"], "limit": self.charge_limit}
            self.save()

    def check_charged_while_off(self):
        """ASUS firmware sets the limit back to 100% at power-on, and on some models it does not hold
        it while off either; asusd puts it back only once Linux runs. Said once per occurrence."""
        mark = self.cfg.pop("off_mark", None)
        if not mark:
            return
        self.save()
        bat = hw.battery_status()
        limit = mark.get("limit", 100)
        if not bat or bat.get("percent") is None or limit >= 100:
            return
        before, now = mark["percent"], bat["percent"]
        log.info(f"off: {before}% -> {now}% (limit {limit}%)")
        if now >= max(before, limit) + 3:
            self.tip("battery-full-charging", tr(
                "While the laptop was off, the battery charged from {before}% to {now}%, past the {limit}% "
                "limit: this laptop's firmware holds the limit only while Linux runs. Sleep keeps it.",
                before=before, now=now, limit=limit))

    def after_resume(self):
        """Puts back what this laptop should have after sleep: the charger may have changed while
        asleep, and the firmware sometimes resets the mode, the lighting or the screen rate."""
        log.info("resumed from sleep")
        ac = hw.ac_online()
        self.report_sleep(ac)
        self.last_ac = ac
        if self.cfg.get("screen") == "auto":
            self.apply_screen_auto()
        if self.cfg.get("gpu_auto"):
            self.apply_gpu_auto()
        self.on_power_source(ac)
        self.restore_kbd_level(ac)
        if self.aura_effect() == "music":
            pass  # still running: it picks up again by itself
        elif self.cfg.get("aura_effect") and not os.path.exists(hw.KBD_IDLE_FILE):
            self.bg(hw.set_aura, self.aura_effect(), self.cfg.get("aura_colour", "ffffff"),
                    self.cfg.get("aura_speed", "med"))
        self.refresh_fast()

    @QtCore.Slot(QtDBus.QDBusMessage)
    def on_cardwire_changed(self, _msg):
        self.refresh_gpu()

    @QtCore.Slot(QtDBus.QDBusMessage)
    def on_portal_setting(self, msg):
        args = msg.arguments()
        if len(args) != 3 or args[0] != "org.freedesktop.appearance":
            return
        value = args[2].variant() if isinstance(args[2], QtDBus.QDBusVariant) else args[2]
        if args[1] == "color-scheme" and value in (1, 2):  # 1 dark, 2 light, 0 no preference
            self.apply_theme(dark=value == 1)
        elif args[1] == "accent-color":
            self.apply_theme()

    def apply_theme(self, dark=None):
        """Follows the desktop's light / dark setting, as G-Helper follows Windows'. The portal
        is asked first: Qt without the desktop's own plugin (the AppImage) reads the scheme only
        at start; changes come as the portal's SettingChanged signal. Extra can fix the theme to
        dark or light and take the desktop's accent colour."""
        theme = self.cfg.get("theme", "auto")
        if theme in ("dark", "light"):
            dark = theme == "dark"
        if dark is None:
            dark = hw.os_dark()
        if dark is None:
            scheme = QtGui.QGuiApplication.styleHints().colorScheme()
            dark = scheme != QtCore.Qt.ColorScheme.Light  # unknown: G-Helper's dark default
        accent = hw.os_accent() if self.cfg.get("desktop_accent") else None
        if (dark, accent) == self.theme_state:
            return
        self.theme_state = (dark, accent)
        w.set_theme(dark)
        w.set_accent(QtGui.QColor(*accent) if accent else None)
        self.setStyleSheet(stylesheet())
        self.full_button.text_colour = w.THEME.control_dark
        for child in self.findChildren(QtWidgets.QWidget):
            child.update()
        if self.tray:
            self.tray.apply_theme()

    # -- plumbing -------------------------------------------------------------
    def bg(self, fn, *args, then=None, key=None):
        """Run fn(*args) off the GUI thread, then call `then(result)` on it. The timers' jobs pass a
        key and are skipped while the last one with it still runs: a hung busctl or nvidia-smi
        must not pile them up in front of what the user clicked."""
        if key:
            if key in self.bg_running:
                return
            self.bg_running.add(key)

        def job():
            try:
                result = fn(*args)
            except Exception:  # the pool would keep it in a Future nobody reads
                log.exception(f"background job {getattr(fn, '__name__', fn)} failed")
                return
            finally:
                self.bg_running.discard(key)
            if then:
                self.bridge.done.emit(then, result)
        self.pool.submit(job)

    def report(self, result, what):
        ok, out = result
        if ok:
            return
        log.warning(f"{what} failed: {out}")
        if self.services_missing:
            self.flash_banner()  # the banner already says why; no raw command errors
            return
        QtWidgets.QMessageBox.warning(self, NAME, tr("{what} failed:", what=what) + f"\n{out}")

    # -- asusd / cardwired status ------------------------------------------------------
    def show_services(self, running):
        missing = [n for n in ("asusd", "cardwired") if n not in running]
        if missing == self.services_missing:
            return
        self.services_missing = missing
        if missing:
            names = ", ".join(missing)
            self.banner.setText(tr("Not running: {names}. Settings cannot be changed. Click here to start.",
                                   names=names))
        self.banner.setVisible(bool(missing))
        self.setFixedHeight(self.root_layout.sizeHint().height())

    def flash_banner(self):
        self.banner.setStyleSheet(f"color: {TURBO.name()}; font-weight: bold;")
        QtCore.QTimer.singleShot(1200, lambda: self.banner.setStyleSheet(""))

    def start_services(self):
        missing = list(self.services_missing)
        if not missing:
            return
        if not all(shutil.which(SERVICE_TOOLS[n]) for n in missing if n in SERVICE_TOOLS):
            self.on_setup()  # not installed: Setup installs them
            return
        self.banner.setText("Starting " + " and ".join(missing) + " ...")
        self.bg(hotkeys.run_as_root, "systemctl start " + " ".join(missing),
                then=lambda r: (self.report_plain(r, tr("Starting services")),
                                setattr(self, "services_missing", ["?"]), self.refresh_slow(),
                                self.refresh_fast()))

    def report_plain(self, result, what):
        ok, out = result
        if not ok:
            log.warning(f"{what} failed: {out}")
        if not ok and out:
            QtWidgets.QMessageBox.warning(self, NAME, tr("{what} failed:", what=what) + f"\n{out}")

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind not in (QtCore.QEvent.Enter, QtCore.QEvent.Leave) or not hasattr(self, "charge_label"):
            return False
        if obj is self.bat_panel:
            self.battery_hover = kind == QtCore.QEvent.Enter
            self.update_charge_label()
        elif obj is self.full_button:
            self.full_hover = kind == QtCore.QEvent.Enter
            self.update_charge_label()
        return False

    # -- refresh ----------------------------------------------------------------
    def refresh_fast(self):
        ac = hw.ac_online()
        if ac != self.last_ac:
            first = self.last_ac is None
            self.last_ac = ac
            if self.cfg.get("screen") == "auto":
                self.apply_screen_auto()
            if not first and self.cfg.get("gpu_auto"):
                self.apply_gpu_auto()
            self.apply_idle()
            if not first:
                self.on_power_source(ac)
            if not first and not ac and self.cfg.get("full_once"):
                self.end_full_charge()
            if not first:
                self.restore_kbd_level(ac)

        self.ticks += 1
        full = self.isVisible() or self.ticks % TRAY_SLOW == 1  # also the first refresh
        profile = hw.get_profile()
        if not self.mode_busy and profile != self.last_profile:
            if self.last_profile is not None and profile in hw.ASUSD_PROFILE:
                # changed outside the app (Fn+F5): it becomes this power source's mode, with a toast
                self.remember_mode(profile)
                self.toast_mode(profile)
            self.last_profile = profile
        if not self.mode_busy:
            self.apply_boost(profile)
            mode = self.cfg.get("active_mode") or profile
            if mode and mode != getattr(self, "logged_mode", None):  # for Battery's draw per mode
                self.logged_mode = mode
                self.bg(hw.log_mode, mode)
        custom = modes.active(self.cfg)
        if custom and profile != custom["base"] and not self.mode_busy:
            # profile changed outside (Fn+F5, asusd on charger change): the custom mode ended
            self.mode_busy = True
            self.bg(lambda: (modes.deactivate(self.cfg), self.save()),
                    then=lambda _: (setattr(self, "mode_busy", False), self.modes_changed()))
            custom = None
        for key, b in self.mode_buttons.items():
            b.set_activated(key == profile and not custom)
        # Settings.VisualiseMode: a custom mode lights "Fans + Power" in its base colour
        self.fans_button.set_activated(bool(custom))
        if custom:
            self.fans_button.set_accent({m[0]: m[3] for m in MODES}.get(custom["base"], CUSTOM))
            name = custom["name"]
        else:
            name = next((m[1] for m in MODES if m[0] == profile), profile or "?")
        self.perf_head.title.setText(tr("Mode: {name}", name=name))
        # dGPU temperature only while the window is open and an app keeps the dGPU awake anyway;
        # asleep it shows the last one (G-Helper 0.248, issue #5290), in Eco none
        now = time.monotonic()
        eco = self.gpu_current == "Integrated"
        power = None if eco else hw.dgpu_power()  # in Eco it sleeps; asking Cardwire costs a process
        if power is None or eco:
            self.dgpu_temp = None
        query = (power == "active" and not eco and self.isVisible() and self.dgpu_apps
                 and now - self.dgpu_temp_at >= DGPU_TEMP_EVERY)
        if query:
            self.dgpu_temp_at = now
        awake = power == "active" and not eco
        # who keeps the dGPU awake (tray tooltip): asked when it wakes, then every DGPU_APPS_EVERY s
        ask_apps = awake and (not self.dgpu_was_awake or now - self.dgpu_apps_at >= DGPU_APPS_EVERY)
        self.dgpu_was_awake = awake
        if ask_apps:
            self.dgpu_apps_at = now
        elif not awake:
            self.dgpu_apps = []
            self.dgpu_seen = {}
        self.bg(lambda: dict(hw.sensors(query, fans=full), dgpu_apps=hw.dgpu_users() if ask_apps else None),
                then=self.show_sensors, key="sensors")

        limit = hw.get_charge_limit()
        self.charge_limit = limit or 100
        if limit is not None and not self.slider.dragging:
            self.slider.set_value(limit)
            self.show_limit(limit)
            if self.cfg.get("full_once") and limit != 100:
                self.cfg.pop("full_once")
                save_config(self.cfg)
        self.full_button.set_activated(bool(self.cfg.get("full_once")))

        self.bat = hw.battery_status()
        text = ""
        if self.bat and self.bat["watts"]:
            if self.bat["status"] == "Charging":
                text = tr("Charging: {watts}W", watts=f"{self.bat['watts']:.1f}")
            elif self.bat["status"] == "Discharging":
                text = tr("Discharging: {watts}W", watts=f"{self.bat['watts']:.1f}")
        self.bat_head.info.setText(text)
        if text and self.isVisible():  # time left / to full is shown in the window only
            self.bg(hw.battery_time, self.charge_limit,
                    then=lambda t: (setattr(self, "bat_time", t), self.update_charge_label()), key="battery_time")
        else:
            self.bat_time = None
        self.update_charge_label()
        self.check_low_battery()

        if full:
            level = hw.get_kbd_level()
            self.kbd_level = level
            self.remember_kbd_level(ac, level)

        if self.bat and self.cfg.get("full_once") and self.bat["percent"] == 100:
            self.end_full_charge()
        self.show_situations()

    def refresh_slow(self):
        """What only the open window shows: services, GPU panel, screen. In the tray none of it
        runs; a GPU mode change elsewhere still arrives as Cardwire's signal."""
        if not self.isVisible():
            return
        self.bg(hw.services_running, then=self.show_services, key="services")
        self.refresh_gpu(key="gpu")
        self.bg(lambda: (hw.screen_info(), hw.get_overdrive()), then=self.show_screen, key="screen")

    def refresh_gpu(self, key=None):
        """key: from the timer. After a switch or Cardwire's signal it always runs, so the state it
        reads is newer than the change."""
        if not self.gpu_buttons:
            return
        eco = self.gpu_current == "Integrated"
        self.bg(lambda: (hw.gpu_mode(), hw.gpu_pending(), hw.dgpu_power(blocked=eco)), then=self.show_gpu,
                key=key)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "slow"):  # opened from the tray: catch up at once
            QtCore.QTimer.singleShot(0, self.refresh_slow)
        # the windows that were open when this one closed come back, beside it (KWin script)
        hidden, self.hidden_windows = getattr(self, "hidden_windows", []), []
        for i, window in enumerate(hidden):
            QtCore.QTimer.singleShot(150 + 50 * i, window.show)

    def hideEvent(self, event):
        """Closing the main window (or a tray click) closes Fans, Extra, Updates, Battery too."""
        super().hideEvent(event)
        if event.spontaneous():  # minimised by the desktop: leave the others alone
            return
        self.hidden_windows = [c for c in self.findChildren(QtWidgets.QWidget)
                               if c.isWindow() and c.isVisible() and not isinstance(c, QtWidgets.QDialog)]
        for window in self.hidden_windows:
            window.hide()

    def update_charge_label(self):
        """The battery title (charge, and the time left, to the limit or to full) and the line under
        the slider."""
        bat = getattr(self, "bat", None)
        title = tr("Battery")
        if bat and bat["percent"] is not None:
            percent = f"{bat['percent']}%"
            if bat["status"] == "Discharging" and bat["percent"] < LOW_BATTERY:
                colour = TURBO if bat["percent"] < LOW_BATTERY / 2 else CUSTOM
                percent = f"<span style='color:{colour.name()}'>{percent}</span>"
            title = tr("Battery: {percent}", percent=percent)
            if self.bat_time:
                kind, secs = self.bat_time
                time_text = duration(secs)
                title += " · " + (tr("{time} left", time=time_text) if kind == "left" else
                                  tr("{time} to {limit}%", time=time_text, limit=self.charge_limit)
                                  if kind == "to limit" else tr("{time} to full", time=time_text))
            elif bat["status"] == "Not charging" and self.last_ac:
                # above the limit it stays where it is until it drops below; it does not discharge to it
                title += " · " + tr("not charging, limit {limit}%", limit=self.charge_limit)
        self.bat_head.title.setText(title)

        tip = ""
        if self.full_hover:
            text = tr("One time charge to 100%")
        elif self.battery_hover:
            health = hw.battery_health()
            cycles = hw.battery_cycles()  # many ASUS batteries report 0
            text = tr("Battery Health: {health}%", health=f"{health:.1f}") if health else ""
            if text and cycles:
                text += ", " + tr("{count} cycles", count=cycles)
        elif self.limit_steps and self.charge_limit not in self.limit_steps:
            text = tr("{limit}% works as 100% here", limit=self.charge_limit)
            tip = tr("This laptop's firmware stops charging only at 60-80% or at 100%; other limits "
                     "charge to full. Pick 80% or less to keep a limit.")
        elif self.dgpu_seen and self.last_ac is False:
            # the same apps the "NVIDIA awake on battery" tip names, but here from the first minute
            since = time.monotonic() - min(self.dgpu_seen.values())
            text = tr("NVIDIA awake on battery for {time}: {apps}", time=duration(since),
                      apps=", ".join(sorted(self.dgpu_seen)))
            tip = tr("Eco turns it off; the apps keep running on the other GPU")
        else:
            text = ""
        self.charge_label.setText(text)
        self.charge_label.setToolTip(tip)

    def show_limit(self, value):
        self.limit_label.setText(tr("Limit {value}%", value=value))

    def show_sensors(self, s):
        fans = self.last_fans = s["fans"] if s["fans"] is not None else self.last_fans

        def fan(name):
            """FanSensorControl.FormatFan: percent of the (calibrated) max RPM."""
            rpm = fans.get(name)
            if rpm is None:
                return ""
            if fan_max(self.cfg, name) < rpm < INADEQUATE_RPM and not self.cfg.get("calibration_backup"):
                self.cfg[f"fan_max_{name}"] = rpm
                save_config(self.cfg)
            if self.cfg.get("fan_rpm", True):  # RPM by default: a reading, true without calibration
                return tr("Fan: {value}", value=f"{rpm}RPM")
            return tr("Fan: {value}", value=f"{min(100, round(rpm * 100 / fan_max(self.cfg, name)))}%")

        if s["dgpu"] is not None:
            self.dgpu_temp = s["dgpu"]
        cpu = f": {s['cpu']:.0f}°C" if s["cpu"] is not None else ""
        if self.dgpu_temp is not None:
            gpu = f": {self.dgpu_temp}°C"
        elif self.gpu_current == "Integrated":
            gpu = ": " + tr("off")
        elif not self.dgpu_was_awake:
            gpu = ": " + tr("sleeping")
        else:
            gpu = ""
        self.perf_head.info.setText(f"CPU{cpu}  {fan('cpu')}")

        self.gpu_head.info.setText(f"GPU{gpu}  {fan('gpu')}")
        self.sit_head.info.setText(f"{self.perf_head.info.text()}\n{self.gpu_head.info.text()}")
        if self.tray:
            tip = f"CPU{cpu} {fan('cpu')}\nGPU{gpu} {fan('gpu')}"
            if self.bat_head.info.text():
                tip += "\n" + self.bat_head.info.text()
            if s.get("dgpu_apps") is not None:
                if s["dgpu_apps"] and s["dgpu_apps"] != self.dgpu_apps:
                    self.check_games()
                self.dgpu_apps = s["dgpu_apps"]
                self.check_dgpu_on_battery()
            # the dGPU's state, and what keeps it from sleeping (ROG Control Center)
            if self.gpu_current == "Integrated":
                tip += "\n" + tr("dGPU: off (Eco)")
            elif not self.dgpu_was_awake:
                tip += "\n" + tr("dGPU: sleeping")
            elif self.dgpu_apps:
                tip += "\n" + tr("dGPU awake: {apps}", apps=", ".join(self.dgpu_apps))
            else:
                tip += "\n" + tr("dGPU: awake")
            self.tray.setToolTip(tip)

    def show_gpu(self, data):
        mode, pending, power = data
        self.dgpu_awake = power == "active"
        if self.gpu_busy:
            return
        self.gpu_current = mode
        after = self.cfg.get("gpu_after_reboot")
        if after and mode and mode != "AsusMuxDgpu" and not pending:
            # back from Ultimate: the MUX is on the iGPU again, now the mode that was picked
            self.cfg.pop("gpu_after_reboot")
            save_config(self.cfg)
            if after != mode:
                self.switch_gpu(after)
                return
        # waiting for the reboot of a MUX change: what we asked for
        target = self.gpu_target if pending and self.gpu_target != mode else None
        if not target:
            self.gpu_target = None
        self.show_gpu_pending(mode, target, pending if target else None)
        auto = bool(self.cfg.get("gpu_auto"))
        for key, b in self.gpu_buttons.items():
            b.set_activated(key == mode and not (auto and key in ("Integrated", "Hybrid")))
        if self.optimized:
            self.optimized.set_activated(auto)
            self.optimized.set_accent(ECO if mode == "Integrated" else STANDARD)
        label = next((m[4] for m in GPU_MODES if m[0] == mode), mode or "?")
        if target:
            to = next((m[4] for m in GPU_MODES if m[0] == target), target)
            self.gpu_head.title.setText(tr("GPU Mode: {mode}", mode=f"{label} \u2192 {to}"))
        else:
            self.gpu_head.title.setText(tr("GPU Mode: {mode}", mode=label))
        if self.tray:
            self.tray.set_gpu(mode)
        self.show_situations()

    def show_gpu_pending(self, mode, target, pending):
        for key, b in self.gpu_buttons.items():
            b.set_pending(key == target)
        if target or pending:
            action = (pending or "Reboot required to complete mode change").rstrip(".")
            reboot = "reboot" in action.lower() or "restart" in action.lower()
            self.gpu_logout_reboot = reboot
            self.gpu_hover.set_rest(tr("{action}. Click here to restart now, or pick the current mode to cancel.",
                                       action=tr(action)) if reboot else
                                    tr("{action}. Click here to log out now, or pick the current mode to cancel.",
                                       action=tr(action)))
            self.gpu_tip.setObjectName("pending")
            self.gpu_tip.setCursor(QtCore.Qt.PointingHandCursor)
        else:
            self.gpu_hover.set_rest("")
            self.gpu_tip.setObjectName("tip")
            self.gpu_tip.unsetCursor()
        self.gpu_tip.style().unpolish(self.gpu_tip)
        self.gpu_tip.style().polish(self.gpu_tip)
        height = 2 * TIP_LINE if (target or pending) else TIP_LINE  # the pending note takes two lines
        if self.gpu_tip.height() != height:
            self.gpu_tip.setFixedHeight(height)
            # layouts pick the new height up on the next event loop pass
            QtCore.QTimer.singleShot(0, lambda: self.setFixedHeight(self.root_layout.sizeHint().height()))

    def on_gpu_tip_clicked(self):
        if self.gpu_tip.objectName() != "pending":
            return
        if not hw.logout_prompt(getattr(self, "gpu_logout_reboot", False)):
            QtWidgets.QMessageBox.information(self, NAME, tr("Log out (or restart) to finish the GPU mode change."))

    def show_screen(self, data):
        info, overdrive = data
        if not info:
            self.screen_head.title.setText(tr("Screen: Turned off"))
            for b in (self.screen_auto, self.screen_60, self.screen_max):
                b.setEnabled(False)
            return
        _, hz, rates = info
        for b in (self.screen_auto, self.screen_60, self.screen_max):
            b.setEnabled(True)
        self.screen_head.title.setText(tr("Screen: {hz}Hz", hz=hz) + (tr(" + Overdrive") if overdrive else ""))
        self.screen_60.setText(tr("{hz}Hz", hz=min(rates)))
        od = overdrive is not None and not self.cfg.get("no_overdrive")
        self.screen_max.setText(tr("{hz}Hz", hz=max(rates)) + (" + OD" if od else ""))
        auto = self.cfg.get("screen") == "auto"
        self.screen_auto.set_activated(auto)
        self.screen_60.set_activated(not auto and hz <= min(rates))
        self.screen_max.set_activated(not auto and hz > min(rates))
        self.show_situations()  # the "Max" button and tiles say the real rate

    # -- actions ----------------------------------------------------------------
    def on_profile(self, key, notify=False, remember=True):
        """A base mode (quiet / balanced / performance) or a custom mode id.
        notify: show the toast (mode picked by a key, not in the window).
        remember: kept as the power source's mode (not for a game's, which ends with the game)."""
        log.info(f"mode -> {key}")
        if remember:
            self.remember_mode(key)
            self.game_mode_picked()
        if notify:
            self.toast_mode(key)
        if modes.get(self.cfg, key):
            self.mode_busy = True

            def job():
                result = modes.activate(self.cfg, key)
                self.save()
                return result
            self.bg(job, then=self.mode_done)
            return
        for k, b in self.mode_buttons.items():
            b.set_activated(k == key)
        self.mode_busy = True

        def job():
            if modes.active(self.cfg):
                modes.deactivate(self.cfg)
                self.save()
            return hw.set_profile(key)
        self.bg(job, then=self.mode_done)

    def mode_done(self, result):
        self.mode_busy = False
        self.last_profile = hw.get_profile()
        self.report(result, tr("Mode"))
        self.refresh_fast()
        self.modes_changed()

    def apply_boost(self, profile=None):
        """CPU boost of the current mode (Fans → Advanced): checked on every fast refresh, so a
        mode change from anywhere (Fn+F5, the charger, a custom mode) carries it along."""
        if not self.cfg.get("cpu_boost"):
            return  # never set: the system's own choice stays
        key = self.cfg.get("active_mode") or profile or hw.get_profile()
        want = self.cfg["cpu_boost"].get(key, True)
        if hw.cpu_boost() not in (None, want) and hw.boost_writable():
            hw.set_cpu_boost(want)

    def remember_mode(self, key):
        """Modes.SetCurrent: the mode is kept per power source, like G-Helper's performance_<ac>.
        asusd is told too, so it already switches the base profile when the charger changes."""
        ac = bool(self.last_ac if self.last_ac is not None else hw.ac_online())
        src = "ac" if ac else "bat"
        if self.cfg.get(f"mode_{src}") == key:
            return
        self.cfg[f"mode_{src}"] = key
        self.save()
        base = modes.base_of(self.cfg, key)
        if base in hw.ASUSD_PROFILE:
            self.bg(hw.set_source_profile, base, ac)

    # keyboard_brightness / keyboard_brightness_ac in G-Helper: each power source keeps its own
    # backlight level. Fn+F2/F3 are handled by the firmware, so a change is seen by polling.
    def remember_kbd_level(self, ac, level):
        if (level is None or time.monotonic() < self.kbd_restoring
                or os.path.exists(hw.KBD_IDLE_FILE)):  # off because of the idle timeout
            return
        key = "kbd_ac" if ac else "kbd_bat"
        if self.cfg.get(key) != level:
            self.cfg[key] = level
            save_config(self.cfg)

    def restore_kbd_level(self, ac):
        level = self.cfg.get("kbd_ac" if ac else "kbd_bat")
        if level and level != self.kbd_level and not os.path.exists(hw.KBD_IDLE_FILE):
            self.kbd_restoring = time.monotonic() + 3  # until the new level reads back
            self.bg(hw.set_kbd_level, level)

    def on_power_source(self, ac):
        """Charger plugged or unplugged: the mode last used on this source comes back
        (custom modes too); with none remembered asusd's own choice stays."""
        log.info(f"power source -> {'AC' if ac else 'battery'}")
        if self.games and self.switch_for_game():  # a game runs: its mode for this source
            return
        self.game_set = None
        target = self.cfg.get("mode_ac" if ac else "mode_bat")
        self.mode_busy = True

        def job():
            current = modes.active(self.cfg)
            if target and modes.get(self.cfg, target):
                modes.activate(self.cfg, target)  # also puts its limits on this source
            elif target in hw.ASUSD_PROFILE:
                if current:
                    modes.deactivate(self.cfg)
                if hw.get_profile() != target:
                    hw.set_profile(target)
                modes.restore_pending(self.cfg)
            elif current:
                modes.reapply(self.cfg)  # the other source has its own limits in asusd
            else:
                modes.restore_pending(self.cfg)
            self.save()
            return True, ""
        self.bg(job, then=self.mode_done)

    def toast_mode(self, key):
        base = modes.base_of(self.cfg, key)
        icon = {"quiet": "battery-profile-powersave", "balanced": "battery-profile-balanced",
                "performance": "battery-profile-performance"}.get(base, "battery-profile-balanced")
        self.toast(icon, modes.name_of(self.cfg, key))

    def toast(self, icon, text):
        """Program.toast.RunToast: KDE's OSD, or a short notification elsewhere (GNOME has no OSD for apps)."""
        def fallback(ok):
            if ok or self.notifier.toast(icon, text, ""):  # the app name is already in its header
                return
            if self.tray:
                self.tray.showMessage(NAME, text, QtWidgets.QSystemTrayIcon.Information, 1500)
        self.bg(hw.osd, icon, text, then=fallback)

    def modes_changed(self):
        """Custom modes were added, renamed, removed or (de)activated."""
        if self.tray:
            self.tray.build_menu()
        if self.fans and self.fans.isVisible():
            self.fans.load()
        if self.extra is not None:
            self.extra.build_bindings()  # "<mode> Mode" key actions

    def on_fans(self):
        if self.fans is None:
            self.fans = FansWindow(self)
        self.fans.show()
        self.fans.raise_()
        self.fans.activateWindow()

    def on_gpu(self, mode):
        if self.cfg.pop("gpu_auto", None):
            save_config(self.cfg)
        if mode == self.gpu_current:
            if self.gpu_target:  # picking the current mode cancels a pending MUX change
                self.gpu_target = None
                self.cfg.pop("gpu_after_reboot", None)
                save_config(self.cfg)
                self.bg(hw.set_armoury, "gpu_mux_mode", 0 if mode == "AsusMuxDgpu" else 1,
                        then=lambda r: (self.report(r, tr("GPU mode")), self.refresh_slow()))
            else:
                self.refresh_slow()
            return
        if mode == "AsusMuxDgpu" or self.gpu_current == "AsusMuxDgpu":
            answer = QtWidgets.QMessageBox.question(
                self, tr("GPU Mode"),
                tr("Switching to or from Ultimate Mode requires restart.\n\nSwitch and reboot now?"))
            if answer != QtWidgets.QMessageBox.Yes:
                self.refresh_slow()
                return
            if self.gpu_current == "AsusMuxDgpu":
                self.cfg["gpu_after_reboot"] = mode  # Cardwire's mode, set after the reboot
                save_config(self.cfg)
            self.switch_gpu(mode, reboot=True)
        else:
            close = []
            if mode == "Integrated":
                close = self.close_gpu_apps()
                if close is None:
                    self.refresh_slow()
                    return
            self.switch_gpu(mode, close=close)

    def close_gpu_apps(self):
        """Programs using the NVIDIA GPU before Eco -> pids to close, or None to cancel.
        Cardwire only blocks new users: running ones keep the dGPU awake until they quit. The
        desktop (KWin) keeps it open too, but that does not wake it, so it is left alone."""
        apps, _desktop = hw.gpu_apps()
        if not apps:
            return []
        pids = [pid for pid, _ in apps]
        if self.cfg.get("kill_gpu_apps"):
            return pids
        names = sorted({name for _, name in apps})
        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Question, tr("Switch to Eco"),
                                    tr("These programs use the NVIDIA GPU:") + "\n\n  " + "\n  ".join(names), parent=self)
        box.setInformativeText(tr("They keep it powered on until they are closed. Close them now?"))
        close = box.addButton(tr("Close and switch"), QtWidgets.QMessageBox.AcceptRole)
        box.addButton(tr("Switch anyway"), QtWidgets.QMessageBox.DestructiveRole)
        cancel = box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(close)
        box.exec()
        if box.clickedButton() is cancel:
            return None
        return pids if box.clickedButton() is close else []

    @staticmethod
    def terminate(pids, wait=3.0):
        """SIGTERM, then give the programs a moment to let go of the GPU."""
        import signal
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        deadline = time.monotonic() + wait
        while pids and time.monotonic() < deadline:
            pids = [pid for pid in pids if os.path.exists(f"/proc/{pid}")]
            time.sleep(0.1)

    def switch_gpu(self, mode, reboot=False, close=()):
        log.info(f"GPU -> {mode}" + (f", closing {list(close)}" if close else ""))
        restart_offered = []  # the Cardwire restart is offered once per switch
        self.gpu_busy = True
        self.gpu_head.title.setText(tr("GPU Mode: Changing ..."))
        for b in self.gpu_buttons.values():
            b.setEnabled(False)

        def done(result):
            ok, out = result
            self.gpu_busy = False
            for b in self.gpu_buttons.values():
                b.setEnabled(True)
            if not ok and "discrete GPU" in (out or "") and not restart_offered:
                restart_offered.append(True)
                self.offer_cardwire_restart(mode, done)
                return
            if not ok:
                self.cfg.pop("gpu_after_reboot", None)
                save_config(self.cfg)
                self.report(result, tr("GPU mode"))
            elif "reboot" in (out or "").lower():
                self.gpu_target = mode
                if reboot:
                    subprocess.Popen(["systemctl", "reboot"])
            elif mode == "Hybrid":
                # a monitor waiting on the dGPU; told again even if told before (the switch may be
                # the user's way of asking for that picture)
                self.monitors_told.clear()
                self.sound_told.clear()
                QtCore.QTimer.singleShot(3000, self.check_monitors)
            self.refresh_slow()

        def job():
            if close:
                self.terminate(list(close))
            return hw.set_gpu_mode(mode)
        self.bg(job, then=done)

    def check_monitors(self):
        def found(result):
            card, dark = result
            if not dark:
                self.monitors_told.clear()  # unplugged or showing: tell again next time
                return
            new = [p for p in dark if p not in self.monitors_told]
            if not new:
                return
            self.monitors_told.update(dark)
            log.info(f"monitor without picture on the dGPU: {dark}")
            self.notify("video-display", tr(
                "The monitor on {port} shows nothing: that port is wired to the NVIDIA GPU, and KDE "
                "started while it was off (Eco). Turn it on without logging out?", port=", ".join(dark)),
                tr("Turn on"), lambda: self.bg(hw.announce_gpu, card,
                                               then=lambda r: self.report(r, tr("External monitor"))))

        def silent(result):
            card, mute = result
            if not mute:
                self.sound_told.clear()
                return
            if not [p for p in mute if p not in self.sound_told]:
                return
            self.sound_told.update(mute)
            log.info(f"no sound on the dGPU's monitor: {mute}, {card} is root-only")
            self.notify("audio-volume-muted", tr(
                "No sound on the monitor or TV on {port}: the NVIDIA GPU's sound came up without access "
                "for you, so it is missing from the sound outputs. Fix it?", port=", ".join(mute)),
                tr("Fix"), lambda: self.bg(hw.unmute_monitors, card,
                                           then=lambda r: self.report(r, tr("HDMI sound"))))
        self.bg(hw.dark_monitors, then=found, key="monitors")
        self.bg(hw.mute_monitors, then=silent, key="sound")

    def offer_cardwire_restart(self, mode, done):
        """Cardwire started before the NVIDIA driver (first boot after a kernel update) and does not
        know the dGPU is a discrete one: a restart of Cardwire, then the same switch again."""
        log.warning("Cardwire does not see the dGPU as discrete: offering a restart of cardwired")
        answer = QtWidgets.QMessageBox.question(
            self, NAME, tr("Cardwire started before the NVIDIA driver was ready (this happens on the first "
                           "start after a kernel update), so it does not know the NVIDIA GPU yet.\n\n"
                           "Restart Cardwire and switch again? It asks for your password."))
        if answer != QtWidgets.QMessageBox.Yes:
            done((False, tr("Cardwire does not see the NVIDIA GPU yet. Restarting the laptop also fixes it.")))
            return
        self.gpu_head.title.setText(tr("GPU Mode: Changing ..."))
        self.bg(hw.restart_cardwire_and_set, mode, then=done)

    def on_optimized(self):
        self.cfg["gpu_auto"] = True
        save_config(self.cfg)
        self.apply_gpu_auto()
        self.refresh_slow()

    def apply_gpu_auto(self):
        plugged = hw.ac_online() and not (self.cfg.get("optimized_usbc") and hw.usb_c_charger())
        want = "Hybrid" if plugged else "Integrated"
        if self.gpu_current not in ("Integrated", "Hybrid") or self.gpu_current == want or self.gpu_busy:
            return
        if want == "Integrated" and hw.dgpu_monitors()[1]:  # Eco would turn that monitor off
            log.info("Optimized: staying in Standard, a monitor is on the dGPU's port")
            return
        close = []
        if want == "Integrated" and self.cfg.get("kill_gpu_apps"):
            close = [pid for pid, _ in hw.gpu_apps()[0]]
        self.switch_gpu(want, close=close)

    def on_screen(self, key):
        self.cfg["screen"] = key
        save_config(self.cfg)
        if key == "auto":
            self.apply_screen_auto()
        elif key == "60":
            self.set_screen(60, False)
        else:
            self.set_screen(1000, not self.cfg.get("no_overdrive"))

    def apply_screen_auto(self):
        if hw.ac_online():
            self.set_screen(1000, not self.cfg.get("no_overdrive"))
        else:
            self.set_screen(60, False)

    def set_screen(self, hz, overdrive):
        """ScreenControl.SetScreen(rate, overdrive): max Hz goes with overdrive on, 60Hz with it off."""
        def job():
            result = hw.set_refresh(hz)
            if hw.get_overdrive() is not None and hw.get_overdrive() != overdrive:
                hw.set_overdrive(overdrive)
            return result
        self.bg(job, then=lambda r: (self.report(r, tr("Refresh rate")), self.refresh_slow()))

    def on_changes(self):
        if getattr(self, "changes", None) is None:
            self.changes = changes_window.ChangesWindow(self)
        self.changes.show()
        self.changes.raise_()
        self.changes.activateWindow()
        self.changes.load()

    def on_battery(self):
        if getattr(self, "battery", None) is None:
            self.battery = battery.BatteryWindow(self)
        self.battery.show()
        self.battery.raise_()
        self.battery.activateWindow()
        self.battery.load()

    def on_setup(self):
        if self.setup is None:
            self.setup = setup.SetupWindow(self)
        else:
            self.setup.refresh()
        self.setup.show()
        self.setup.raise_()
        self.setup.activateWindow()

    def first_run_setup(self):
        """Opens Setup at start when something required or recommended is missing (until 'Later'
        for this version)."""
        self.check_conflicts()
        self.check_charged_while_off()
        self.bg(hw.log_health)
        if hw.remove_kwin_limits() and self.tray:
            self.tray.showMessage(NAME, tr("Log out once to get the HDMI / USB-C video ports back: "
                                  "\"Eco without logout\" is not needed with Cardwire."),
                                  QtWidgets.QSystemTrayIcon.Information, 8000)
        if not self.cfg.get("kde_profiles_off"):  # once: the user may turn KDE's switch back on
            self.cfg["kde_profiles_off"] = True
            self.save()
            before = hw.stop_kde_power_profiles()
            if before:
                system_changes.record("kde_profiles", before=before)
            if before and self.tray:
                self.tray.showMessage(NAME, tr("KDE no longer changes the power mode on plug / unplug: "
                                      "the mode last used on each power source comes back instead. "
                                      "Undo in Extra → System changes."),
                                      QtWidgets.QSystemTrayIcon.Information, 8000)
        if self.cfg.get("setup_later") == VERSION:
            return
        self.bg(setup.checks, then=lambda rows: setup.needs_setup(rows) and self.on_setup())

    def check_conflicts(self):
        self.bg(conflicts.found, then=self.show_conflicts)

    def show_conflicts(self, found):
        if found:
            log.warning("conflicting tools: " + ", ".join(c.key for c in found))
            self.conflict_line.setText(tr("Also running: {names}. They change the same settings, so modes "
                                          "may not stick. Click to fix.", names=", ".join(c.title for c in found)))
        visible = bool(found)
        if visible != self.conflict_line.isVisible():
            self.conflict_line.setVisible(visible)
            QtCore.QTimer.singleShot(0, lambda: self.setFixedHeight(self.root_layout.sizeHint().height()))

    def after_setup(self):
        """Setup changed services, packages or the key rule: pick them up."""
        self.check_conflicts()
        self.refresh_slow()
        self.hotkeys.start()
        if self.extra is not None:
            self.extra.refresh()
        self.add_tray()

    def add_tray(self):
        """The tray icon, once the desktop has a tray: at start, or when GNOME's AppIndicator
        extension came on later (Setup turns it on during the session)."""
        if self.tray is None and QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_available = True
            self.tray = Tray(self)
            self.tray.show()
            self.apply_theme()

    def on_extra(self):
        if self.extra is None:
            self.extra = ExtraWindow(self)
        self.extra.show()
        self.extra.raise_()
        self.extra.activateWindow()

    def save(self):
        save_config(self.cfg)

    def apply_topmost(self):
        on = bool(self.cfg.get("topmost"))
        hw.kwin_place_window(on)                    # Wayland: only the compositor can do it
        if bool(self.windowFlags() & QtCore.Qt.WindowStaysOnTopHint) != on:
            visible = self.isVisible()
            self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, on)
            if visible:
                self.show()

    def aura_effect(self):
        """The effect in cfg, or Static where asusd does not allow it (an older choice, another laptop)."""
        effect = self.cfg.get("aura_effect", "static")
        if effect == "music":
            return effect if self.cfg.get("music_enabled") and hw.music_possible() else "static"
        allowed = hw.aura_modes()
        return effect if allowed is None or effect in allowed else "static"

    def apply_aura(self):
        """The keyboard effect, colour and speed in cfg (set in Extra or by the Fn+F4 action)."""
        save_config(self.cfg)
        if self.aura_effect() == "music":
            self.start_music()
            return
        self.stop_music()
        self.bg(hw.set_aura, self.aura_effect(), self.cfg.get("aura_colour", "ffffff"),
                self.cfg.get("aura_speed", "med"), then=lambda r: self.report(r, tr("Keyboard lighting")))

    def start_music(self):
        """The Music effect: the keyboard color follows the sound. It writes the color itself (not
        through asusd), with the permission asked for when Music is turned on in Extra. Runs only
        while Music is the chosen effect: nothing in the background otherwise."""
        if self.music and self.music.is_alive():
            self.music.set_idle(self.cfg.get("aura_colour", "ffffff"))  # a new color chosen
            return
        if not hw.music_writable():  # the permission went away (System changes → Undo)
            log.warning("Music effect: no permission for the keyboard color, Static instead")
            self.cfg["aura_effect"] = "static"
            if self.extra is not None:
                self.extra.show_aura()
            self.apply_aura()
            return
        log.info("keyboard lighting: Music")
        self.music = hw.MusicLights(self.cfg.get("aura_colour", "ffffff"), log=log.warning,
                                    step=int(self.cfg.get("music_step", hw.MUSIC_STEP)))  # hidden: for trying
        self.music.info = log.info
        self.music.start()

    def stop_music(self):
        if self.music:
            self.music.stop()
            self.music.join(2)
            self.music = None

    def on_limit(self, percent):
        percent = hw.firmware_limit(percent, self.limit_steps)
        if self.cfg.pop("full_once", None) is not None:
            self.cfg.pop("limit_before_full", None)
            save_config(self.cfg)
        self.bg(hw.set_charge_limit, percent, then=lambda r: self.report(r, tr("Charge limit")))

    def on_full_charge(self):
        """BatteryControl.ToggleBatteryLimitFull: charge to 100% once, then restore the limit."""
        if self.cfg.get("full_once"):
            self.end_full_charge()
            return
        self.cfg["limit_before_full"] = self.slider.value
        self.cfg["full_once"] = True
        save_config(self.cfg)
        self.slider.set_value(100)
        self.show_limit(100)
        self.full_button.set_activated(True)
        # asusd puts the limit back on unplug even without this app; at 100% the app does it
        self.bg(hw.one_shot_full_charge, then=lambda r: self.report(r, tr("Charge limit")))

    def end_full_charge(self):
        limit = hw.firmware_limit(self.cfg.pop("limit_before_full", 80), self.limit_steps)
        self.cfg.pop("full_once", None)
        save_config(self.cfg)
        self.full_button.set_activated(False)
        self.bg(hw.set_charge_limit, limit, then=lambda r: (self.report(r, tr("Charge limit")), self.refresh_fast()))

    def on_credits(self):
        QtWidgets.QMessageBox.about(
            self, tr("Credits"),
            f"<b>{NAME}</b> {VERSION}<br><br>"
            + tr("Inspired by {ghelper} by seerge (GPL-3.0): its interface, layout and behaviour are "
                 "seerge's work. An unofficial project, not affiliated with G-Helper.", ghelper="<a href='https://github.com/seerge/g-helper'>G-Helper</a>") + "<br>"
            + tr("Hardware control goes through {tools}.",
                 tools="<a href='https://asus-linux.org'>asusctl</a> &amp; "
                       "<a href='https://opengamingcollective.github.io/cardwire/'>Cardwire</a>") + "<br>"
            + tr("Icons: {icons}.", icons="<a href='https://lucide.dev'>Lucide</a> (ISC)") + "<br><br>"
            + (tr("Like it? {support}, and support {ghelper} too.",
                  support=f"<a href='{DONATE_URL}'>{tr('Support the project')}</a>",
                  ghelper="<a href='https://github.com/seerge/g-helper'>G-Helper</a>") + "<br><br>"
               if DONATE_URL else "")
            + tr("Not affiliated with or endorsed by ASUS. ASUS, ROG and TUF are trademarks of ASUSTeK Computer Inc."))

    def on_startup(self, on):
        if on:
            os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
            with open(AUTOSTART, "w") as f:
                f.write(hw.autostart_entry())
        elif os.path.exists(AUTOSTART):
            os.remove(AUTOSTART)

    def closeEvent(self, event):
        if self.tray_available:
            event.ignore()
            self.hide()
            if not self.cfg.get("tray_hint_shown") and self.tray:
                self.cfg["tray_hint_shown"] = True
                self.save()
                self.tray.showMessage(NAME, tr("Still running in the tray. Use Quit to close it."),
                                      QtGui.QIcon(w.APP_ICON), 5000)
        else:
            event.accept()
            quit_app()


class Tray(QtWidgets.QSystemTrayIcon):
    """Tray icon shows the GPU mode in its colour (tray-eco / standard / ultimate), like VisualiseIcon()."""

    ICONS = {"Integrated": "tray-eco.svg", "Hybrid": "tray-standard.svg", "AsusMuxDgpu": "tray-ultimate.svg"}

    def __init__(self, window):
        super().__init__(QtGui.QIcon(w.APP_ICON))
        self.window = window
        self.gpu = None
        self.menu = QtWidgets.QMenu()
        self.mode_actions = {}
        self.situation_actions = {}
        self.build_menu()
        self.menu.aboutToShow.connect(self.sync_menu)
        self.setContextMenu(self.menu)
        self.activated.connect(self.on_activated)
        self.set_gpu("Hybrid")
        self.apply_theme()

    def build_menu(self):
        """Settings.SetContextMenu: modes (custom ones too), GPU modes, Quit; the situations first
        while that layout is on."""
        window = self.window
        self.menu.clear()
        self.situation_actions = {}
        if window.cfg.get("situations_on"):
            title = self.menu.addAction(tr("Situation"))
            title.setEnabled(False)
            for key in situations.KEYS:
                act = self.menu.addAction(situations.title(key), lambda k=key: window.pick_situation(k))
                act.setCheckable(True)
                self.situation_actions[key] = act
            self.menu.addSeparator()
        title = self.menu.addAction(tr("Mode"))
        title.setEnabled(False)
        self.mode_actions = {}
        for key, label, _ in modes.all_modes(window.cfg):
            act = self.menu.addAction(label, lambda k=key: window.on_profile(k))
            act.setCheckable(True)
            self.mode_actions[key] = act
        self.menu.addSeparator()
        if window.gpu_buttons:
            title = self.menu.addAction(tr("GPU Mode"))
            title.setEnabled(False)
            for key, label, *_ in GPU_MODES:
                if key in window.gpu_buttons:
                    self.menu.addAction(label, lambda k=key: window.on_gpu(k))
            if window.optimized:
                self.menu.addAction(tr("Optimized"), window.on_optimized)
            self.menu.addSeparator()
        self.menu.addAction(tr("Battery"), window.on_battery)
        self.menu.addAction(tr("Quit"), quit_app)

    def apply_theme(self):
        self.menu.setStyleSheet(stylesheet())

    def sync_menu(self):
        current = self.window.cfg.get("active_mode") or hw.get_profile()
        for key, act in self.mode_actions.items():
            act.setChecked(key == current)
        situation = self.window.current_situation()
        for key, act in self.situation_actions.items():
            act.setChecked(key == situation)

    def set_gpu(self, mode):
        if mode == self.gpu or mode not in self.ICONS:
            return
        self.gpu = mode
        self.setIcon(QtGui.QIcon(os.path.join(w.ASSETS, self.ICONS[mode])))

    def show_window(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def on_activated(self, reason):
        if reason == QtWidgets.QSystemTrayIcon.Trigger:
            if self.window.isVisible():
                self.window.hide()
            else:
                self.show_window()


def main():
    logsetup("--debug" in sys.argv)
    from . import migrate
    for line in migrate.LAST:
        log.info(f"renamed from asus-tso-helper: {line}")
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP)
    app.setApplicationDisplayName(NAME)
    app.setDesktopFileName(APP)
    app.setQuitOnLastWindowClosed(False)
    hw.integrate_desktop()
    app.setStyle("Fusion")
    font = app.font()
    # the windows are measured in Noto Sans (KDE's font, as wide as G-Helper's Segoe UI); GNOME's
    # Adwaita Sans is wider and ran headers into their readouts
    if "Noto Sans" in QtGui.QFontDatabase.families():
        font.setFamily("Noto Sans")
    font.setPointSizeF(9)  # Segoe UI 9pt
    app.setFont(font)
    # one instance, like G-Helper: a second launch only brings the running window up
    server_name = f"{APP}-{os.getuid()}"
    probe = QtNetwork.QLocalSocket()
    probe.connectToServer(server_name)
    if probe.waitForConnected(500):
        if "--minimized" not in sys.argv:
            probe.write(b"show")
            probe.flush()
            probe.waitForBytesWritten(500)
        probe.disconnectFromServer()
        sys.exit(0)
    QtNetwork.QLocalServer.removeServer(server_name)  # stale socket from a crash
    server = QtNetwork.QLocalServer()
    server.listen(server_name)

    tray_ok = QtWidgets.QSystemTrayIcon.isSystemTrayAvailable()
    window = MainWindow(tray_ok)
    if os.path.exists(AUTOSTART):
        window.on_startup(True)  # points at this copy (e.g. an AppImage that was moved)

    def on_connection():
        conn = server.nextPendingConnection()
        if conn is None:
            return

        def on_data():
            if bytes(conn.readAll()).startswith(b"show"):
                window.show()
                window.raise_()
                window.activateWindow()
            conn.disconnectFromServer()
        conn.readyRead.connect(on_data)
    server.newConnection.connect(on_connection)
    window.add_tray()
    if "--minimized" not in sys.argv or not tray_ok:
        window.show()
    QtCore.QTimer.singleShot(1500, window.first_run_setup)
    sys.exit(app.exec())
