"""Keyboard color that follows the sound the laptop plays (the "Music" effect).

The TUF keyboard is one zone: the whole keyboard flashes on the beat (Analyzer has the details).
Colors go straight to asus-wmi's kbd_rgb_mode with the "don't save" flag: through asusd every change
would also be written to its config file, many times a second. That file is root-only, so turning
Music on adds a udev rule letting the user's group write it (music_access_script).
The sound is what the default output plays (pw-record from the sink's monitor), never the mic.

Nothing runs when there is nothing to show: with the backlight off, the lid closed or the screen
locked it pauses, and after a while of silence it stops listening until something plays again."""
import array
import math
import os
import select
import subprocess
import threading
import time

from .common import KBD_LED

KBD_RGB = os.path.join(KBD_LED, "kbd_rgb_mode")
MUSIC_RULE = "/etc/udev/rules.d/71-shiftray-kbd-color.rules"
RATE = 8000  # enough for bass and for treble up to 4 kHz; fewer samples to go through
FPS = 25  # each color is an ACPI call (~1.6 ms): the main cost, so no more than the eye needs
SILENT = 300  # peak (16-bit samples) below which the sound counts as silence
SILENT_AFTER = 1.0  # seconds of silence before the keyboard fades into the chosen color
SLEEP_AFTER = 10.0  # seconds of silence before it stops listening
CHECK_EVERY = 2.0  # seconds between the checks for a paused state and for a new sound
PROBE_EVERY = 20.0  # while asleep: listen this often for a moment (a stream that was silent, now not)
LATENCY = "20ms"  # asks PipeWire for small packets; pw-record's default allows 100 ms lumps
STEP = 12  # smallest change of a color channel worth a firmware call
LID = "/proc/acpi/button/lid"


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


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def pause_reason():
    """Why there is nothing to show now, or None: backlight off (Fn or the idle timeout), lid
    closed, screen locked. All cheap: two sysfs / procfs reads and one D-Bus property."""
    if _read(os.path.join(KBD_LED, "brightness")) == "0":
        return "backlight off"
    try:
        lids = os.listdir(LID)
    except OSError:
        lids = []
    if any("closed" in (_read(os.path.join(LID, n, "state")) or "") for n in lids):
        return "lid closed"
    try:
        out = subprocess.run(["busctl", "--system", "get-property", "org.freedesktop.login1",
                              "/org/freedesktop/login1/session/auto", "org.freedesktop.login1.Session",
                              "LockedHint"], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    return "screen locked" if out.strip() == "b true" else None


def playing_streams():
    """Ids of the streams that are playing (not paused), or None without pactl. A paused player
    corks its stream; play uncorks it, which is what wakes a sleeping Music effect up."""
    try:
        out = subprocess.run(["pactl", "list", "sink-inputs"], capture_output=True, text=True, timeout=2,
                             env=dict(os.environ, LC_ALL="C")).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    ids, current = set(), None
    for line in out.splitlines():
        if line.startswith("Sink Input #"):
            current = line.split("#", 1)[1].strip()
        elif line.strip() == "Corked: no" and current:
            ids.add(current)
    return ids


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
        self.shown = 0.0  # the brightness shown: jumps up with level, falls behind it smoothly
        self.hue = None
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
        k = seconds * FPS  # this many frames' worth: the rates below are per 1/FPS s, packets vary
        ease = lambda a: 1 - (1 - a) ** k  # an easing step a per frame, over k frames
        slow = seconds / 4  # character: about the last 4 seconds
        self.tempo += -self.tempo * slow
        if top < SILENT:
            self.quiet += seconds
            self.level *= 0.85 ** k
        else:
            self.quiet = 0.0
            bass, treble, loud = math.sqrt(bass / n), math.sqrt(treble / n), math.sqrt(total / n)
            self.bass_avg += ease(0.06) * (bass - self.bass_avg) if self.bass_avg else bass
            self.loud_peak = max(loud, self.loud_peak * 0.999 ** k)
            share = min(1.0, 3 * treble / (bass + treble + 1))  # treble is quieter: x3 spreads it
            self.tone += slow * (share - self.tone)
            self.energy += slow * (loud / (self.loud_peak + 1) - self.energy)
            beat = min(1.0, max(0.0, (bass / (self.bass_avg + 1) - 1.0) / 0.5))
            if beat > 0.5 and self.beat < 0.25:  # a new beat: a step across the palette
                self.tempo += 1 / 4  # counted over the same 4 seconds -> beats a second
                self.phase += 0.35
            self.beat = beat
            fast = min(1.0, self.tempo / 3)
            self.level *= (0.9 - 0.08 * fast) ** k  # slow music: long fades; fast: short flashes
            body = 0.04 + 0.16 * self.energy ** 1.5  # a dim glow between the beats: they must stand out
            self.level = max(self.level, min(1.0, 1.5 * beat), body)
            self.phase += seconds * (0.03 + 0.05 * fast)  # drifting through the palette
        # palette: centered on red (deep) ... blue (bright), 0.15 to each side
        target = ((0.62 * self.tone) + 0.2 * math.sin(2 * math.pi * (self.phase + 0.25)) - 0.05) % 1
        if self.hue is None:
            self.hue = target
        self.hue = (self.hue + ease(0.3) * ((target - self.hue + 0.5) % 1 - 0.5)) % 1  # glides, the short way round
        # up at once (a flash late by even 0.1 s looks behind the beat), down smoothly
        self.shown = self.level if self.level > self.shown else self.shown + ease(0.35) * (self.level - self.shown)
        hue = self.hue
        sat = 0.75 + 0.25 * self.energy  # calm parts a little paler
        step = seconds / 1.5 if self.quiet >= SILENT_AFTER else -seconds * 4
        self.fade = min(1.0, max(0.0, self.fade + step))
        music = hsv(hue, min(1.0, self.shown), sat)
        return tuple(round(m + (i - m) * self.fade) for m, i in zip(music, idle))


class MusicLights(threading.Thread):
    """Runs until stop(); in silence, paused and at the end the keyboard shows idle_color ('rrggbb').
    step: the smallest channel change written (STEP); bigger means fewer firmware calls, in steps."""

    def __init__(self, idle_color, log=None, step=STEP):
        super().__init__(daemon=True, name="music-lights")
        self.set_idle(idle_color)
        self.log = log
        self.step = step
        self.stopping = threading.Event()
        self.proc = None
        self.fd = None
        self.shown = None
        self.last = 0.0
        self.failures = 0

    def set_idle(self, color):
        self.idle = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))

    def stop(self):
        self.stopping.set()
        if self.proc:
            self.proc.terminate()

    # -- the keyboard ---------------------------------------------------------------
    def show(self, color, force=False):
        """A flash (brighter) goes out at once; fading and gliding at most FPS times a second,
        which is where the firmware calls (the cost) are."""
        if not force and self.shown:
            change = max(abs(a - b) for a, b in zip(color, self.shown))
            if change < self.step:
                return
            flash = sum(color) > sum(self.shown) + 3 * self.step
            if not flash and time.monotonic() - self.last < 1 / FPS:
                return
        self.last = time.monotonic()
        try:
            if self.fd is None:
                self.fd = os.open(KBD_RGB, os.O_WRONLY)  # kept open: one call per color, not three
            os.write(self.fd, f"0 0 {color[0]} {color[1]} {color[2]} 0".encode())
            self.shown, self.failures = color, 0
        except OSError as err:  # asleep, or the permission went away
            self._close()
            self.failures += 1
            if self.failures == 30:
                self._log(f"music lights: cannot set the keyboard color ({err})")

    def _close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    # -- the sound ------------------------------------------------------------------
    def _listen(self):
        return subprocess.Popen(
            ["pw-record", "-P", "{ stream.capture.sink = true media.name = \"Shiftray keyboard\" }",
             "--rate", str(RATE), "--channels", "1", "--format", "s16",
             "--latency", LATENCY, "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def _end(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait()
            self.proc = None

    def _probe(self):
        """Listens for half a second: True when something is heard."""
        try:
            self.proc = self._listen()
        except OSError:
            return False
        data = self.proc.stdout.read(RATE)  # 0.5 s of 16-bit samples
        self._end()
        samples = array.array("h", data[:len(data) // 2 * 2])
        return bool(samples) and max(map(abs, samples)) >= SILENT

    def _wait(self, until):
        """Sleeps in CHECK_EVERY steps until until() is true or stop()."""
        while not self.stopping.wait(CHECK_EVERY):
            if until():
                return

    def _wait_for_sound(self):
        """Asleep after a silence: wakes on a stream that starts playing, a short listen every
        PROBE_EVERY seconds (a stream that stayed open through the silence), or a pause."""
        before = playing_streams()
        last_probe = time.monotonic()

        def woke():
            nonlocal before, last_probe
            if pause_reason():
                return True  # the pause loop takes over
            now = playing_streams()
            if now is not None and before is not None:
                if now - before:
                    return True
                before &= now  # a stream paused now counts as new when it plays again
            if time.monotonic() - last_probe >= PROBE_EVERY:
                last_probe = time.monotonic()
                return self._probe()
            return False
        self._wait(woke)

    # -- the loop ---------------------------------------------------------------------
    def run(self):
        chunk = RATE // FPS * 2
        analyzer = Analyzer()
        while not self.stopping.is_set():
            why = pause_reason()
            if why:
                self._log(f"music lights: paused ({why})", info=True)
                if why != "backlight off":
                    self.show(self.idle, force=True)  # not frozen on a flash when it is seen again
                self._wait(lambda: pause_reason() is None)
                continue
            try:
                self.proc = self._listen()
            except OSError as err:
                self._log(f"music lights: no pw-record ({err})")
                return
            asleep, checked, data, buf = False, time.monotonic(), b"x", b""
            pipe = self.proc.stdout.fileno()
            os.set_blocking(pipe, False)
            while not self.stopping.is_set():
                if not select.select([pipe], [], [], 1.0)[0]:
                    continue
                data = os.read(pipe, 1 << 16)  # all that is there, not one frame: never behind
                if not data:
                    break  # PipeWire restarted (wireplumber, a new output): start again
                buf += data
                if len(buf) > 3 * chunk:  # fell behind (a busy moment): the newest sound only
                    buf = buf[-chunk:]
                if len(buf) < chunk // 4:
                    continue  # a sliver: wait for a real packet (PipeWire sends ~20 ms ones)
                # each packet as it comes, not whole 1/FPS frames: a beat shows within one packet
                samples = array.array("h", buf[:len(buf) // 2 * 2])
                buf = buf[len(buf) // 2 * 2:]
                self.show(analyzer.frame(samples, len(samples) / RATE, self.idle))
                if analyzer.quiet >= SLEEP_AFTER:
                    asleep = True
                    break
                if time.monotonic() - checked >= CHECK_EVERY:
                    checked = time.monotonic()
                    if pause_reason():
                        break
            self._end()
            if self.stopping.is_set():
                break
            if asleep:
                self.show(self.idle, force=True)
                self._log("music lights: silence, not listening until something plays", info=True)
                self._wait_for_sound()
                analyzer.quiet = SILENT_AFTER  # heard again: a new silence must last SLEEP_AFTER once more
            elif not data:
                time.sleep(1)
        self.show(self.idle, force=True)
        self._close()

    def _log(self, text, info=False):
        if self.log and not info:
            self.log(text)
        elif self.info:
            self.info(text)

    info = None  # set to log.info for the pauses (warnings go to log)
