"""Keyboard color that follows the sound the laptop plays (the "Music" effect).

The TUF keyboard is one zone: the whole keyboard pulses with the loudness, red for bass and
blue-violet for treble. Colors go straight to asus-wmi's kbd_rgb_mode with the "don't save" flag:
through asusd every change would also be written to its config file, 30 times a second. That file
is root-only, so Setup adds a udev rule letting the user's group write it (music_access_script).
The sound is what the default output plays (pw-record from the sink's monitor), never the mic."""
import array
import math
import os
import subprocess
import threading
import time

from .common import KBD_LED

KBD_RGB = os.path.join(KBD_LED, "kbd_rgb_mode")
MUSIC_RULE = "/etc/udev/rules.d/71-shiftray-kbd-color.rules"
RATE = 11025
FPS = 30
SILENT = 300  # peak (16-bit samples) below which the sound counts as silence
SILENT_AFTER = 2.0  # seconds of silence before the keyboard shows the chosen color again


def music_possible():
    return os.path.exists(KBD_RGB)


def music_writable():
    return os.access(KBD_RGB, os.W_OK)


def music_access_script():
    """Root lines: the logged-in user's group may write the keyboard color, now and after boot."""
    return (f"g=$(id -gn \"${{PKEXEC_UID:-0}}\") && "
            f"printf '%s\\n' '# Shiftray: the Music keyboard effect writes colors without asusd' "
            f"\"ACTION==\\\"add|change\\\", SUBSYSTEM==\\\"leds\\\", KERNEL==\\\"asus::kbd_backlight\\\", "
            f"RUN+=\\\"/bin/sh -c 'chgrp $g /sys%p/kbd_rgb_mode && chmod 0220 /sys%p/kbd_rgb_mode'\\\"\" "
            f"> {MUSIC_RULE} && chmod 644 {MUSIC_RULE} && udevadm control --reload && "
            f"udevadm trigger --action=change -s leds && udevadm settle || exit 1\n")


MUSIC_UNDO = (f"rm -f {MUSIC_RULE} && udevadm control --reload && chgrp root {KBD_RGB} && "
              f"chmod 0200 {KBD_RGB}")


def write_color(r, g, b):
    with open(KBD_RGB, "w") as f:
        f.write(f"0 0 {r} {g} {b} 0")  # 0: don't save; mode 0: static


def hsv(h, v):
    i, f = int(h * 6) % 6, h * 6 % 1
    p, q, t = 0.0, v * (1 - f), v * f
    r, g, b = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]
    return round(r * 255), round(g * 255), round(b * 255)


class Analyzer:
    """Samples in, one color a frame out: loudness -> brightness (fast up, slower down, against the
    loudest of the last seconds), the share of bass -> hue (0 red ... 0.75 violet)."""
    LOW = 1 - math.exp(-2 * math.pi * 200 / RATE)  # one-pole low-pass at 200 Hz: bass
    MID = 1 - math.exp(-2 * math.pi * 2000 / RATE)  # what is left above 2 kHz: treble

    def __init__(self):
        self.low = self.mid = 0.0
        self.peak = SILENT
        self.level = 0.0
        self.hue = 0.0
        self.quiet = 0.0

    def frame(self, samples, seconds):
        """-> (r, g, b), or None while it is quiet."""
        low, mid, a_low, a_mid = self.low, self.mid, self.LOW, self.MID
        bass = treble = total = 0.0
        top = 0
        for x in samples:
            low += a_low * (x - low)
            mid += a_mid * (x - mid)
            bass += low * low
            treble += (x - mid) ** 2
            total += x * x
            top = max(top, abs(x))
        self.low, self.mid = low, mid
        n = max(1, len(samples))
        if top < SILENT:
            self.quiet += seconds
            self.level *= 0.8
            return None if self.quiet >= SILENT_AFTER else hsv(self.hue, self.level)
        self.quiet = 0.0
        loud = math.sqrt(total / n)
        self.peak = max(loud, self.peak * 0.997, SILENT)
        self.level = max(min(1.0, loud / self.peak) ** 1.5, self.level * 0.82)
        share = math.sqrt(bass) / (math.sqrt(bass) + math.sqrt(treble) + 1e-9)
        self.hue += 0.25 * ((1 - share) * 0.75 - self.hue)
        return hsv(self.hue, 0.08 + 0.92 * self.level)


class MusicLights(threading.Thread):
    """Runs until stop(); on silence and at the end the keyboard shows idle_color ('rrggbb')."""

    def __init__(self, idle_color, log=None):
        super().__init__(daemon=True, name="music-lights")
        self.set_idle(idle_color)
        self.log = log
        self.stopping = threading.Event()
        self.proc = None

    def set_idle(self, color):
        self.idle = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))

    def stop(self):
        self.stopping.set()
        if self.proc:
            self.proc.terminate()

    def run(self):
        chunk = RATE // FPS * 2
        analyzer, shown, failures = Analyzer(), None, 0
        while not self.stopping.is_set():
            try:
                self.proc = subprocess.Popen(
                    ["pw-record", "-P", "{ stream.capture.sink = true media.name = \"Shiftray keyboard\" }",
                     "--rate", str(RATE), "--channels", "1", "--format", "s16", "-"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            except OSError as err:
                self._log(f"music lights: no pw-record ({err})")
                return
            while not self.stopping.is_set():
                data = self.proc.stdout.read(chunk)
                if not data:
                    break  # PipeWire restarted (wireplumber, a new output): start again
                samples = array.array("h", data[:len(data) // 2 * 2])
                color = analyzer.frame(samples, len(samples) / RATE) or self.idle
                if shown and max(abs(a - b) for a, b in zip(color, shown)) < 4:
                    continue
                try:
                    write_color(*color)
                    shown, failures = color, 0
                except OSError as err:  # asleep, or the permission went away
                    failures += 1
                    if failures == 30:
                        self._log(f"music lights: cannot set the keyboard color ({err})")
            self.proc.terminate()
            self.proc.wait()
            if not self.stopping.is_set():
                time.sleep(1)
        try:
            write_color(*self.idle)
        except OSError:
            pass

    def _log(self, text):
        if self.log:
            self.log(text)
