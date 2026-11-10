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
SILENT_AFTER = 1.0  # seconds of silence before the keyboard fades into the chosen color


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
    """Samples in, one color a frame out. Mastered music is about equally loud all the time, so the
    brightness follows the beat instead: bass against its own average of the last second (a kick
    drum flashes, then fades). The hue drifts slowly and jumps a step on a strong beat. In silence
    the color fades into the chosen one (idle), and back out when the sound returns."""
    LOW = 1 - math.exp(-2 * math.pi * 150 / RATE)  # one-pole low-pass at 150 Hz: kick and bass
    DRIFT = 1 / 20  # hue turns per second

    def __init__(self):
        self.low = 0.0
        self.bass_avg = self.loud_peak = 0.0
        self.beat = self.level = 0.0
        self.hue = 0.0
        self.fade = 1.0  # 1 = all idle color, 0 = all music
        self.quiet = SILENT_AFTER

    def frame(self, samples, seconds, idle=(255, 255, 255)):
        low, a_low = self.low, self.LOW
        bass = total = 0.0
        top = 0
        for x in samples:
            low += a_low * (x - low)
            bass += low * low
            total += x * x
            top = max(top, abs(x))
        self.low = low
        n = max(1, len(samples))
        self.level *= 0.85
        if top < SILENT:
            self.quiet += seconds
        else:
            self.quiet = 0.0
            bass, loud = math.sqrt(bass / n), math.sqrt(total / n)
            self.bass_avg += 0.06 * (bass - self.bass_avg) if self.bass_avg else bass
            self.loud_peak = max(loud, self.loud_peak * 0.995)
            beat = min(1.0, max(0.0, (bass / (self.bass_avg + 1) - 1.0) / 0.8))
            if beat > 0.6 and self.beat < 0.3:
                self.hue += 0.11  # a new beat: the next color
            self.beat = beat
            body = 0.3 * loud / (self.loud_peak + 1)  # a glow between the beats, so it never goes dark
            self.level = max(self.level, beat, body)
        self.hue = (self.hue + self.DRIFT * seconds) % 1
        step = seconds / 1.5 if self.quiet >= SILENT_AFTER else -seconds * 4
        self.fade = min(1.0, max(0.0, self.fade + step))
        music = hsv(self.hue, 0.12 + 0.88 * self.level)
        return tuple(round(m + (i - m) * self.fade) for m, i in zip(music, idle))


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
                color = analyzer.frame(samples, len(samples) / RATE, self.idle)
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
