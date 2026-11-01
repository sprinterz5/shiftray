"""GPU modes (Cardwire, MUX through asusd), who uses the dGPU, games, dGPU sensors."""
import glob
import json
import os
import re
import shlex
import time

from ..log import log
from .common import ARMOURY, _bus_get, _cached, _read, run
from .profile import set_armoury


# Cardwire, supergfxd's successor from the same people, never unloads the NVIDIA driver: it
# blocks new opens of the dGPU with eBPF/LSM, so Eco and Standard switch at once, without a
# logout, and the dGPU powers down by itself once nothing uses it. Programs that already use it
# keep it until they quit. Ultimate is the firmware MUX (asus-armoury gpu_mux_mode, 0 = the dGPU
# drives the panel), set through asusd and applied on reboot.
CARDWIRE = "org.opengamingcollective.cardwire"
CARDWIRE_PATH = "/org/opengamingcollective/cardwire"
CARDWIRE_MODES = ["Integrated", "Hybrid", "Manual", "Smart"]  # D-Bus Mode values 0..3


def cardwire_mode():
    ok, out = run("busctl", "--system", "get-property", CARDWIRE, CARDWIRE_PATH, CARDWIRE + ".Mode",
                  "Mode", timeout=5)
    m = re.match(r"u (\d+)", out) if ok else None
    return CARDWIRE_MODES[int(m.group(1))] if m and int(m.group(1)) < len(CARDWIRE_MODES) else None


def set_cardwire_mode(mode):
    def attempt():
        return run("busctl", "--system", "set-property", CARDWIRE, CARDWIRE_PATH, CARDWIRE + ".Mode",
                   "Mode", "u", str(CARDWIRE_MODES.index(mode)), timeout=30)
    ok, out = attempt()
    if not ok and "discrete GPU" in out:
        # Cardwire looks for the GPUs once, at boot. When the NVIDIA driver comes up late (the first
        # boot of a new kernel, while akmods builds it) it misses the dGPU and refuses Eco: it is
        # asked to look again, then the switch is tried once more.
        log.info("Cardwire missed the dGPU at boot: asking it to look again")
        run("busctl", "--system", "call", CARDWIRE, CARDWIRE_PATH, CARDWIRE + ".Debug", "RefreshGpu", timeout=15)
        _cardwire_missed[0] = None  # it may know the dGPU now
        ok, out = attempt()
    return ok, out


def restart_cardwire_and_set(mode):
    """Cardwire tells the dGPU's type once, when it starts: started before the NVIDIA driver came up
    (the first boot of a new kernel, while akmods builds it), it never learns the dGPU is a
    discrete one and refuses Eco. A restart, now that the driver is there, fixes that (one password)."""
    ok, out = run("pkexec", "systemctl", "restart", "cardwired", timeout=120)
    if not ok:
        return ok, out
    _cardwire_missed[0] = None  # it may know the dGPU now
    for _ in range(30):  # until it answers on D-Bus again, with the GPUs looked at
        if cardwire_mode() is not None:
            break
        time.sleep(0.5)
    time.sleep(1)
    return set_cardwire_mode(mode)


def has_mux():
    return os.path.exists(os.path.join(ARMOURY, "gpu_mux_mode"))


def mux_dgpu():
    return _read(os.path.join(ARMOURY, "gpu_mux_mode", "current_value")) == "0"


def gpu_mode():
    """"Integrated" (Eco), "Hybrid" (Standard), "AsusMuxDgpu" (Ultimate), Cardwire's
    "Manual" / "Smart", or None when Cardwire is not running."""
    if has_mux() and mux_dgpu():
        return "AsusMuxDgpu"
    return cardwire_mode()


def _display_devices():
    """PCI display controllers (class 0x03xxxx) whose files can be read: Cardwire hides the dGPU's
    while it blocks it, so in Eco this misses it."""
    found = 0
    for path in glob.glob("/sys/bus/pci/devices/*/class"):
        if (_read(path) or "").startswith("0x03"):
            found += 1
    return found


