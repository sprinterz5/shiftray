Name:           shiftray
Version:        0.13.1
Release:        1%{?dist}
Summary:        asusctl and Cardwire in one small app: modes, GPU, fans and battery for ASUS laptops

# the app is GPL-3.0; its Lucide icons are ISC
License:        GPL-3.0-only AND ISC
URL:            https://github.com/sprinterz5/shiftray
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  make
BuildRequires:  python3-devel
BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib
BuildRequires:  systemd-rpm-macros

Requires:       python3
Requires:       python3-pyside6
Requires:       python3-evdev
Requires:       python3-dbus
Requires:       hicolor-icon-theme
# asusctl and cardwire come from the Terra repository, which the app's Setup adds;
# weak dependencies keep the package installable without it
Recommends:     asusctl
Recommends:     cardwire
Recommends:     swayidle
Recommends:     libkscreen
# the app was called asus-tso-helper up to 0.6.1: this package replaces it on upgrade
Obsoletes:      asus-tso-helper < 0.7.0
Provides:       asus-tso-helper = %{version}-%{release}

%description
A desktop app for asusctl (asusd) and Cardwire, with windows following G-Helper by seerge:
performance and custom modes, GPU modes (Eco, Standard, Ultimate, Optimized),
fan curves and power limits, keyboard lighting, battery charge limit, key
bindings and BIOS / driver updates. Hardware control goes through asusd
(asusctl) and Cardwire. Unofficial: not affiliated with the G-Helper project
or with ASUS.

%prep
%autosetup

%build
# nothing to build: Python

%install
%make_install PREFIX=%{_prefix} UDEVDIR=%{_udevrulesdir}
%py_byte_compile %{python3} %{buildroot}%{_datadir}/%{name}

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/%{name}.desktop
appstream-util validate-relax --nonet %{buildroot}%{_metainfodir}/*.metainfo.xml

%files
%license LICENSE shiftray/assets/icons/LICENSE
%doc README.md CHANGELOG.md
%{_bindir}/%{name}
%{_datadir}/%{name}/
%{_datadir}/applications/%{name}.desktop
%{_metainfodir}/io.github.sprinterz5.shiftray.metainfo.xml
%{_datadir}/icons/hicolor/256x256/apps/%{name}.png
%{_datadir}/icons/hicolor/scalable/apps/%{name}.svg
%{_udevrulesdir}/70-%{name}.rules

%changelog
* Tue Oct 27 2026 sprinterz5 <63397324+sprinterz5@users.noreply.github.com> - 0.13.1-1
- HDMI on the dGPU turns on without a logout; iGPU memory sizes are right

* Thu Oct 22 2026 sprinterz5 <63397324+sprinterz5@users.noreply.github.com> - 0.13.0-1
- No GPU modes on laptops without a dGPU; the package takes over from the AppImage

* Wed Oct 21 2026 sprinterz5 <63397324+sprinterz5@users.noreply.github.com> - 0.12.0-1
- Five more languages; battery wear over months

* Wed Oct 21 2026 sprinterz5 <63397324+sprinterz5@users.noreply.github.com> - 0.11.0-1
- A game switches the mode; situations in the tray menu; UPower charge limit conflict; AppStream metainfo

* Wed Oct 21 2026 sprinterz5 <63397324+sprinterz5@users.noreply.github.com> - 0.10.0-1
- GNOME support: tray icon through the AppIndicator extension, toasts, logout, idle timeout

* Tue Oct 20 2026 sprinterz5 <63397324+sprinterz5@users.noreply.github.com> - 0.9.3-1
- The NVIDIA GPU no longer stays awake because of the app; notification buttons show up

* Tue Oct 20 2026 sprinterz5 <63397324+sprinterz5@users.noreply.github.com> - 0.9.2-1
- Setup fixes KDE power management in Eco (PowerDevil crash in ddcutil 2.x)

* Tue Oct 20 2026 sprinterz5 <63397324+sprinterz5@users.noreply.github.com> - 0.9.1-1
- Situations: GPU temperature and fan in the header

* Tue Oct 20 2026 sprinterz5 <63397324+sprinterz5@users.noreply.github.com> - 0.9.0-1
- Charge limit that holds on TUF and some ROG models, tidier main window, Situations (experimental)

* Tue Oct 20 2026 sprinterz5 <63397324+sprinterz5@users.noreply.github.com> - 0.8.0-1
- Battery tips, time to the charge limit, Report a problem on GitHub

* Sun Oct 18 2026 sprinterz5 <63397324+sprinterz5@users.noreply.github.com> - 0.7.1-1
- AppImage 30 MB, faster start, less CPU in the tray; Cardwire restart after a kernel update

* Sun Oct 18 2026 sprinterz5 <63397324+sprinterz5@users.noreply.github.com> - 0.7.0-1
- Renamed from asus-tso-helper to Shiftray

* Sun Oct 18 2026 sprinterz5 <63397324+sprinterz5@users.noreply.github.com> - 0.6.1-1
- Conflicting tools found and stopped with undo; simpler Setup

* Sat Oct 17 2026 sprinterz5 <63397324+sprinterz5@users.noreply.github.com> - 0.6.0-1
- System changes with undo, log and --debug, code split, tests

* Sat Oct 17 2026 sprinterz5 <63397324+sprinterz5@users.noreply.github.com> - 0.5.0-1
- Battery window, game hints, restore after sleep, deep sleep option, update progress

* Fri Oct 16 2026 sprinterz5 <63397324+sprinterz5@users.noreply.github.com> - 0.4.0-1
- AppImage and one-click update; GPU modes on Cardwire; see CHANGELOG.md
