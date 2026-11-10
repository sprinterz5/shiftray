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


def hsv(h, v, s=1.0):
    i, f = int(h * 6) % 6, h * 6 % 1
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    r, g, b = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]
    return round(r * 255), round(g * 255), round(b * 255)


class Analyzer:
    """Samples in, one color a frame out: the beat and the character of the music.

    Beat: bass against its own average of the last second (mastered music is about equally loud
    all the time, so loudness alone looks static); a kick flashes, then fades.
    Character, over the last seconds:
      tone   - share of treble: deep, bassy music gets warm colors (red, orange, magenta),
               bright music cool ones (cyan, blue);
      energy - loudness against the loudest part so far: a calm verse glows softly, a chorus bright;
      tempo  - beats a second: fast music flashes short and steps through the colors, slow music
               breathes.
    The colors on the beats step around a palette centered on the tone. The volume knob does not
    matter: the sink's monitor carries the sound before it. In silence the color fades into the
    chosen one (idle), and back out when the sound returns."""
    LOW = 1 - math.exp(-2 * math.pi * 150 / RATE)  # low-pass at 150 Hz: kick and bass
    HIGH = 1 - math.exp(-2 * math.pi * 3000 / RATE)  # what is above 3 kHz: hats, cymbals, voice "s"

    def __init__(self):
        self.low = self.high = 0.0
        self.bass_avg = self.loud_peak = 0.0
        self.beat = self.level = 0.0
        self.tone = 0.3  # 0 deep ... 1 bright
        self.energy = 0.5
        self.tempo = 1.0  # beats a second
        self.phase = 0.0  # where on the palette
        self.fade = 1.0  # 1 = all idle color, 0 = all music
        self.quiet = SILENT_AFTER

    def frame(self, samples, seconds, idle=(255, 255, 255)):
        low, high, a_low, a_high = self.low, self.high, self.LOW, self.HIGH
        bass = treble = total = 0.0
        top = 0
        for x in samples:
            low += a_low * (x - low)
            high += a_high * (x - high)
            bass += low * low
            treble += (x - high) ** 2
            total += x * x
            top = max(top, abs(x))
        self.low, self.high = low, high
        n = max(1, len(samples))
        slow = seconds / 4  # character: about the last 4 seconds
        self.tempo += -self.tempo * slow
        if top < SILENT:
            self.quiet += seconds
            self.level *= 0.85
        else:
            self.quiet = 0.0
            bass, treble, loud = math.sqrt(bass / n), math.sqrt(treble / n), math.sqrt(total / n)
            self.bass_avg += 0.06 * (bass - self.bass_avg) if self.bass_avg else bass
            self.loud_peak = max(loud, self.loud_peak * 0.999)
            share = min(1.0, 3 * treble / (bass + treble + 1))  # treble is quieter: x3 spreads it
            self.tone += slow * (share - self.tone)
            self.energy += slow * (loud / (self.loud_peak + 1) - self.energy)
            beat = min(1.0, max(0.0, (bass / (self.bass_avg + 1) - 1.0) / 0.8))
            if beat > 0.6 and self.beat < 0.3:  # a new beat
                self.tempo += 1 / 4  # counted over the same 4 seconds -> beats a second
                self.phase += 0.25 + 0.1 * min(self.tempo, 4)
            self.beat = beat
            fast = min(1.0, self.tempo / 3)
            self.level *= 0.9 - 0.12 * fast  # slow music: long fades; fast: short flashes
            body = 0.08 + 0.45 * self.energy ** 1.5  # the glow between the beats
            self.level = max(self.level, beat * (0.5 + 0.5 * self.energy), body)
            self.phase += seconds * (0.05 + 0.1 * fast)  # breathing through the palette
        # palette: centered on red (deep) ... blue (bright), 0.15 to each side
        hue = ((0.62 * self.tone) + 0.15 * math.sin(2 * math.pi * self.phase) - 0.05) % 1
        sat = 0.75 + 0.25 * self.energy  # calm parts a little paler
        step = seconds / 1.5 if self.quiet >= SILENT_AFTER else -seconds * 4
        self.fade = min(1.0, max(0.0, self.fade + step))
        music = hsv(hue, min(1.0, self.level), sat)
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