def has_dgpu():
    """A second GPU to switch to (NVIDIA or AMD), or the firmware's dGPU switch or MUX. Strix Halo,
    Zenbooks and Vivobooks have the integrated GPU only, and then GPU modes have nothing to do."""
    return (_display_devices() >= 2 or has_mux() or os.path.exists(os.path.join(ARMOURY, "dgpu_disable"))
            or dgpu_power() is not None or cardwire_dgpu() is not None)


def dgpu_card():
    """/sys/class/drm/cardN of the discrete GPU (not the one the firmware booted on), or None.
    Cardwire hides its files in Eco, so then there is none."""
    for card in sorted(glob.glob("/sys/class/drm/card[0-9]*")):
        if "-" not in os.path.basename(card) and _read(os.path.join(card, "device", "boot_vga")) == "0":
            return card
    return None


def dgpu_monitors():
    """Monitors plugged into the dGPU's own ports (on many laptops HDMI is wired to the NVIDIA GPU),
    as connector names like "HDMI-A-1". The kernel's cached status: reading it wakes nothing."""
    card = dgpu_card()
    if not card:
        return card, []
    name = os.path.basename(card)
    found = [os.path.basename(c)[len(name) + 1:] for c in sorted(glob.glob(f"{card}-*"))
             if _read(os.path.join(c, "status")) == "connected"]
    return card, found


def desktop_outputs():
    """Outputs the desktop drives (KDE, kscreen-doctor), or None where it cannot be asked."""
    ok, out = run("kscreen-doctor", "-j", timeout=5)
    if not ok:
        return None
    try:
        return {o["name"] for o in json.loads(out).get("outputs", []) if o.get("connected")}
    except (ValueError, AttributeError, KeyError, TypeError):
        return None


def dark_monitors():
    """(card, ports): monitors on the dGPU the desktop does not drive. KWin opens the GPUs it can
    when it starts; one that came back later (KDE started in Eco, then Standard) stays unused until
    the kernel announces it again, and the monitor shows nothing."""
    card, plugged = dgpu_monitors()
    if not plugged:
        return card, []
    outputs = desktop_outputs()
    if outputs is None:
        return card, []
    return card, [p for p in plugged if p not in outputs]


def announce_gpu(card):
    """Announces the dGPU again (a udev "add" event), so KWin picks it up without a logout (one password)."""
    return run("pkexec", "udevadm", "trigger", "--action=add", card, timeout=120)


def dgpu_sound_card():
    """/sys/class/sound/cardN of the NVIDIA GPU's HDMI audio, or None."""
    for card in sorted(glob.glob("/sys/class/sound/card[0-9]*")):
        if _read(os.path.join(card, "device", "vendor")) == "0x10de":
            return card
    return None


def mute_monitors():
    """(sound card, ports): monitors on the dGPU whose sound the user cannot open. logind gives the
    seat's user access to a sound card when it appears; the NVIDIA one appears with the dGPU (Eco to
    Standard) and is sometimes left root-only, so PipeWire never sees it and the TV stays silent."""
    _, plugged = dgpu_monitors()
    card = dgpu_sound_card() if plugged else None
    if not card:
        return card, []
    control = "/dev/snd/controlC" + os.path.basename(card)[len("card"):]
    if not os.path.exists(control) or os.access(control, os.R_OK | os.W_OK):
        return card, []
    return card, plugged


def unmute_monitors(card):
    """Announces the NVIDIA sound card again, so logind gives the user access to it (one password),
    then WirePlumber looks at the sound cards again. Easy Effects loses its links when WirePlumber
    restarts and plays into nothing, so it is restarted too when it runs as a service."""
    ok, out = run("pkexec", "udevadm", "trigger", "--action=change", "--parent-match=" + card, timeout=120)
    if ok:
        run("udevadm", "settle", timeout=10)
        run("systemctl", "--user", "restart", "wireplumber", timeout=30)
        run("systemctl", "--user", "try-restart", "easyeffects.service", timeout=30)
    return ok, out


