"""Main window, part: Key Bindings. Laptop keys (hotkeys.py), desktop shortcuts (shortcuts.py)
and what each bound key does.

A mixin: its methods run as MainWindow methods (self is the main window).
"""
import secrets
import shutil
import subprocess

from PySide6 import QtCore, QtGui, QtWidgets

from . import backend as hw
from . import hotkeys
from . import modes
from . import shortcuts
from .extra import AURA, aura_choices
from .i18n import tr


class KeysMixin:
    # Fn+F2/F3 change the backlight in firmware: this file changes when they are pressed
    KBD_HW_CHANGED = "/sys/class/leds/asus::kbd_backlight/brightness_hw_changed"

    # -- key bindings ---------------------------------------------------------------
    def on_hotkeys_state(self, state):
        self.hotkeys_state = state
        if self.extra:
            self.extra.build_bindings()

    def on_hotkey_seen(self, key):
        seen = self.cfg.setdefault("seen_keys", [])
        if key not in seen:
            seen.append(key)
            self.save()
            if self.extra:
                self.extra.build_bindings()

    def capture_laptop_key(self):
        """Add -> Laptop key: the next ASUS key pressed becomes a bindable row.
        Some Fn keys never reach Linux (Fn+F2/F3: the keyboard firmware changes the light
        itself), so the dialog watches for that and explains instead of waiting forever."""
        self.hotkeys.capturing = True
        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Information, tr("Add laptop key"),
                                    tr("Press the laptop key (Fn + ...) you want to bind."),
                                    QtWidgets.QMessageBox.Cancel, self.extra or self)
        box.rejected.connect(self.end_capture)
        self.capture_box = box
        self.capture_light = hw._read(self.KBD_HW_CHANGED)
        self.capture_ticks = 0
        self.capture_timer = QtCore.QTimer(self, interval=300, timeout=self.capture_tick)
        self.capture_timer.start()
        box.open()

    def capture_tick(self):
        if not self.capture_box:
            return
        self.capture_ticks += 1
        light = hw._read(self.KBD_HW_CHANGED)
        if light != self.capture_light:
            self.capture_fail(tr("This key is handled by the keyboard's own firmware (it changes the "
                                 "backlight by itself) and never reaches Linux, so it cannot be rebound."))
        elif self.capture_ticks >= 34:  # ~10 s
            self.capture_fail(tr("No laptop key arrived. That key is either handled by the firmware "
                                 "itself or sent as a normal key combination.") + "\n\n" +
                              tr("Use Add -> Keyboard shortcut for normal key combinations."))

    def capture_fail(self, text):
        box = self.capture_box
        self.end_capture()
        if box:
            box.done(0)
        QtWidgets.QMessageBox.information(self.extra or self, tr("Add laptop key"), text)

    def end_capture(self):
        self.hotkeys.capturing = False
        self.capture_box = None
        timer = getattr(self, "capture_timer", None)
        if timer:
            timer.stop()

    def on_key_captured(self, scan, label):
        box = self.capture_box
        self.end_capture()
        if box:
            box.done(0)
        builtin = hotkeys.SCAN_TO_KEY.get(scan)
        name = builtin or f"scan_{scan:02X}"
        if builtin:
            self.on_hotkey_seen(builtin)
        elif name not in self.cfg.get("custom_keys", {}):
            self.cfg.setdefault("custom_keys", {})[name] = {"scan": scan, "label": label}
            self.save()
            if self.extra:
                self.extra.build_bindings()
        else:
            label = self.cfg["custom_keys"][name]["label"]
        if builtin:
            label = hotkeys.KEYS[builtin][0]
        QtWidgets.QMessageBox.information(self.extra or self, tr("Add laptop key"),
                                          tr("{key} is in the list. Pick what it should do.", key=label))

    def shortcut_description(self, sid):
        n = list(self.cfg.get("shortcuts", {})).index(sid) + 1 if sid in self.cfg.get("shortcuts", {}) else 0
        return f"Shiftray custom key {n}"

    def add_shortcut(self):
        """Add -> Keyboard shortcut: pick keys here, the desktop confirms and owns them."""
        dlg = QtWidgets.QDialog(self.extra or self)
        dlg.setWindowTitle(tr("Add keyboard shortcut"))
        lay = QtWidgets.QVBoxLayout(dlg)
        lay.addWidget(QtWidgets.QLabel(tr("Press the key combination, then OK.\n"
                                       "KDE will ask once to confirm it.")))
        edit = QtWidgets.QKeySequenceEdit()
        edit.setMaximumSequenceLength(1)
        lay.addWidget(edit)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        if dlg.exec() != QtWidgets.QDialog.Accepted or edit.keySequence().isEmpty():
            return
        seq = edit.keySequence().toString(QtGui.QKeySequence.PortableText)
        sid = "user_" + secrets.token_hex(4)
        self.cfg.setdefault("shortcuts", {})[sid] = {"trigger": ""}
        self.save()
        self.portal_busy = True
        self.portal.bind([(sid, self.shortcut_description(sid), hotkeys.to_portal_trigger(seq))])

    def on_shortcuts_bound(self, triggers):
        self.portal_busy = False
        known = self.cfg.get("shortcuts", {})
        for sid, trigger in triggers.items():
            if sid in known:
                known[sid]["trigger"] = trigger
        self.save()
        if self.extra:
            self.extra.build_bindings()

    def on_shortcut(self, sid):
        action = self.hotkeys.binding(sid)
        if not action:
            return
        code = hotkeys.KEY_ACTIONS.get(action)
        if code is not None and self.hotkeys.send_key(code):
            return
        self.on_hotkey(action, sid)

    def remove_key(self, key):
        self.cfg.get("custom_keys", {}).pop(key, None)
        if self.cfg.get("shortcuts", {}).pop(key, None) is not None:
            hw.run("busctl", "--user", "call", "org.kde.kglobalaccel", "/kglobalaccel",
                   "org.kde.KGlobalAccel", "unregister", "ss", shortcuts.APP_ID, key, timeout=5)
        self.cfg.pop(f"key_{key}", None)
        self.cfg.pop(f"key_{key}_cmd", None)
        self.save()
        if self.extra:
            self.extra.build_bindings()

    def enable_hotkeys(self):
        self.bg(hotkeys.install_rule, then=lambda r: (self.report(r, tr("Enabling Key Bindings")),
                                                     QtCore.QTimer.singleShot(1500, self.hotkeys.start)))

    def on_hotkey(self, action, key):
        """InputDispatcher.KeyProcess for the actions that are not a plain key press."""
        def spawn(*cmd):
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                pass
        if action == "aura":
            keys = [k for k, _ in aura_choices()]
            current = self.cfg.get("aura_effect", "static")
            self.cfg["aura_effect"] = keys[(keys.index(current) + 1) % len(keys)] if current in keys else keys[0]
            self.apply_aura()
            self.toast("input-keyboard-brightness", dict(AURA)[self.cfg["aura_effect"]])
            if self.extra is not None:
                self.extra.show_aura()
        elif action == "performance":  # Modes.GetNext: Silent, Balanced, Turbo, then custom modes
            order = [m[0] for m in modes.all_modes(self.cfg)]
            current = self.cfg.get("active_mode") or hw.get_profile()
            nxt = order[(order.index(current) + 1) % len(order)] if current in order else "balanced"
            self.on_profile(nxt, notify=True)
        elif action.startswith("mode:"):
            if action[5:] in [m[0] for m in modes.all_modes(self.cfg)]:
                self.on_profile(action[5:], notify=True)
        elif action == "screen":
            if hw.is_gnome():  # Mutter's power save "off"; the first input turns it back on
                spawn("busctl", "--user", "set-property", "org.gnome.Mutter.DisplayConfig",
                      "/org/gnome/Mutter/DisplayConfig", "org.gnome.Mutter.DisplayConfig", "PowerSaveMode", "i", "3")
            else:
                spawn("kscreen-doctor", "--dpms", "off")
        elif action == "lock":
            spawn("loginctl", "lock-session")
        elif action in ("screenshot", "calculator"):  # the desktop's own app: KDE's, else GNOME's
            for cmd in {"screenshot": (["spectacle"], ["gnome-screenshot", "-i"]),
                        "calculator": (["kcalc"], ["gnome-calculator"])}[action]:
                if shutil.which(cmd[0]):
                    spawn(*cmd)
                    break
        elif action == "ghelper":
            if self.isVisible():
                self.hide()
            else:
                self.show()
                self.raise_()
                self.activateWindow()
        elif action == "mangohud":  # G-Helper's FPS overlay; MangoHud draws it inside the game
            if shutil.which("mangohudctl"):
                spawn("mangohudctl", "toggle", "no_display")
            else:
                self.toast("dialog-information", tr("MangoHud is not installed"))
        elif action == "custom":
            cmd = self.cfg.get(f"key_{key}_cmd", "").strip()
            if cmd:
                spawn("sh", "-c", cmd)
