"""Parsers in backend/: fed the real output format of each tool, no hardware needed."""
import os
import subprocess
import time
import unittest
from unittest import mock

from shiftray import backend as hw
from shiftray.backend import battery, gpu


class Facade(unittest.TestCase):
    def test_every_hw_name_used_by_the_app_exists(self):
        """`hw.<name>` anywhere in the app must be re-exported by backend/__init__.py."""
        import glob
        import re
        root = os.path.join(os.path.dirname(__file__), "..", "shiftray")
        missing = set()
        for path in glob.glob(os.path.join(root, "*.py")):
            with open(path) as f:
                for name in re.findall(r"\bhw\.(\w+)", f.read()):
                    if not hasattr(hw, name):
                        missing.add(f"{os.path.basename(path)}: hw.{name}")
        self.assertEqual(missing, set())


class SysfsCache(unittest.TestCase):
    def setUp(self):
        from shiftray.backend import common
        self.common = common
        common._found.clear()

    def test_found_path_is_kept_until_it_disappears(self):
        find = mock.Mock(return_value="/sys/class/hwmon/hwmon3")
        with mock.patch.object(os.path, "exists", return_value=True) as exists:
            self.assertEqual(self.common._cached("k", find), "/sys/class/hwmon/hwmon3")
            self.common._cached("k", find)
            self.assertEqual(find.call_count, 1)
            exists.return_value = False  # gone (a driver reloaded): look again
            self.common._cached("k", find)
            self.assertEqual(find.call_count, 2)

    def test_nothing_found_is_looked_for_again_later(self):
        find = mock.Mock(return_value=None)
        self.assertIsNone(self.common._cached("k", find))
        self.common._cached("k", find)
        self.assertEqual(find.call_count, 1)
        with mock.patch.object(time, "monotonic", return_value=time.monotonic() + 61):
            self.common._cached("k", find)
        self.assertEqual(find.call_count, 2)


class Cardwire(unittest.TestCase):
    def test_dgpu_users_parses_lsof(self):
        # busctl prints a{sas}: count, then per node: path, count, names
        out = 'a{sas} 2 "/dev/nvidia0" 2 "steam" "kwin_wayland" "/dev/dri/card1" 1 "firefox"'
        with mock.patch.object(gpu, "cardwire_dgpu", return_value="/gpu"), \
                mock.patch.object(gpu, "run", return_value=(True, out)):
            self.assertEqual(gpu.dgpu_users(), ["firefox", "steam"])  # the desktop's own are left out

    def test_last_blocked(self):
        out = 'a((tu)usus) 2 1700000000 5 111 "chrome" 1 "" 1700000100 7 4242 "game.exe" 1 "steam_app_1"'
        with mock.patch.object(gpu, "run", return_value=(True, out)):
            self.assertEqual(gpu.cardwire_last_blocked(), (4242, "game.exe"))

    def test_last_blocked_empty(self):
        with mock.patch.object(gpu, "run", return_value=(True, "a((tu)usus) 0")):
            self.assertIsNone(gpu.cardwire_last_blocked())


class CardwireRetry(unittest.TestCase):
    def test_missed_dgpu_is_looked_for_again(self):
        calls = []
        answers = iter([(False, "Couldn't set mode to Integrated, Integrated and Smart modes require a offload "
                                 "discrete GPU"), (True, ""), (True, "")])
        with mock.patch.object(gpu, "run", side_effect=lambda *a, **k: calls.append(a) or next(answers)):
            self.assertEqual(gpu.set_cardwire_mode("Integrated"), (True, ""))
        self.assertTrue(any("RefreshGpu" in c for c in calls))


class CardwireLookup(unittest.TestCase):
    def setUp(self):
        gpu._cardwire_dgpu.clear()
        gpu._cardwire_missed[0] = None

    tearDown = setUp

    def test_no_dgpu_is_not_asked_every_refresh(self):
        """A laptop without a dGPU (or Cardwire that missed it): one look a minute, not every 2 s."""
        with mock.patch.object(gpu, "run", return_value=(False, "no such object")) as run:
            self.assertIsNone(gpu.cardwire_dgpu())
            self.assertIsNone(gpu.cardwire_dgpu())
            self.assertEqual(run.call_count, 1)
            with mock.patch.object(time, "monotonic", return_value=time.monotonic() + 61):
                gpu.cardwire_dgpu()
            self.assertEqual(run.call_count, 2)

    def test_found_dgpu_is_kept(self):
        answers = [(True, 'ssuubbbbssbs "AMD Radeon 660M" "0000:36:00.0" 128 1 true false false true "AMD" "amdgpu" false "none"'),
                   (True, 'ssuubbbbssbs "NVIDIA GeForce RTX 4060 Laptop GPU" "0000:01:00.0" 129 0 false true false true "Nvidia" "nvidia" true "0"')]
        with mock.patch.object(gpu, "run", side_effect=answers) as run:
            self.assertTrue(gpu.cardwire_dgpu().endswith("/Gpu/1"))
            self.assertTrue(gpu.cardwire_dgpu().endswith("/Gpu/1"))
            self.assertEqual(run.call_count, 2)