def gpu_supported():
    """GPU modes to offer; none without a dGPU."""
    if not has_dgpu():
        return []
    return ["Integrated", "Hybrid"] + (["AsusMuxDgpu"] if has_mux() else [])


def gpu_pending():
    """"Reboot required" while a MUX change waits for the reboot. asusd 6.5 keeps a MUX write
    queued in memory and writes it to the firmware at shutdown, so the firmware's own
    pending_reboot only shows writes made some other way."""
    if _read(os.path.join(ARMOURY, "pending_reboot")) == "1":
        return "Reboot required"
    if has_mux():
        queued = _bus_get("/xyz/ljones/asus_armoury/gpu_mux_mode", "xyz.ljones.AsusArmoury", "QueuedGpuValue")
        if isinstance(queued, int) and queued >= 0:
            return "Reboot required"
    return None


def set_gpu_mode(mode):
    """Eco / Standard at once through Cardwire. Ultimate and back: the MUX, after a reboot
    (Cardwire is left in Standard for it; the caller sets the wanted mode after the reboot)."""
    if mode == "AsusMuxDgpu":
        set_cardwire_mode("Hybrid")
        ok, out = set_armoury("gpu_mux_mode", 0)
        return ok, "Reboot required" if ok else out
    if has_mux() and mux_dgpu():
        ok, out = set_armoury("gpu_mux_mode", 1)
        return ok, "Reboot required" if ok else out
    return set_cardwire_mode(mode)


# Leftovers of the supergfxd version's "Eco without logout": KWin limited to the panel's GPU
# and Mesa's EGL. With Cardwire they only cost the HDMI port, so they go (next login).
KWIN_ENV = os.path.expanduser("~/.config/plasma-workspace/env/asus-tso-helper-igpu.sh")
KWIN_DROPIN = os.path.expanduser("~/.config/systemd/user/plasma-kwin_wayland.service.d/asus-tso-helper.conf")


def remove_kwin_limits():
    """-> True when something was removed (takes effect at the next login)."""
    removed = False
    for path in (KWIN_ENV, KWIN_DROPIN):
        try:
            os.remove(path)
            removed = True
        except OSError:
            pass
    if removed:
        try:
            os.rmdir(os.path.dirname(KWIN_DROPIN))
        except OSError:
            pass
        run("systemctl", "--user", "daemon-reload", timeout=10)
    return removed


def game_name(pid):
    """The game's name when this process is one (Steam, Proton, Wine, Lutris, Heroic all leave
    their marks in its environment), else None. Browsers and launchers probing the GPUs are not."""
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            env = dict(v.split(b"=", 1) for v in f.read().split(b"\0") if b"=" in v)
        with open(f"/proc/{pid}/comm") as f:
            comm = f.read().strip()
    except (OSError, ValueError):
        return None
    if comm.lower() in ("steam", "steamwebhelper", "pressure-vessel", "reaper", "wineserver", "lutris", "heroic",
                        "fossilize_repla"):  # Steam's shader pre-caching carries the game's id too
        return None
    app = env.get(b"SteamAppId") or env.get(b"SteamGameId") or b"0"
    if app not in (b"0", b"") or b"STEAM_COMPAT_DATA_PATH" in env or b"WINEPREFIX" in env \
            or comm.lower().endswith(".exe"):
        return comm
    return None


# Wine's own processes, running next to every Windows game
WINE_HELPERS = {"services.exe", "winedevice.exe", "plugplay.exe", "explorer.exe", "rpcss.exe", "svchost.exe",
                "tabtip.exe", "conhost.exe", "start.exe", "wineboot.exe", "rundll32.exe", "steam.exe",
                "winemenubuilder", "iexplore.exe"}
STEAM_ROOTS = ("~/.local/share/Steam", "~/.steam/steam", "~/.var/app/com.valvesoftware.Steam/.local/share/Steam")
_steam_titles = {}


