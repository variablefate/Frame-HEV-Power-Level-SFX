#!/usr/bin/env python3
"""Frame HEV status panel: battery, automatic levels, service, uninstall.

Launched from the application menu. It reads the installed runtime next to this
file but never touches the power-button device, so it cannot disturb the
background service.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
except (ImportError, ValueError) as error:  # pragma: no cover - shown to the user
    print(f"Frame HEV panel needs the GTK3 Python bindings: {error}", file=sys.stderr)
    raise SystemExit(1)

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
import frame_hev  # noqa: E402  (the installed runtime sits next to this panel)

CONFIG = Path(os.environ.get("FRAME_HEV_CONFIG", Path.home() / ".config/frame-hev/environment"))
LAUNCHER = Path.home() / ".local/bin/frame-hev"
UNIT = "frame-hev.service"
STATES = {"active", "activating", "deactivating", "failed", "inactive", "maintenance",
          "reloading", "refreshing", "unknown"}


def read_settings():
    try:
        return CONFIG.read_text()
    except OSError:
        return ""


def configured(text, key, default):
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"\'')
    return default


def settings_problem(text):
    """Reject settings that would stop the service from starting."""
    return frame_hev.settings_problem(text)


def save_settings(text):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".environment-", dir=CONFIG.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w") as output:
            output.write(text if text.endswith("\n") else text + "\n")
        temporary.chmod(0o600)
        temporary.replace(CONFIG)
    finally:
        temporary.unlink(missing_ok=True)


def service_state():
    """Return systemd's own state word, or "unknown" plus why it could not ask."""
    frame_hev.session_environment()
    try:
        result = subprocess.run(["systemctl", "--user", "is-active", UNIT],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as error:
        return "unknown", str(error)
    state = (result.stdout or "").strip()
    if state in STATES:
        return state, None
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    return "unknown", detail[0] if detail else None


def version():
    try:
        return (APP / "VERSION").read_text().strip()
    except OSError:
        return "unknown"


def error_dialog(parent, message):
    dialog = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.ERROR,
                               buttons=Gtk.ButtonsType.CLOSE, text=message)
    dialog.run()
    dialog.destroy()


