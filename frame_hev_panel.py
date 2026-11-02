#!/usr/bin/env python3
"""Frame HEV status panel: battery, automatic levels, service, updates, removal.

Launched from the application menu. It reads the installed runtime next to this
file but never touches the power-button device, so it cannot disturb the
background service.
"""
from __future__ import annotations

import json
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
UPDATER = APP / "update.py" if (APP / "update.py").is_file() else APP / "tools/update.py"
STATES = {"active", "activating", "deactivating", "failed", "inactive", "maintenance",
          "reloading", "refreshing", "unknown"}
STATE_TEXT = {"active": ("Running", "#3ba55c"), "activating": ("Starting", "#d9a441"),
              "deactivating": ("Stopping", "#d9a441"), "failed": ("Failed", "#d9534f"),
              "inactive": ("Paused", "#8b949e"), "reloading": ("Reloading", "#d9a441"),
              "refreshing": ("Refreshing", "#d9a441"), "maintenance": ("Maintenance", "#d9a441")}
THRESHOLD_CHOICES = (50, 25, 20, 15, 10, 5)
POLL_CHOICES = (30, 60, 120, 300, 600)


def read_settings():
    try:
        return CONFIG.read_text()
    except OSError:
        return ""


def configured(text, key, default):
    return frame_hev.parse_settings(text).get(key, default)


def number(values, key, default):
    try:
        return float(values.get(key, default))
    except (TypeError, ValueError):
        return float(default)


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