def steam_title(app_id):
    """A Steam game's name from its appmanifest in any Steam library, or None."""
    if app_id in _steam_titles:
        return _steam_titles[app_id]
    title = None
    libraries = []
    for root in STEAM_ROOTS:
        try:
            with open(os.path.join(os.path.expanduser(root), "steamapps", "libraryfolders.vdf")) as f:
                libraries += re.findall(r'"path"\s+"([^"]+)"', f.read())
        except OSError:
            pass
        libraries.append(os.path.expanduser(root))
    for lib in dict.fromkeys(libraries):
        try:
            with open(os.path.join(lib, "steamapps", f"appmanifest_{app_id}.acf"), errors="replace") as f:
                found = re.search(r'"name"\s+"([^"]+)"', f.read())
        except OSError:
            continue
        if found:
            title = found.group(1)
            break
    _steam_titles[app_id] = title
    return title


def game_title(pid):
    """What to call the game this process belongs to: its Steam name, else the program's name;
    None for anything that is not a game, Wine's own helpers included."""
    name = game_name(pid)
    if not name or name.lower() in WINE_HELPERS:
        return None
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            env = dict(v.split(b"=", 1) for v in f.read().split(b"\0") if b"=" in v)
    except (OSError, ValueError):
        return name
    app = (env.get(b"SteamAppId") or env.get(b"SteamGameId") or b"").decode(errors="replace")
    return (steam_title(app) if app.isdigit() and app != "0" else None) or name


def scan_games(known):
    """{pid: game title or None} for the running processes; only pids not in known (the last scan)
    are looked at, so a scan every few seconds reads a handful of files."""
    now = {}
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            pid = int(entry)
            now[pid] = known[pid] if pid in known else game_title(pid)
    return now


def cardwire_last_blocked():
    """(pid, name) of the newest process Cardwire kept off the dGPU (Eco), or None."""
    ok, out = run("busctl", "--system", "call", CARDWIRE, CARDWIRE_PATH, CARDWIRE + ".Logger",
                  "ProcessBlocked", timeout=5)
    try:
        words = shlex.split(out) if ok else []
    except ValueError:
        return None
    if len(words) < 7:
        return None  # a((tu)usus) N  then per entry: secs nanos pid comm gpu app_id
    pid, comm = words[-4], words[-3]
    return (int(pid), comm) if pid.isdigit() else None


def dgpu_games():
    """Names of the games now running on the dGPU."""
    return sorted({n for n in (game_name(pid) for pid, _ in gpu_apps()[0]) if n})


DESKTOP_GPU_USERS = {"kwin_wayland", "kwin_x11", "Xwayland", "Xorg", "plasmashell", "gnome-shell",
                     "mutter", "sddm-greeter", "gdm-x-session"}


def gpu_apps():
    """Own processes holding the NVIDIA GPU open -> (apps that can be closed, desktop parts that can't)."""
    apps, desktop = [], []
    me = os.getuid()
    # /dev/nvidia* plus the NVIDIA card's DRM nodes (KWin opens those, not /dev/nvidia*)
    nodes = ["/dev/nvidia"]
    for vendor in glob.glob("/sys/bus/pci/devices/*/vendor"):
        if _read(vendor) == "0x10de":
            for node in glob.glob(os.path.join(os.path.dirname(vendor), "drm", "*")):
                nodes.append("/dev/dri/" + os.path.basename(node))
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            if os.stat(f"/proc/{pid}").st_uid != me:
                continue
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(f"/proc/{pid}/fd/{fd}")
                if target.startswith(nodes[0]) or target in nodes[1:]:
                    break
            except OSError:
                continue
        else:
            continue
        name = _read(f"/proc/{pid}/comm", "?")
        try:  # "RDD Process" -> "firefox": show the program, not the thread name
            exe = os.path.basename(os.readlink(f"/proc/{pid}/exe"))
        except OSError:
            exe = name
        if exe in OWN_GPU_PROBES:
            continue
        is_desktop = name in DESKTOP_GPU_USERS or exe in DESKTOP_GPU_USERS
        (desktop if is_desktop else apps).append((int(pid), exe if not is_desktop else name))
    return apps, desktop