class Panel(Gtk.Window):
    def __init__(self):
        super().__init__(title="Frame HEV")
        self.set_icon_name("frame-hev")
        self.set_border_width(20)
        self.set_default_size(480, -1)
        self.set_resizable(False)
        self.refreshing = False

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add(box)

        heading = Gtk.Label(xalign=0)
        heading.set_markup('<span size="x-large" weight="bold">Frame HEV</span>')
        box.pack_start(heading, False, False, 0)

        self.percent = Gtk.Label(xalign=0)
        self.percent.set_markup('<span size="xx-large" weight="bold">—</span>')
        box.pack_start(self.percent, False, False, 0)

        self.detail = Gtk.Label(xalign=0)
        self.detail.set_line_wrap(True)
        box.pack_start(self.detail, False, False, 0)

        self.levels = Gtk.Label(xalign=0)
        self.levels.set_line_wrap(True)
        box.pack_start(self.levels, False, False, 0)

        self.service = Gtk.Label(xalign=0)
        box.pack_start(self.service, False, False, 0)

        buttons = Gtk.Grid(column_homogeneous=True, row_homogeneous=True, column_spacing=10, row_spacing=8)
        self.announce = Gtk.Button(label="Announce now")
        self.announce.connect("clicked", self.on_announce)
        restart = Gtk.Button(label="Restart service")
        restart.connect("clicked", self.on_restart)
        settings = Gtk.Button(label="Settings…")
        settings.connect("clicked", self.on_settings)
        uninstall = Gtk.Button(label="Uninstall…")
        uninstall.get_style_context().add_class("destructive-action")
        uninstall.connect("clicked", self.on_uninstall)
        buttons.attach(self.announce, 0, 0, 1, 1)
        buttons.attach(restart, 1, 0, 1, 1)
        buttons.attach(settings, 0, 1, 1, 1)
        buttons.attach(uninstall, 1, 1, 1, 1)
        box.pack_start(buttons, False, False, 0)

        self.note = Gtk.Label(xalign=0)
        self.note.set_line_wrap(True)
        box.pack_start(self.note, False, False, 0)

        footer = Gtk.Label(xalign=0)
        footer.set_line_wrap(True)
        shown = str(CONFIG).replace(str(Path.home()), "~", 1)
        footer.set_markup(f"<small>Version {version()} · settings: {shown}</small>")
        box.pack_start(footer, False, False, 0)

    def refresh(self):
        if self.refreshing:
            return
        self.refreshing = True

        def work():
            try:
                source, percent = frame_hev.read_battery()
                battery = {"percent": percent, "source": str(source),
                           "charging": frame_hev.read_charging()}
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                battery = {"error": str(error)}
            GLib.idle_add(self.show_state, battery, *service_state())

        threading.Thread(target=work, name="frame-hev-panel-refresh", daemon=True).start()

    def show_state(self, battery, state, detail):
        self.refreshing = False
        if "error" in battery:
            self.percent.set_markup('<span size="xx-large" weight="bold">—</span>')
            self.detail.set_text(f"Battery unavailable: {battery['error']}")
        else:
            self.percent.set_markup(f'<span size="xx-large" weight="bold">{battery["percent"]}%</span>')
            charging = battery["charging"]
            activity = "charging" if charging else ("discharging" if charging is False else "charge state unknown")
            source = "SteamVR level" if battery["source"] == "openvr:hmd" else "kernel reading"
            self.detail.set_text(f"{activity.capitalize()} · {source}")
        try:
            levels = frame_hev.parse_thresholds(configured(read_settings(), "FRAME_HEV_THRESHOLDS",
                                                           frame_hev.DEFAULT_THRESHOLDS))
            if levels:
                self.levels.set_text("Automatic announcements at "
                                     + ", ".join(f"{level}%" for level in levels) + " while discharging.")
            else:
                self.levels.set_text("Automatic announcements are off.")
        except ValueError as error:
            self.levels.set_text(f"FRAME_HEV_THRESHOLDS is invalid: {error}")
        if state == "unknown":
            self.service.set_text("Background service: state unknown — the user session bus is not reachable.")
            self.service.set_tooltip_text(detail)
        else:
            self.service.set_text(f"Background service: {state}.")
            self.service.set_tooltip_text(None)
        return False

    def on_announce(self, button):
        button.set_sensitive(False)
        self.note.set_text("Announcing the current level…")

        def work():
            problem = run([str(LAUNCHER), "announce"], 60)
            GLib.idle_add(self.after_command, "Announcement", problem)

        threading.Thread(target=work, name="frame-hev-panel-announce", daemon=True).start()

    def after_command(self, what, problem):
        self.announce.set_sensitive(True)
        self.note.set_text("" if problem is None else f"{what} failed: {problem}")
        self.refresh()
        return False

    def on_restart(self, _button):
        self.note.set_text("Restarting the background service…")

        def work():
            problem = run(["systemctl", "--user", "restart", UNIT], 30)
            GLib.idle_add(self.after_restart, problem)

        threading.Thread(target=work, name="frame-hev-panel-restart", daemon=True).start()

    def after_restart(self, problem):
        self.note.set_text("Service restarted." if problem is None else f"Could not restart the service: {problem}")
        self.refresh()
        return False

    def on_settings(self, _button):
        dialog = Gtk.Dialog(title="Frame HEV settings", transient_for=self, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.set_default_size(560, 360)
        text = Gtk.TextView()
        text.set_monospace(True)
        text.get_buffer().set_text(read_settings())
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.add(text)
        hint = Gtk.Label(xalign=0)
        hint.set_text("Changes apply after Restart service. The file is read at startup.")
        area = dialog.get_content_area()
        area.set_spacing(10)
        area.set_border_width(12)
        area.pack_start(scroller, True, True, 0)
        area.pack_start(hint, False, False, 0)
        dialog.show_all()
        while True:
            if dialog.run() != Gtk.ResponseType.OK:
                break
            start, end = text.get_buffer().get_bounds()
            content = text.get_buffer().get_text(start, end, False)
            problem = settings_problem(content)
            if problem:
                error_dialog(self, problem)
                continue
            try:
                save_settings(content)
            except OSError as error:
                error_dialog(self, f"Could not save the settings: {error}")
                continue
            self.note.set_text("Settings saved. Restart the service to apply them.")
            break
        dialog.destroy()
        self.refresh()

    def on_uninstall(self, _button):
        helper = Path.home() / ".local/bin/frame-hev-uninstall"
        if helper.exists():
            command = [str(helper)]
        else:
            # No helper to draw the preview: confirm here before deleting anything.
            dialog = Gtk.MessageDialog(transient_for=self, modal=True,
                                       message_type=Gtk.MessageType.QUESTION,
                                       buttons=Gtk.ButtonsType.OK_CANCEL,
                                       text="Remove Frame HEV, its settings, and cached audio?")
            response = dialog.run()
            dialog.destroy()
            if response != Gtk.ResponseType.OK:
                return
            command = ["bash", str(APP / "uninstall.sh"), "--purge"]
        try:
            subprocess.Popen(command)
        except OSError as error:
            error_dialog(self, f"Could not start the uninstaller: {error}")
            return
        self.note.set_text("Confirm the removal in the window that opened.")
        GLib.timeout_add(1000, self.check_removed)

    def check_removed(self):
        # Stay open when the removal was cancelled; quit once it really happened.
        if not (APP / "frame_hev.py").exists():
            Gtk.main_quit()
            return False
        return True


def run(command, timeout):
    """Run one command, returning None on success or a short failure message."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        return str(error)
    if result.returncode == 0:
        return None
    return (result.stderr or result.stdout).strip() or f"exit status {result.returncode}"


def main():
    # Repair the session bus first: a menu or terminal launch can arrive with a
    # runtime directory that has no bus, and every systemctl --user call would fail.
    frame_hev.session_environment()
    panel = Panel()
    panel.connect("destroy", Gtk.main_quit)
    panel.show_all()
    panel.refresh()

    def tick():
        panel.refresh()
        return True

    GLib.timeout_add_seconds(30, tick)
    Gtk.main()


if __name__ == "__main__":
    main()
