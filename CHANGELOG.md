# Changelog

## 0.13.1
- **HDMI on the NVIDIA GPU**: when KDE started in Eco and a monitor on the dGPU's port shows nothing
  in Standard, a notification offers **Turn on**: the GPU is announced to KDE again, no logout.
  Optimized no longer switches to Eco while a monitor is plugged in there.
- **iGPU memory sizes** in Extra: the choices said the driver's index (0, 1, 2…) as gigabytes; they
  now say the real size (Auto, 2 GB, 3 GB…).

## 0.13.0
- **Laptops without a discrete GPU** (Strix Halo, Zenbook, Vivobook): the GPU mode block, its tray
  entries and the GPU part of situations are gone, and Setup no longer asks for Cardwire. Before,
  four GPU buttons that did nothing sat in the middle of the window.
- **From the AppImage to the package**: the installed Fedora or Arch package points the menu entry
  and autostart at itself the first time it starts, so the old AppImage stops coming back.
- **Memory, honestly**: the README says what the app takes (about 60 MB as a package, 100 MB as
  an AppImage) and why.

## 0.12.0
- **Five more languages**: German, Spanish, Polish, Brazilian Portuguese and Simplified Chinese
  (Extra → Language, or the system's language). Machine-assisted; corrections from native speakers
  are welcome as pull requests.
- **Battery wear over months**: the app notes the battery's health once a day, and after two weeks
  the Battery window shows the change, e.g. "−1.0% in 3.0 months".

## 0.11.0
- **A game switches the mode** (Extra → Other, off by default): pick a mode for the charger and one
  for battery, or "Don't change". Steam, Proton, Wine, Lutris and Heroic games are recognised; the
  notification names Steam games by their name. When the last game quits, the mode you had comes
  back; picking a mode yourself during a game wins until it ends. The charger's and the battery's
  remembered modes are never overwritten by a game's.
- **Situations in the tray menu** while that layout is on: right-click, pick Gaming, no window.
- **The desktop's own charge limit** (UPower; GNOME's "Preserve Battery Health") set to another
  value than the app's shows up as a conflict in Setup and the main window: the two overwrote each
  other after a restart.
- **Charged past the limit while off**: ASUS firmware puts the limit back to 100% at power-on, and
  some models do not hold it while switched off either. The app notes the charge at shutdown and,
  if the battery came back above the limit, says so once (sleep keeps the limit).
- AppStream metainfo, so Discover and GNOME Software show the app with screenshots (packages).

## 0.10.0
- **GNOME support.** Vanilla GNOME shows no tray icons; Setup now installs GNOME's AppIndicator
  extension (the one Ubuntu ships) and turns it on, and the tray icon appears without restarting the
  app (a fresh install of the extension shows after the next login). Undo in Changes.
- On GNOME: mode toasts are short notifications that replace each other and stay out of the list;
  "Log out to finish" opens GNOME's own dialog; the keyboard light timeout works through GNOME's idle
  monitor (swayidle cannot work there); the screen-off key turns the panel off through Mutter.
- The window title no longer runs under GNOME's close button, and the app uses Noto Sans where it is
  installed (as on KDE): GNOME's wider font pushed the GPU header into its readout.
- "Keep app window always on top" is hidden where it cannot work (Wayland outside KDE).

## 0.9.3
- **The NVIDIA GPU no longer stays awake because of the app.** Each temperature read (nvidia-smi)
  wakes the dGPU for about 17 seconds, and with the window open it was read every 12, so in Standard
  the dGPU never slept again, on battery too (about 8 W). It is now read only while an app such as
  a game keeps the dGPU awake anyway. nvidia-smi is no longer named as what keeps it awake, nor
  offered for closing before Eco.
- **Notification buttons show up.** "Switch to Eco", "Switch to Turbo" and the others never
  appeared: the tips were sent by a busctl call that ended at once, and Plasma drops the buttons of
  a notification whose sender is gone. The app now sends them from its own D-Bus connection and
  handles the button itself (a click on the notification does the same); they also carry the app's
  icon now.