def run(command, timeout):
    """Run one command, returning None on success or a short failure message."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        return str(error)
    if result.returncode == 0:
        return None
    return (result.stderr or result.stdout).strip() or f"exit status {result.returncode}"


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
        self.set_default_size(500, -1)
        self.set_resizable(False)
        self.refreshing = False
        self.syncing_switch = False
        self.update_state = "idle"       # idle | checking | offered | updating | done
        self.update_version = None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add(box)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        heading = Gtk.Label(xalign=0)
        heading.set_markup('<span size="x-large" weight="bold">Frame HEV</span>')
        header.pack_start(heading, False, False, 0)
        self.status = Gtk.Label(xalign=1)
        header.pack_end(self.status, False, False, 0)
        box.pack_start(header, False, False, 0)

        self.percent = Gtk.Label(xalign=0)
        self.percent.set_markup('<span size="xx-large" weight="bold">—</span>')
        box.pack_start(self.percent, False, False, 0)

        self.detail = Gtk.Label(xalign=0)
        self.detail.set_line_wrap(True)
        box.pack_start(self.detail, False, False, 0)

        self.bar = Gtk.LevelBar()
        self.bar.set_min_value(0)
        self.bar.set_max_value(100)
        self.bar.set_mode(Gtk.LevelBarMode.CONTINUOUS)
        self.bar.set_value(0)
        box.pack_start(self.bar, False, False, 0)

        self.levels = Gtk.Label(xalign=0)
        self.levels.set_line_wrap(True)
        self.levels.set_max_width_chars(56)
        box.pack_start(self.levels, False, False, 0)

        service_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        service_row.pack_start(Gtk.Label(xalign=0, label="Background service"), False, False, 0)
        self.service_switch = Gtk.Switch()
        self.service_switch.set_valign(Gtk.Align.CENTER)
        self.service_switch.set_tooltip_text("Start or stop the background announcement service")
        self.service_switch.connect("state-set", self.on_service_switch)
        service_row.pack_end(self.service_switch, False, False, 0)
        box.pack_start(service_row, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.announce = Gtk.Button(label="Announce now")
        self.announce.connect("clicked", self.on_announce)
        settings = Gtk.Button(label="Settings…")
        settings.connect("clicked", self.on_settings)
        actions.pack_start(self.announce, True, True, 0)
        actions.pack_start(settings, True, True, 0)
        box.pack_start(actions, False, False, 0)

        self.note = Gtk.Label(xalign=0)
        self.note.set_line_wrap(True)
        box.pack_start(self.note, False, False, 0)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.version_label = Gtk.Label(xalign=0)
        self.version_label.set_line_wrap(True)
        self.version_label.set_text(f"Version {version()}")
        self.version_label.set_tooltip_text(f"Settings: {CONFIG}")
        footer.pack_start(self.version_label, True, True, 0)
        self.update_button = Gtk.Button(label="Check for updates")
        self.update_button.get_style_context().add_class("flat")
        self.update_button.connect("clicked", self.on_update)
        footer.pack_end(self.update_button, False, False, 0)
        uninstall = Gtk.Button(label="Uninstall…")
        uninstall.get_style_context().add_class("destructive-action")
        uninstall.connect("clicked", self.on_uninstall)
        footer.pack_end(uninstall, False, False, 0)
        box.pack_start(footer, False, False, 0)

    # --- state ---------------------------------------------------------------
    def refresh(self):
        if self.refreshing:
            return
        self.refreshing = True

        def work():
            try:
                source, percent = frame_hev.read_battery()
                other = None
                try:
                    other_source, other_percent = frame_hev.read_battery(
                        source="steamvr" if str(source) != "openvr:hmd" else "kernel")
                    if other_percent != percent:
                        other = ("SteamVR" if str(other_source) == "openvr:hmd" else "OS", other_percent)
                except (OSError, RuntimeError, ValueError):
                    other = None
                battery = {"percent": percent, "source": str(source), "other": other,
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
            self.bar.set_value(0)
        else:
            self.percent.set_markup(f'<span size="xx-large" weight="bold">{battery["percent"]}%</span>')
            self.bar.set_value(battery["percent"])
            charging = battery["charging"]
            activity = "charging" if charging else ("discharging" if charging is False else "charge state unknown")
            gauge = "SteamVR" if battery["source"] == "openvr:hmd" else "OS"
            detail = f"{activity.capitalize()} · {gauge} gauge"
            if battery.get("other"):
                detail += f" · {battery['other'][0]} shows {battery['other'][1]}%"
            self.detail.set_text(detail)
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
        text, colour = STATE_TEXT.get(state, ("Unknown", "#8b949e"))
        self.status.set_markup(f'<span foreground="{colour}" size="large">●</span>  <b>{text}</b>')
        self.status.set_tooltip_text(detail if state == "unknown" else f"Background service: {state}")
        self.syncing_switch = True
        self.service_switch.set_active(state == "active")
        self.syncing_switch = False
        self.service_switch.set_sensitive(state != "unknown")
        return False

    # --- actions -------------------------------------------------------------
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

    def on_service_switch(self, switch, wanted):
        if self.syncing_switch:
            return False
        switch.set_sensitive(False)
        self.note.set_text("Starting the background service…" if wanted else "Stopping the background service…")

        def work():
            problem = run(["systemctl", "--user", "start" if wanted else "stop", UNIT], 30)
            GLib.idle_add(self.after_service, wanted, problem)

        threading.Thread(target=work, name="frame-hev-panel-service", daemon=True).start()
        return True

    def after_service(self, wanted, problem):
        self.syncing_switch = True
        self.service_switch.set_active(wanted if problem is None else not wanted)
        self.syncing_switch = False
        self.service_switch.set_sensitive(True)
        self.note.set_text("" if problem is None else
                           f"Could not {'start' if wanted else 'stop'} the service: {problem}")
        self.refresh()
        return False

    # --- settings ------------------------------------------------------------
    def on_settings(self, _button):
        original = read_settings()
        values = frame_hev.parse_settings(original)
        try:
            selected = set(frame_hev.parse_thresholds(values.get("FRAME_HEV_THRESHOLDS",
                                                                  frame_hev.DEFAULT_THRESHOLDS)))
        except ValueError:
            selected = set(frame_hev.parse_thresholds(frame_hev.DEFAULT_THRESHOLDS))
        levels = sorted(set(THRESHOLD_CHOICES) | selected, reverse=True)

        dialog = Gtk.Dialog(title="Frame HEV settings", transient_for=self, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.set_default_size(520, -1)
        dialog.set_resizable(False)
        area = dialog.get_content_area()
        area.set_spacing(10)
        area.set_border_width(16)

        def section(title):
            label = Gtk.Label(xalign=0)
            label.set_markup(f"<b>{title}</b>")
            area.pack_start(label, False, False, 0)

        def row(text, widget, suffix=None):
            holder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            holder.pack_start(Gtk.Label(xalign=0, label=text), False, False, 0)
            if suffix is not None:
                holder.pack_end(suffix, False, False, 0)
            holder.pack_end(widget, False, False, 0)
            area.pack_start(holder, False, False, 0)

        section("Automatic announcements")
        area.pack_start(Gtk.Label(xalign=0, label="Announce when the battery drops through:"),
                        False, False, 0)
        toggles = {}
        grid = Gtk.Grid(column_homogeneous=True, column_spacing=6, row_spacing=6)
        provider = Gtk.CssProvider()
        provider.load_from_data(b".hev-level { padding: 8px 12px; }")
        for index, level in enumerate(levels):
            toggle = Gtk.CheckButton(label=f"{level}%")
            toggle.set_active(level in selected)
            toggle.set_tooltip_text(f"Announce at {level}% while discharging")
            toggle.get_style_context().add_class("hev-level")
            toggle.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            grid.attach(toggle, index % 6, index // 6, 1, 1)
            toggles[level] = toggle
        area.pack_start(grid, False, False, 0)

        summary = Gtk.Label(xalign=0)
        summary.set_line_wrap(True)
        summary.set_max_width_chars(56)

        def refresh_summary(*_ignored):
            chosen = sorted((level for level, box in toggles.items() if box.get_active()), reverse=True)
            summary.set_text("Announcing at " + ", ".join(f"{level}%" for level in chosen)
                             + " while discharging." if chosen
                             else "Automatic announcements are off.")

        for toggle in toggles.values():
            toggle.connect("toggled", refresh_summary)
        refresh_summary()
        area.pack_start(summary, False, False, 0)

        hint = Gtk.Label(xalign=0)
        hint.set_line_wrap(True)
        hint.set_max_width_chars(56)
        hint.get_style_context().add_class("dim-label")
        hint.set_text("A level announces after the battery has been above it. If the battery "
                      "is already below a level you just enabled, that level starts after the "
                      "next charge above it.")
        area.pack_start(hint, False, False, 0)

        poll = Gtk.ComboBoxText()
        current_poll = int(number(values, "FRAME_HEV_THRESHOLD_POLL", frame_hev.DEFAULT_THRESHOLD_POLL))
        for seconds in sorted(set(POLL_CHOICES) | {current_poll}):
            label = (f"{seconds} seconds" if seconds < 60 else
                     f"{seconds // 60} minute" + ("s" if seconds >= 120 else ""))
            poll.append(str(seconds), label)
        poll.set_active_id(str(current_poll))
        row("Check every", poll)

        volume = number(values, "FRAME_HEV_VOLUME", 0.65)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        scale.set_value(max(0.0, min(1.0, volume)) * 100)
        scale.set_digits(0)
        scale.set_draw_value(False)
        scale.set_size_request(220, -1)
        volume_label = Gtk.Label(label=f"{int(scale.get_value())}%")
        scale.connect("value-changed", lambda widget: volume_label.set_text(f"{int(widget.get_value())}%"))
        row("Volume", scale, volume_label)

        section("Battery gauge")
        gauges = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        kernel_radio = Gtk.RadioButton.new_with_label(None, "OS gauge — matches the level the headset shuts down at")
        steam_radio = Gtk.RadioButton.new_with_label_from_widget(kernel_radio,
                                                                 "SteamVR — matches the percentage Steam displays")
        lowest_radio = Gtk.RadioButton.new_with_label_from_widget(kernel_radio,
                                                                  "Lowest of both — warn at whichever reads lower")
        (steam_radio if values.get("FRAME_HEV_BATTERY_SOURCE") == "steamvr" else
         lowest_radio if values.get("FRAME_HEV_BATTERY_SOURCE") == "lowest" else
         kernel_radio).set_active(True)
        gauges.pack_start(kernel_radio, False, False, 0)
        gauges.pack_start(steam_radio, False, False, 0)
        gauges.pack_start(lowest_radio, False, False, 0)
        area.pack_start(gauges, False, False, 0)

        section("Power button")
        radios = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        power = Gtk.RadioButton.new_with_label(None, "Double-tap the power button")
        aux = Gtk.RadioButton.new_with_label_from_widget(power, "Use the button above power instead")
        (aux if values.get("FRAME_HEV_BUTTON") == "aux" else power).set_active(True)
        radios.pack_start(power, False, False, 0)
        radios.pack_start(aux, False, False, 0)
        area.pack_start(radios, False, False, 0)

        tap = Gtk.SpinButton.new_with_range(150, 800, 10)
        tap.set_value(number(values, "FRAME_HEV_DOUBLE_TAP_MS", 350))
        row("Double-tap window", tap, Gtk.Label(label="ms"))

        area.pack_start(Gtk.Label(xalign=0, label="Saving restarts the background service."),
                        False, False, 0)
        dialog.show_all()
        while True:
            if dialog.run() != Gtk.ResponseType.OK:
                break
            chosen = sorted((level for level, toggle in toggles.items() if toggle.get_active()), reverse=True)
            replacement = {
                "FRAME_HEV_VOLUME": f"{scale.get_value() / 100:.2f}",
                "FRAME_HEV_BUTTON": "aux" if aux.get_active() else "power",
                "FRAME_HEV_DOUBLE_TAP_MS": str(int(tap.get_value())),
                "FRAME_HEV_THRESHOLDS": ",".join(map(str, chosen)) or "off",
                "FRAME_HEV_THRESHOLD_POLL": poll.get_active_id() or str(current_poll),
                "FRAME_HEV_BATTERY_SOURCE": ("steamvr" if steam_radio.get_active() else
                                             "lowest" if lowest_radio.get_active() else "kernel"),
            }
            text = frame_hev.render_settings(original, replacement)
            problem = frame_hev.settings_problem(text)
            if problem:
                error_dialog(self, problem)
                continue
            try:
                save_settings(text)
            except OSError as error:
                error_dialog(self, f"Could not save the settings: {error}")
                continue
            self.note.set_text("Settings saved.")
            break
        dialog.destroy()
        self.refresh()
        if self.service_switch.get_active():
            self.on_restart(None)

    # --- updates -------------------------------------------------------------
    def on_update(self, _button):
        if self.update_state == "offered":
            self.apply_update()
        elif self.update_state in ("idle", "done"):
            self.check_update()

    def update_repo(self):
        return configured(read_settings(), "FRAME_HEV_UPDATE_REPO", "")

    def update_args(self, *extra):
        """Leave --repo off unless the settings name one, so update.py's default stands."""
        args = [sys.executable, str(UPDATER), *extra]
        repo = self.update_repo()
        return [*args, "--repo", repo] if repo else args

    def check_update(self):
        self.update_state = "checking"
        self.update_button.set_sensitive(False)
        self.update_button.set_label("Checking…")

        def work():
            try:
                result = subprocess.run(self.update_args("--json"),
                                        capture_output=True, text=True, timeout=60)
                answer = json.loads(result.stdout or "{}")
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                GLib.idle_add(self.after_check, str(error), None)
                return
            problem = None if result.returncode == 0 else (result.stderr.strip() or f"exit {result.returncode}")
            GLib.idle_add(self.after_check, problem, answer)

        threading.Thread(target=work, name="frame-hev-panel-update-check", daemon=True).start()

    def after_check(self, problem, report):
        self.update_button.set_sensitive(True)
        if isinstance(report, dict) and report.get("error"):
            problem, report = report["error"], None
        if problem is not None or not isinstance(report, dict):
            self.update_state = "idle"
            self.update_button.set_label("Check for updates")
            self.note.set_text(f"Update check failed: {problem or 'unreadable answer'}")
            return False
        if report.get("newer"):
            self.update_state = "offered"
            self.update_version = report.get("latest")
            self.update_button.set_label(f"Update to v{self.update_version}")
            self.update_button.get_style_context().add_class("suggested-action")
            self.note.set_text(f"Version {self.update_version} is available.")
        else:
            self.update_state = "done"
            self.update_button.set_label(f"Up to date (v{report.get('installed')})")
            self.note.set_text("")
        return False

    def apply_update(self):
        self.update_state = "updating"
        self.update_button.set_sensitive(False)
        self.update_button.set_label("Updating…")

        def work():
            problem = run(self.update_args("--apply"), 900)
            GLib.idle_add(self.after_apply, problem)

        threading.Thread(target=work, name="frame-hev-panel-update-apply", daemon=True).start()

    def after_apply(self, problem):
        self.update_button.set_sensitive(True)
        if problem is None:
            self.update_state = "done"
            self.update_button.set_label("Updated")
            self.note.set_text("Updated. Reopen the panel to run the new version.")
        else:
            self.update_state = "offered"
            self.update_button.set_label(f"Update to v{self.update_version}")
            self.note.set_text(f"Update failed: {problem}")
        self.refresh()
        return False

    # --- removal -------------------------------------------------------------
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
