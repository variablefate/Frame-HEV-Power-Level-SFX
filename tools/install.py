#!/usr/bin/env python3
"""Install or update Frame HEV in the Steam user's home directory."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
UNIT = "frame-hev.service"
NATIVE_UNIT = "steamos-powerbuttond.service"
ICON_SIZES = (48, 64, 128, 256)


def gtk_python():
    """Return an interpreter with GTK3, or None when the menu cannot be shown."""
    candidates = ["/usr/bin/python3", sys.executable, shutil.which("python3") or ""]
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen or not Path(candidate).exists():
            continue
        seen.add(candidate)
        try:
            probe = subprocess.run([candidate, "-c", "import gi; gi.require_version('Gtk', '3.0')"],
                                   capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return candidate
    return None


def refresh_menu():
    for tool in ("kbuildsycoca6", "/usr/sbin/kbuildsycoca6", "/usr/bin/kbuildsycoca6",
                 "kbuildsycoca5", "/usr/sbin/kbuildsycoca5"):
        resolved = tool if Path(tool).exists() else shutil.which(tool)
        if resolved:
            command([resolved, "--noincremental"], check=False, capture=True)
            return


def menu_files(source, home, work, python):
    """Return the paths, contents, and modes the application menu needs.

    The panel launcher bakes in the interpreter that actually has GTK, because
    a menu-launched shell has a thinner PATH than a login shell.
    """
    app = home / ".local/share/frame-hev"
    icons = home / ".local/share/icons/hicolor"
    entry = (source / "desktop/frame-hev.desktop.in").read_text()
    entry = (entry.replace("@EXEC@", f'"{home / ".local/bin/frame-hev-panel"}"')
                  .replace("@UNINSTALL@", f'"{home / ".local/bin/frame-hev-uninstall"}"'))
    files = {
        home / ".local/bin/frame-hev-panel":
            (f'#!/bin/sh\n# Frame HEV control panel; interpreter chosen at install time.\n'
             f'exec "{python}" "{app / "frame_hev_panel.py"}" "$@"\n').encode(),
        home / ".local/bin/frame-hev-uninstall": (source / "tools/frame-hev-uninstall.sh").read_bytes(),
        home / ".local/share/applications/frame-hev.desktop": entry.encode(),
        icons / "scalable/apps/frame-hev.svg": (source / "assets/icon.svg").read_bytes(),
    }
    modes = {path: 0o644 for path in files}
    modes[home / ".local/bin/frame-hev-panel"] = 0o755
    modes[home / ".local/bin/frame-hev-uninstall"] = 0o755
    for size in ICON_SIZES:
        rendered = work / f"frame-hev-{size}.png"
        try:
            command(["rsvg-convert", "-w", str(size), "-h", str(size),
                     str(source / "assets/icon.svg"), "-o", str(rendered)], capture=True)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            continue
        if rendered.is_file():
            files[icons / f"{size}x{size}/apps/frame-hev.png"] = rendered.read_bytes()
            modes[icons / f"{size}x{size}/apps/frame-hev.png"] = 0o644
    return [(path, files[path], modes[path]) for path in files]


def command(args, *, check=True, capture=False):
    result = subprocess.run(args, text=True, capture_output=capture)
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise RuntimeError(f"{' '.join(map(str, args))} failed ({result.returncode})" +
                           (f"\n{detail}" if detail else ""))
    return result


def systemctl(*args, check=True):
    return command(["systemctl", "--user", *args], check=check, capture=True)


def preflight():
    if sys.platform != "linux":
        raise RuntimeError("Run install.sh from the project root to install over SSH from this computer.")
    if os.getuid() == 0:
        raise RuntimeError("Run as the normal Steam user without sudo.")
    for name in ("systemctl", "pw-play", "busctl"):
        if not shutil.which(name):
            raise RuntimeError(f"{name} is missing. This installer requires a Steam Frame running SteamOS.")
    runtime = Path(f"/run/user/{os.getuid()}")
    if not (runtime / "bus").is_socket():
        raise RuntimeError(f"The Steam user's session bus was not found at {runtime}/bus. "
                           "Run as the logged-in Steam user without sudo, with Steam running.")
    # A desktop/remote-desktop terminal can inherit a separate D-Bus session.
    # Use this user's systemd session for every installer subprocess, including
    # doctor and PipeWire, instead of preserving an unrelated inherited address.
    os.environ["XDG_RUNTIME_DIR"] = str(runtime)
    os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime}/bus"
    result = systemctl("show", NATIVE_UNIT, "--property=LoadState", "--property=ActiveState",
                       "--property=SubState", "--property=Result", check=False)
    state = dict(line.split("=", 1) for line in (result.stdout or "").splitlines() if "=" in line)
    if state.get("LoadState") == "not-found":
        raise RuntimeError(f"This SteamOS installation has no {NATIVE_UNIT} user service. "
                           "Its power-button setup may differ from the supported Frame firmware. "
                           "Please report your SteamOS version and this message.")
    if result.returncode:
        detail = (result.stderr or result.stdout or "No diagnostic output.").strip()
        raise RuntimeError("Could not query the Steam user's service manager. "
                           "This is a session/service error, not a headset sleep check.\n" + detail)
    if state.get("LoadState") != "loaded" or state.get("ActiveState") != "active":
        detail = ", ".join(f"{key}={state.get(key, 'unknown')}"
                           for key in ("LoadState", "ActiveState", "SubState", "Result"))
        raise RuntimeError(f"The native power-button service is not ready ({detail}). "
                           "HEV requires it to be active before taking over button input. "
                           "Keep Steam/SteamVR running on the awake headset and retry. "
                           "If it is already awake, collect the actual service error with:\n"
                           f"env XDG_RUNTIME_DIR={runtime} DBUS_SESSION_BUS_ADDRESS=unix:path={runtime}/bus "
                           f"systemctl --user --no-pager --full status {NATIVE_UNIT} steamvr.service")


def snapshot(path):
    if path.is_symlink():
        raise RuntimeError(f"Refusing to replace a symlink: {path}")
    return (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None


def atomic_write(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def restore(path, previous):
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        atomic_write(path, *previous)


def install(source=ROOT, home=None, *, offline=False, source_dir=None):
    """Stage and validate before touching a running installation; roll back failures."""
    home = Path.home() if home is None else Path(home)
    source = Path(source)
    preflight()
    required = ["frame_hev.py", "frame_hev_panel.py", "assets/icon.svg", "assets/manifest.json",
                "assets/README.md", "README.md", "VERSION", "LICENSE", "tools/fetch_sounds.py",
                "tools/control.sh", "tools/uninstall.sh", "tools/update.py",
                "tools/frame-hev-uninstall.sh",
                "desktop/frame-hev.desktop.in", "config/environment", "systemd/frame-hev.service"]
    for relative in required:
        if not (source / relative).is_file():
            raise RuntimeError(f"Incomplete download: {relative} is missing. Extract the whole release ZIP.")

    app = home / ".local/share/frame-hev"
    unit = home / ".config/systemd/user" / UNIT
    launcher = home / ".local/bin/frame-hev"
    settings = home / ".config/frame-hev/environment"
    if app.is_symlink():
        raise RuntimeError(f"Refusing to replace a symlink: {app}")
    old_unit, old_launcher = snapshot(unit), snapshot(launcher)
    old_active = systemctl("is-active", "--quiet", UNIT, check=False).returncode == 0
    old_enabled = systemctl("is-enabled", "--quiet", UNIT, check=False).returncode == 0
    app.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".frame-hev-install-", dir=app.parent))
    staged = work / "application"
    backup = work / "previous"
    preserve_work = False
    moved_new = made_settings = False
    python = gtk_python()
    menu, menu_before = [], {}
    try:
        staged.mkdir()
        for relative in ("frame_hev.py", "frame_hev_panel.py", "VERSION", "LICENSE", "README.md",
                         "assets/manifest.json", "assets/README.md"):
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, target)
        shutil.copyfile(source / "tools/uninstall.sh", staged / "uninstall.sh")
        shutil.copyfile(source / "tools/update.py", staged / "update.py")
        fetch = [sys.executable, str(source / "tools/fetch_sounds.py"),
                 "--quiet", "--output", str(staged / "assets"), "--cache-dir", str(app / "assets"),
                 "--cache-dir", str(source / "assets")]
        if offline:
            fetch.append("--offline")
        if source_dir:
            fetch.extend(["--source-dir", str(source_dir)])
        print("Preparing original HEV audio (verified local copies are reused)...", flush=True)
        command(fetch)
        print("Checking headset compatibility...", flush=True)
        command([sys.executable, str(staged / "frame_hev.py"), "doctor"], capture=True)

        try:
            # Leave the current service intact until staging and doctor succeed.
            systemctl("stop", UNIT, check=old_active)
            if app.exists():
                app.replace(backup)
            staged.replace(app)
            moved_new = True
            atomic_write(unit, (source / "systemd/frame-hev.service").read_bytes(), 0o644)
            atomic_write(launcher, (source / "tools/control.sh").read_bytes(), 0o755)
            if python:
                menu = menu_files(source, home, work, python)
                for path, data, mode in menu:
                    menu_before[path] = snapshot(path)
                    atomic_write(path, data, mode)
            if not settings.exists():
                atomic_write(settings, (source / "config/environment").read_bytes(), 0o600)
                made_settings = True
            systemctl("daemon-reload")
            systemctl("reset-failed", UNIT, check=False)
            systemctl("enable", "--now", UNIT)
            systemctl("is-active", "--quiet", UNIT)
        except Exception:
            print("Installation failed; restoring the previous installation...", file=sys.stderr)
            try:
                systemctl("stop", UNIT, check=False)
                if moved_new:
                    shutil.rmtree(app)
                if backup.exists():
                    backup.replace(app)
                restore(unit, old_unit)
                restore(launcher, old_launcher)
                for path, previous in menu_before.items():
                    restore(path, previous)
                if made_settings:
                    settings.unlink(missing_ok=True)
                systemctl("daemon-reload")
                systemctl("enable" if old_enabled else "disable", UNIT, check=old_enabled)
                if old_active:
                    systemctl("start", UNIT)
            except Exception as rollback_error:
                preserve_work = True
                print(f"Rollback needs attention: {rollback_error}\nBackup retained at {work}", file=sys.stderr)
            raise
    finally:
        if not preserve_work:
            shutil.rmtree(work)
    version = (app / "VERSION").read_text().strip()
    if menu:
        refresh_menu()
        updater = shutil.which("gtk-update-icon-cache") or next(
            (path for path in ("/usr/sbin/gtk-update-icon-cache", "/usr/bin/gtk-update-icon-cache")
             if Path(path).exists()), None)
        if updater:
            command([updater, "-f", "-t", str(home / ".local/share/icons/hicolor")],
                    check=False, capture=True)
    print(f"\nFrame HEV {version} is installed and enabled at startup.")
    print("Double-tap power while awake to hear the battery level.")
    print("Single press still sleeps; hold still opens the power menu.")
    if menu:
        print("Menu:      Applications → Frame HEV (right-click for Uninstall)")
    else:
        print("Menu:      skipped - the GTK3 Python bindings were not found")
    print("Try now:   ~/.local/bin/frame-hev announce")
    print("Settings: ~/.config/frame-hev/environment")
    print("Remove:   ~/.local/bin/frame-hev uninstall")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="only use verified local audio")
    parser.add_argument("--source-dir", type=Path, help="import loose WAV files from an installed Half-Life game")
    args = parser.parse_args()
    try:
        install(offline=args.offline, source_dir=args.source_dir)
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"\nFrame HEV could not be installed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