class Games(unittest.TestCase):
    def _spawn(self, env):
        p = subprocess.Popen(["sleep", "5"], env=dict(os.environ, **env))
        self.addCleanup(lambda: (p.kill(), p.wait()))
        time.sleep(0.1)
        return p.pid

    def test_steam_game(self):
        self.assertEqual(gpu.game_name(self._spawn({"SteamAppId": "1091500"})), "sleep")

    def test_wine_game(self):
        self.assertEqual(gpu.game_name(self._spawn({"WINEPREFIX": "/tmp/x"})), "sleep")

    def test_not_a_game(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("Steam", "STEAM", "WINE"))}
        p = subprocess.Popen(["sleep", "5"], env=env)
        self.addCleanup(lambda: (p.kill(), p.wait()))
        time.sleep(0.1)
        self.assertIsNone(gpu.game_name(p.pid))


class DgpuUsers(unittest.TestCase):
    def test_own_nvidia_smi_is_not_named(self):
        """The app's own temperature read must never be shown as what keeps the dGPU awake."""
        out = 'a{sas} 1 "/dev/nvidia0" 3 "nvidia-smi" "steam" "kwin_wayland"'
        with mock.patch.object(gpu, "cardwire_dgpu", return_value="/gpu/1"), \
                mock.patch.object(gpu, "run", return_value=(True, out)):
            self.assertEqual(gpu.dgpu_users(), ["steam"])


class Battery(unittest.TestCase):
    def test_history_parses_upower(self):
        out = "a(udu) 3 1700000300 13 2 1700000200 57.2 2 1700000100 0 0"
        with mock.patch.object(battery, "run", return_value=(True, out)):
            rows = battery.battery_history("charge", 3600)
        self.assertEqual(rows, [(1700000100, 0.0, 0), (1700000200, 57.2, 2), (1700000300, 13.0, 2)])

    def test_watts_per_mode(self):
        now = int(time.time())
        rate = [(now - 3000, 20.0, 2), (now - 2400, 20.0, 2), (now - 1800, 10.0, 2), (now - 1200, 10.0, 2)]
        with mock.patch.object(battery, "mode_log_lines", return_value=[(now - 4000, "performance"),
                                                                         (now - 2000, "quiet")]), \
                mock.patch.object(battery, "battery_history", return_value=rate):
            modes = battery.watts_per_mode(86400)
        self.assertAlmostEqual(modes["performance"][0], 20.0)
        self.assertAlmostEqual(modes["quiet"][0], 10.0)


class ChargeLimit6080(unittest.TestCase):
    """G-Helper's AppConfig.IsChargeLimit6080 and BatteryControl.SetBatteryChargeLimit."""

    def test_models(self):
        self.assertEqual(battery.charge_limit_steps("ASUS TUF Gaming A15 FA507NV"), battery.LIMIT_6080_STEPS)
        self.assertEqual(battery.charge_limit_steps("ROG Zephyrus G14 GA403UV"), battery.LIMIT_6080_STEPS)
        self.assertIsNone(battery.charge_limit_steps("ASUS TUF Gaming A16 FA607PV"))  # a TUF that takes any
        self.assertIsNone(battery.charge_limit_steps("ROG Strix G16 G614JV"))

    def test_what_a_limit_becomes(self):
        steps = battery.LIMIT_6080_STEPS
        self.assertEqual([battery.firmware_limit(v, steps) for v in (40, 60, 75, 80, 85, 90, 100)],
                         [60, 60, 75, 80, 80, 100, 100])
        self.assertEqual(battery.firmware_limit(85, None), 85)


class HealthLog(unittest.TestCase):
    def test_wear_from_first_and_last_week(self):
        day = 86400
        lines = [(i * day, 98.0 if i < 7 else 97.0) for i in range(0, 90)]
        with mock.patch.object(battery, "health_log_lines", return_value=lines):
            change, days = battery.health_change()
        self.assertAlmostEqual(change, -1.0)
        self.assertEqual(days, 89)

    def test_too_early(self):
        with mock.patch.object(battery, "health_log_lines", return_value=[(0, 98.0), (86400, 97.9)]):
            self.assertIsNone(battery.health_change())