def dgpu_name():
    """'NVIDIA GeForce RTX 4060 Laptop GPU' from lspci, without waking the dGPU like nvidia-smi."""
    ok, out = run("lspci", "-d", "10de:", "-mm", timeout=5)
    for line in out.splitlines() if ok else []:
        parts = re.findall(r'"([^"]*)"', line)
        if len(parts) >= 3 and ("VGA" in parts[0] or "3D" in parts[0]):
            m = re.search(r"\[(.+)\]", parts[2])
            name = (m.group(1) if m else parts[2]).replace(" Max-Q / Mobile", " Laptop GPU")
            return name if name.startswith("NVIDIA") else "NVIDIA " + name
    return ""


def dgpu_power(blocked=False):
    """NVIDIA GPU runtime PM state, without waking it: "active", "suspended", or None.
    While Cardwire blocks the dGPU (Eco) it also hides its sysfs files from programs, so then
    (blocked=True, or the files are gone) the state comes from Cardwire."""
    if not blocked:
        dev = _cached("nvidia", _nvidia_device)
        state = _read(os.path.join(dev, "power", "runtime_status")) if dev else None
        if state:
            return state
    path = cardwire_dgpu()
    if not path:
        return None
    ok, out = run("busctl", "--system", "call", CARDWIRE, path, CARDWIRE + ".Gpu", "PowerState", timeout=5)
    m = re.search(r'"(D\w+)', out) if ok else None
    return None if not m else "active" if m.group(1) == "D0" else "suspended"


def _nvidia_device():
    """The NVIDIA GPU's PCI device directory in sysfs."""
    for vendor in glob.glob("/sys/bus/pci/devices/*/vendor"):
        dev = os.path.dirname(vendor)
        if _read(vendor) == "0x10de" and (_read(os.path.join(dev, "class")) or "").startswith("0x03"):
            return dev
    return None


_cardwire_dgpu = []
_cardwire_missed = [None]  # when the last look found none: asked again a minute later, not every 2 s


def cardwire_dgpu():
    """Cardwire's D-Bus object of the offload dGPU (discrete, not the default GPU), or None."""
    missed = _cardwire_missed[0]
    if not _cardwire_dgpu and (missed is None or time.monotonic() - missed >= 60):
        for i in range(8):
            path = f"{CARDWIRE_PATH}/Gpu/{i}"
            ok, out = run("busctl", "--system", "call", CARDWIRE, path, CARDWIRE + ".Gpu", "GetDevice", timeout=5)
            if not ok:
                break
            try:
                fields = shlex.split(out)  # ssuubbbbssbs name pci render card default discrete ...
            except ValueError:
                continue
            if len(fields) > 6 and fields[5] == "false" and fields[6] == "true":
                _cardwire_dgpu.append(path)
                break
        if not _cardwire_dgpu:
            _cardwire_missed[0] = time.monotonic()
    return _cardwire_dgpu[0] if _cardwire_dgpu else None


# the app's own temperature and power reads: never the reason to name, or to close
OWN_GPU_PROBES = {"nvidia-smi"}
DGPU_SYSTEM_USERS = DESKTOP_GPU_USERS | OWN_GPU_PROBES | {"cardwired", "nvidia-powerd", "systemd", "systemd-logind"}


def dgpu_users():
    """Apps keeping the dGPU awake, from Cardwire's Gpu.Lsof (no sysfs or /proc scan of our own,
    so it works in Eco too and does not wake the GPU): sorted names without the desktop's own."""
    path = cardwire_dgpu()
    if not path:
        return []
    ok, out = run("busctl", "--system", "call", CARDWIRE, path, CARDWIRE + ".Gpu", "Lsof", timeout=5)
    try:
        words = shlex.split(out)[1:] if ok else []  # a{sas} N  node M name... node M name...
    except ValueError:
        return []
    names, i = set(), 1
    while i < len(words):
        count = int(words[i + 1]) if words[i + 1].isdigit() else 0
        names.update(words[i + 2:i + 2 + count])
        i += 2 + count
    return sorted(names - DGPU_SYSTEM_USERS)