## 0.9.2
- **KDE power management in Eco**: PowerDevil crashed at start while Eco hid the NVIDIA GPU (Secure
  Boot and ddcutil 2.x, Fedora 44 among them: ddcutil#641), leaving KDE without the battery icon,
  dimming and the lid action. Setup finds it and starts PowerDevil without ddcutil (no password;
  undo in Changes); only external monitors' brightness over DDC is lost. Diagnostics reports the
  ddcutil version and PowerDevil's state.
- Setup no longer cuts long explanations to one line.

## 0.9.1
- Situations: the header shows the GPU's temperature and fan under the CPU's, as the GPU Mode block
  does in the classic layout.

## 0.9.0
- **Situations** (experimental, Extra → Other): four tiles, On the go, Desk, Gaming and Quiet, each
  setting the mode, GPU mode, screen rate and keyboard light at once, so a tile always gives the same
  result (Optimized and Auto follow the charger).
  They replace the Mode, GPU Mode and Screen blocks; under them the picked one's settings, which
  fold away once set. A situation with Ultimate asks about the reboot, like the Ultimate button.
- **Charge limit on TUF and some ROG models** (G-Helper's list): their firmware stops charging only
  at 60-80% or at 100%, and any other limit charged to full. The slider now stops only at 60, 80 and
  100 there, and a limit set before (85%, say) is shown as working like 100%.
- **Main window, tidier**: the battery title says the charge and the time left ("Battery: 22% · 1h 11m
  left", orange under 20%, red under 10%; "not charging, limit 80%" on the charger above the limit),
  the limit is the slider's caption, and "Full once" says
  what the 100% chip did. The keyboard effect and colour moved to Extra → Laptop Backlight (set once;
  Fn+F4 still walks through the effects). Version, Updates, Extra and Quit share one row; Credits
  moved to Extra. Even, smaller gaps between the blocks; the screen buttons fill the row. Fans read
  in RPM by default.
- **Extra**: Key Bindings, Laptop Backlight and Other open and close with a click on their title (the
  app remembers which); Setup, Diagnostics, Changes and Credits are one row of equal buttons at the
  bottom, under the asusd / Cardwire line. On KDE a window that gets shorter or taller (a section
  closed, the GPU note) keeps the edge on the panel's side in place, and Fans, Extra and the others
  line up with the main window on that side too (tops under a top panel, bottoms above a bottom one).

## 0.8.0
- **Battery tips** (notifications only; Extra → "Battery tips" turns them off):
  - after a sleep of an hour or more on battery, how much it cost; when it was a lot and the laptop
    sleeps in s2idle, a button to the deep sleep setting;
  - on battery, the apps that kept the NVIDIA GPU awake for 2 minutes, in one notification, with
    its draw and Eco;
  - at 20% in a heavier mode, Silent and Eco, once per discharge.
- The charge time counts to the charge limit ("35m to 85%"), not to 100%.
- Extra → Diagnostics → **Report a problem on GitHub**: copies the report and opens a new issue
  with a template that says where to paste it. Saving it to a file is still there.
- The GPU line says "GPU: off" in Eco and "GPU: sleeping" while the NVIDIA GPU sleeps, instead of
  an empty temperature.
- System changes: the list was white with white titles when Qt used its light palette; scroll bars
  follow the app's theme everywhere.
- The integrated GPU's temperature is no longer read: nothing showed it, and on some AMD laptops
  reading it can hang the kernel.

## 0.7.1
- Eco after a kernel update: Cardwire, started before the NVIDIA driver was built and loaded,
  does not know the NVIDIA GPU is a discrete one and refuses Eco. The app now explains that and
  offers to restart Cardwire (one password), then switches again.
- **AppImage 30 MB instead of 50** and about 350 ms faster to start: the Python standard library is
  shipped compiled (the AppImage is read-only, so Python compiled it on every start), and Qt and
  Python parts the app never loads are left out, ICU's 30 MB of locale tables among them.
- Less work in the tray: the 2-second refresh no longer scans every PCI device, hwmon sensor and
  power supply each time, and the Fans window stops polling while it is closed. In the tray, fan
  speeds and the keyboard light (ACPI calls into the firmware) are read every 10 s instead of 2:
  about 2.5x less CPU while the window is closed.
- A hung busctl or nvidia-smi can no longer pile up the refresh jobs in front of a click, and
  without a dGPU (or when Cardwire missed it) Cardwire is asked once a minute, not every 2 s.

## 0.7.0
- **New name: Shiftray** (was asus-tso-helper). Settings, the log and autostart move over by
  themselves on the first start; the AppImage, COPR and AUR packages update to the new name.
- Run from source with `./bin/shiftray`.

## 0.6.1
- **Conflicting tools**: TLP, auto-cpufreq, TuneD, supergfxd next to Cardwire, optimus-manager,
  system76-power, EnvyControl and g-helper-linux are found and shown in Setup and as a line in the
  main window. Set up stops the services (listed in Changes, Undo starts them again); for the
  others it says how to remove them.
- Setup: a line linking to Changes instead of a second confirmation box.

## 0.6.0
- **System changes** (Extra → Changes): every change the app made outside its own files, why, how
  risky, whether it is in place, and **Undo** for each or all (root ones with one password). Things
  others may rely on (Terra, supergfxd) show the command to undo them by hand.
- **Setup lists what it will change** before asking for the password; deep sleep warns first;
  KDE's power profile setting keeps its old values, so Undo puts them back.
- **Log** in `~/.local/state/shiftray/app.log`; start with `--debug` to log every command.
  Diagnostics includes it and the system changes.
- Code: the hardware layer is split by area (`backend/`), the main window into parts; unit tests
  run on every push; docs/ARCHITECTURE.md and docs/DEBUGGING.md for contributors.
- Described as what it is: asusctl and Cardwire in one app, with G-Helper's design.

## 0.5.0
- **Battery** window (the small chart button next to Battery Charge Limit, or the tray menu):
  charge and power draw over 24 hours or 7 days, battery health, and the average draw of each mode
  on battery with the hours a full charge lasts in it.
- **Game hints**: a notification with a button when a game starts in Eco (Cardwire kept it off the
  NVIDIA GPU: switch to Standard) or in Silent (switch to Turbo). Once per game; off in Extra.
- **After sleep** the mode of the current power source, screen rate, GPU auto mode, keyboard
  level and lighting are put back: the charger may have changed while asleep.
- **Deep sleep (S3)** option in Extra on laptops whose firmware offers it, instead of s2idle that
  can drain the battery overnight.
- Install Updates shows a progress bar under each update and the package being fetched; it no
  longer hangs on packages with two architectures, and reopening the window keeps a running install.
- The tray tooltip always shows the dGPU: off (Eco), sleeping, or awake and which apps keep it so.
- "Keep app window always on top" works on Wayland (through KWin).
- Closing the main window closes Fans, Extra, Updates and Battery too; opening it brings them back.

## 0.4.0
- **AppImage**: one file for any distribution with glibc 2.34+ (2021 on), with Python, Qt and the
  key libraries inside. It adds itself to the app menu on first start.
- **Update in one click**: when a new release is out, the red version label (or the app row in
  Updates) downloads it, checks GitHub's SHA-256 and restarts into it.
- Ultimate (MUX) tested both ways; a MUX change asusd keeps queued until shutdown shows as pending.
- **Russian** translation, picked from the system language or in Extra → Language. Other languages
  are one JSON file each in `shiftray/translations/`.
- Refresh rate also on **GNOME** (Mutter's display API), **Sway / Hyprland** (wlr-randr) and X11
  (xrandr), not only KDE.
- **RPM** (COPR) and **AUR** packages on the system's Python and Qt.
- KDE no longer switches the power profile on plug / unplug: it did so before the app and could
  leave Silent on battery instead of the remembered mode.
- The tray tooltip names the apps keeping the dGPU awake ("dGPU awake: firefox").
- **CPU Boost** on or off per mode (Fans → Advanced), a **MangoHud** key action, and a clamshell
  mode checkbox in Extra (KDE).
- Light / dark theme follows the desktop while the app runs, in the AppImage too; Extra can fix it
  to dark or light, and take the desktop's accent colour instead of G-Helper's blue.
- Fans, Extra, Updates and Setup open beside the main window, left or right where there is room (KDE).

## 0.3.0
- GPU modes on **Cardwire** instead of supergfxd: Eco and Standard switch at once, without a
  logout, and HDMI keeps working. Ultimate switches the firmware MUX through asusd.
- Setup installs Cardwire from Terra and retires supergfxd.
- Power Mode (CPU EPP) per mode in Fans → Advanced, battery time left / to full, keyboard brightness
  per power source, a key action per mode, stop nvidia-powerd on battery, diagnostics report.
- The main window opens at the tray icon (KDE).

## 0.2.1
- First-run Setup: Terra repository, power-profiles-daemon, key rule, swayidle in one password.
- Updates installs only the red rows.

## 0.2.0
- First version: G-Helper's main window, Fans + Power, Extra, Updates on asusctl / supergfxctl.
