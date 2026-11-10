"""System facts and settings: model, CPU, services, sensors, sleep mode."""
import os
import shutil

from .common import _hwmon, _read, _read_int, run


MEM_SLEEP = "/sys/power/mem_sleep"


def deep_sleep():
    """True / False: the laptop sleeps in S3 ("deep") / in s2idle; None when the firmware has
    only one of them (most laptops since 2020 have no S3, there is nothing to choose)."""
    try:
        with open(MEM_SLEEP) as f:
            modes = f.read().split()
    except OSError:
        return None
    if "deep" not in " ".join(modes) or "s2idle" not in " ".join(modes):
        return None
    return "[deep]" in modes


def set_deep_sleep(on):
    """Now and after every boot (mem_sleep_default on the kernel command line), one password."""
    mode = "deep" if on else "s2idle"
    if shutil.which("grubby"):
        boot = ("grubby --update-kernel=ALL --args=mem_sleep_default=deep" if on
                else "grubby --update-kernel=ALL --remove-args=mem_sleep_default")
    elif shutil.which("update-grub"):  # Debian, Ubuntu
        conf = "/etc/default/grub.d/shiftray-sleep.cfg"
        boot = (f"mkdir -p /etc/default/grub.d && echo 'GRUB_CMDLINE_LINUX_DEFAULT=\"$GRUB_CMDLINE_LINUX_DEFAULT "
                f"mem_sleep_default=deep\"' > {conf} && update-grub" if on
                else f"rm -f {conf} /etc/default/grub.d/asus-tso-helper-sleep.cfg && update-grub")
    else:
        boot = "true"  # unknown boot loader: for this boot only
    return run("pkexec", "sh", "-c", f"echo {mode} > {MEM_SLEEP} && {boot}", timeout=120)


LSM = "/sys/kernel/security/lsm"
BPF_LSM_GRUB = "/etc/default/grub.d/shiftray-bpf-lsm.cfg"


def bpf_lsm():
    """The kernel runs the BPF LSM, which Cardwire needs. Fedora and Arch turn it on; Ubuntu and
    Debian build it but leave it out of the kernel's list, so it takes an lsm= boot option."""
    return "bpf" in (_read(LSM) or "").split(",")


def bpf_lsm_fixable():
    """lsm= can be set here and will work: the kernel is built with BPF LSM (CONFIG_BPF_LSM=y, as
    Cardwire's guide checks), it boots through GRUB set up by grubby or update-grub (not
    systemd-boot or kernelstub, where the option would silently do nothing), and there is no lsm=
    of the user's own on the command line (Undo removes the option, which would lose theirs)."""
    if any(a.startswith("lsm=") for a in (_read("/proc/cmdline") or "").split()) or not _read(LSM):
        return False
    config = _read(f"/boot/config-{os.uname().release}")
    if config is not None and "CONFIG_BPF_LSM=y" not in config:
        return False
    if shutil.which("grubby"):
        return True
    return bool(shutil.which("update-grub")) and os.path.exists("/boot/grub/grub.cfg")


def bpf_lsm_script():
    """Root lines adding bpf to the security modules the kernel runs now (lsm= replaces the
    kernel's default list, so the current one is kept as it is). Takes effect at the next boot."""
    lsm = ",".join([m for m in (_read(LSM) or "").split(",") if m and m != "bpf"] + ["bpf"])
    if shutil.which("grubby"):
        return f"grubby --update-kernel=ALL --args=lsm={lsm}"
    return (f"mkdir -p /etc/default/grub.d && echo 'GRUB_CMDLINE_LINUX_DEFAULT=\"$GRUB_CMDLINE_LINUX_DEFAULT "
            f"lsm={lsm}\"' > {BPF_LSM_GRUB} && update-grub")


BPF_LSM_UNDO = ("if command -v grubby >/dev/null; then grubby --update-kernel=ALL --remove-args=lsm; "
                f"elif [ -e {BPF_LSM_GRUB} ]; then rm -f {BPF_LSM_GRUB} && update-grub; fi")


def cpu_name():
    for line in (_read("/proc/cpuinfo", "") or "").splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].replace("with Radeon Graphics", "").strip()
    return ""


def services_running():
    names = ("asusd", "cardwired")
    return [n for n in names if run("systemctl", "is-active", "--quiet", n, timeout=5)[0]]


def model_name():
    """'ASUS TUF Gaming A15 FA507NV_FA507NV' -> 'ASUS TUF Gaming A15 FA507NV'."""
    name = _read("/sys/class/dmi/id/product_name", "")
    return name.split("_")[0].strip()


def sensors(include_dgpu, fans=True):
    """Cheap sysfs reads, plus nvidia-smi when include_dgpu. Each nvidia-smi run keeps the dGPU
    awake for a few seconds more, so the caller asks rarely (ui.DGPU_TEMP_EVERY). Fan speeds are
    ACPI calls into the firmware: fans=False leaves them out ("fans": None)."""
    out = {"cpu": None, "dgpu": None, "fans": {} if fans else None}
    k10 = _hwmon("k10temp") or _hwmon("coretemp")
    if k10:
        t = _read_int(os.path.join(k10, "temp1_input"))
        out["cpu"] = t / 1000 if t is not None else None
    asus = _hwmon("asus") if fans else None
    if asus:
        for n in (1, 2, 3):
            rpm = _read_int(os.path.join(asus, f"fan{n}_input"))
            if rpm is not None:
                label = _read(os.path.join(asus, f"fan{n}_label"), f"fan{n}")
                out["fans"][label.replace("_fan", "")] = rpm
    if include_dgpu:
        ok, val = run("nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits",
                      timeout=3)
        if ok and val.strip().isdigit():
            out["dgpu"] = int(val.strip())
    return out


def dgpu_watts():
    """The NVIDIA GPU's power draw in W (nvidia-smi: only ask while it is awake anyway), or None."""
    ok, val = run("nvidia-smi", "--query-gpu=power.draw", "--format=csv,noheader,nounits", timeout=3)
    try:
        return float(val.strip()) if ok else None
    except ValueError:
        return None
