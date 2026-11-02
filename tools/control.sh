#!/usr/bin/env bash
# Frame HEV control: installed as ~/.local/bin/frame-hev.
set -euo pipefail
app_dir="$HOME/.local/share/frame-hev"
action="${1:-announce}"
if [[ $# -gt 0 ]]; then shift; fi

# A terminal or menu launch can lack a usable session bus; repair it so
# systemctl --user works without asking the user to set variables by hand.
runtime="/run/user/$(id -u)"
if [[ ! -S "${XDG_RUNTIME_DIR:-}/bus" && -S "$runtime/bus" ]]; then
    XDG_RUNTIME_DIR="$runtime"
    DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime/bus"
    export XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS
fi
case "$action" in
    announce|doctor|update)
        if [[ -f "$HOME/.config/frame-hev/environment" ]]; then
            set -a
            source "$HOME/.config/frame-hev/environment"
            set +a
        fi
        if [[ "$action" == update ]]; then
            exec python3 "$app_dir/update.py" "$@"
        fi
        exec python3 "$app_dir/frame_hev.py" "$action" "$@"
        ;;
    start|stop|restart|status)
        exec systemctl --user "$action" frame-hev.service "$@"
        ;;
    logs)
        exec journalctl --user -u frame-hev.service -f "$@"
        ;;
    panel)
        exec "$HOME/.local/bin/frame-hev-panel" "$@"
        ;;
    uninstall)
        exec bash "$app_dir/uninstall.sh" "$@"
        ;;
    help|--help|-h)
        cat <<'USAGE'
Usage: ~/.local/bin/frame-hev [command]
  announce [--percent N]   Speak the current battery (or preview N percent)
  doctor                  Show battery, input, and audio diagnostics
  status                  Show service status
  logs                    Follow service logs (Ctrl+C to exit)
  panel                   Open the status panel (Desktop Mode)
  update [--check]        Check GitHub for a newer release; --apply installs it
  start | stop | restart  Control the announcement service
  uninstall [--purge]     Remove the app; --purge also removes settings/cache

Settings: ~/.config/frame-hev/environment
After editing settings: ~/.local/bin/frame-hev restart
USAGE
        ;;
    *) echo "Unknown command: $action. Run frame-hev --help." >&2; exit 2 ;;
esac