class UpowerLimit(unittest.TestCase):
    """GNOME's "Preserve Battery Health" (UPower) next to asusd's limit."""

    def found(self, enabled, end, limit=80):
        from shiftray import conflicts

        def fake_run(*cmd, timeout=None):
            if cmd[:2] == ("upower", "-e"):
                return True, "/org/freedesktop/UPower/devices/line_power_ACAD\n/org/freedesktop/UPower/devices/battery_BAT1"
            return True, {"ChargeThresholdEnabled": f"b {enabled}", "ChargeEndThreshold": f"u {end}"}[cmd[-1]]
        with mock.patch.object(conflicts.hw, "run", side_effect=fake_run), \
                mock.patch.object(conflicts.hw, "get_charge_limit", return_value=limit):
            return conflicts._upower_limit()

    def test_only_a_different_limit_is_a_conflict(self):
        self.assertFalse(self.found("false", 60))
        self.assertFalse(self.found("true", 80))
        self.assertTrue(self.found("true", 60))


class PowerdevilDdcutil(unittest.TestCase):
    """PowerDevil crashes in libddcutil 2.x while Cardwire's Eco blocks the card (ddcutil#641)."""

    def run_with(self, result="success", version="2.2.1", lockdown="none [integrity] confidentiality",
                 cardwire=True, desktop="KDE"):
        from shiftray.backend import desktop as d

        def fake_run(*cmd, timeout=None):
            if cmd[:2] == ("systemctl", "--user"):
                return True, result
            if cmd[0] == "rpm":
                return True, version
            if cmd[:2] == ("systemctl", "is-active"):
                return cardwire, ""
            return False, ""
        with mock.patch.object(d, "run", side_effect=fake_run), \
                mock.patch.object(d.shutil, "which", side_effect=lambda n: n == "rpm"), \
                mock.patch.object(d, "_read_lockdown", return_value=lockdown), \
                mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": desktop}):
            return d.powerdevil_ddcutil_crash()

    def test_who_gets_the_fix(self):
        self.assertTrue(self.run_with())                                   # the author's laptop
        self.assertFalse(self.run_with(version="3.0.2"))                   # fixed ddcutil
        self.assertFalse(self.run_with(lockdown="[none] integrity"))       # no Secure Boot: card unbound
        self.assertFalse(self.run_with(cardwire=False))
        self.assertTrue(self.run_with(version="3.0.2", result="core-dump"))  # crashed anyway
        self.assertIsNone(self.run_with(desktop="GNOME"))


class DarkMonitors(unittest.TestCase):
    """A monitor on the dGPU's HDMI that KDE does not drive (it started in Eco)."""

    def test_monitor_kde_does_not_drive(self):
        with mock.patch.object(gpu, "dgpu_monitors", return_value=("/sys/class/drm/card0", ["HDMI-A-1"])), \
                mock.patch.object(gpu, "desktop_outputs", return_value={"eDP-1"}):
            self.assertEqual(gpu.dark_monitors(), ("/sys/class/drm/card0", ["HDMI-A-1"]))

    def test_monitor_showing_or_unknown(self):
        with mock.patch.object(gpu, "dgpu_monitors", return_value=("/sys/class/drm/card0", ["HDMI-A-1"])):
            with mock.patch.object(gpu, "desktop_outputs", return_value={"eDP-1", "HDMI-A-1"}):
                self.assertEqual(gpu.dark_monitors()[1], [])
            with mock.patch.object(gpu, "desktop_outputs", return_value=None):  # not KDE
                self.assertEqual(gpu.dark_monitors()[1], [])


class MuteMonitors(unittest.TestCase):
    """A TV on the dGPU's HDMI without sound: the NVIDIA sound card came up root-only."""

    def run_with(self, plugged=("HDMI-A-1",), exists=True, access=False):
        with mock.patch.object(gpu, "dgpu_monitors", return_value=("/sys/class/drm/card0", list(plugged))), \
                mock.patch.object(gpu, "dgpu_sound_card", return_value="/sys/class/sound/card0"), \
                mock.patch.object(gpu.os.path, "exists", return_value=exists), \
                mock.patch.object(gpu.os, "access", return_value=access) as acc:
            result = gpu.mute_monitors()
        if plugged and exists:
            self.assertEqual(acc.call_args[0][0], "/dev/snd/controlC0")
        return result[1]

    def test_root_only_card(self):
        self.assertEqual(self.run_with(), ["HDMI-A-1"])

    def test_nothing_to_fix(self):
        self.assertEqual(self.run_with(access=True), [])   # the user has it
        self.assertEqual(self.run_with(plugged=()), [])     # no monitor on the dGPU
        self.assertEqual(self.run_with(exists=False), [])   # no sound card node (Eco)


if __name__ == "__main__":
    unittest.main()
