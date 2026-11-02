#!/usr/bin/env bash
# Remove Frame HEV: background service, application, launchers, menu entry,
# icons, and (with --purge) settings and cached audio.
#
#   frame-hev uninstall             # remove the app, keep settings and cache
#   frame-hev uninstall --purge     # also remove settings and cached audio
#   bash uninstall.sh --dry-run     # list what would be removed and exit
#
# The application menu's "Uninstall Frame HEV…" action uses --dry-run for its
# confirmation text and then runs this script with --purge.
set -euo pipefail

purge=false
dry_run=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --purge) purge=true; shift ;;
        --dry-run) dry_run=true; shift ;;
        --help|-h)
            sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
if [[ "$(id -u)" == 0 ]]; then echo 'Run without sudo, as the Steam user.' >&2; exit 1; fi

# A menu or SSH launch can arrive without a usable session bus. Repointing at the
# Steam user's own runtime keeps systemctl --user working, and without it this
# script would abort half-way through the removal.
runtime="/run/user/$(id -u)"
if [[ ! -S "${XDG_RUNTIME_DIR:-}/bus" && -S "$runtime/bus" ]]; then
    XDG_RUNTIME_DIR="$runtime"
    DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime/bus"
    export XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS
fi

APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}"

refresh_menu() {
    for tool in kbuildsycoca6 /usr/sbin/kbuildsycoca6 /usr/bin/kbuildsycoca6 \
                kbuildsycoca5 /usr/sbin/kbuildsycoca5; do
        if command -v "$tool" >/dev/null 2>&1 || [[ -x "$tool" ]]; then
            "$tool" --noincremental >/dev/null 2>&1 || true
            return 0
        fi
    done
    return 0
}

# Enumerate the installer's outputs rather than a remembered file list: leaving
# one behind is how an uninstaller silently stops being an uninstaller.
targets=()
add() { if [[ -e "$1" || -L "$1" ]]; then targets+=("$1"); fi; }
add "$HOME/.local/share/frame-hev"
add "$HOME/.config/systemd/user/frame-hev.service"
add "$HOME/.local/bin/frame-hev"
add "$HOME/.local/bin/frame-hev-panel"
add "$HOME/.local/bin/frame-hev-uninstall"
add "$APPS/frame-hev.desktop"
if [[ -L "$HOME/Desktop/frame-hev.desktop" ]]; then
    # Only our shortcut (a symlink to the menu entry); never a user's own file.
    targets+=("$HOME/Desktop/frame-hev.desktop")
fi
for size in 48x48 64x64 128x128 256x256 scalable; do
    for ext in png svg; do add "$ICONS/$size/apps/frame-hev.$ext"; done
done
add "$CACHE/frame-hev-uninstall.log"
if $purge; then
    add "$HOME/.config/frame-hev"
    add "$CACHE/frame-hev"
fi

if $dry_run; then
    if [[ ${#targets[@]} -eq 0 ]]; then
        echo 'Nothing to remove - Frame HEV does not look installed here.'
        exit 0
    fi
    echo 'Frame HEV will be removed:'
    printf '  %s\n' "${targets[@]}"
    exit 0
fi

if systemctl --user cat frame-hev.service >/dev/null 2>&1; then
    # If stopping fails, keep the files so the running service can be diagnosed.
    systemctl --user stop frame-hev.service
    systemctl --user disable frame-hev.service
fi
# The panel is a separate process; the service stop above does not end it.
for pid in $(pgrep -f 'frame_hev_panel\.py' 2>/dev/null || true); do
    kill "$pid" 2>/dev/null || true
done
rm -f -- "$HOME/.config/systemd/user/frame-hev.service" \
         "$HOME/.local/bin/frame-hev" "$HOME/.local/bin/frame-hev-panel" \
         "$HOME/.local/bin/frame-hev-uninstall"
systemctl --user daemon-reload 2>/dev/null || true
systemctl --user reset-failed frame-hev.service 2>/dev/null || true
rm -rf -- "$HOME/.local/share/frame-hev"
rm -f -- "$APPS/frame-hev.desktop" "$CACHE/frame-hev-uninstall.log"
if [[ -L "$HOME/Desktop/frame-hev.desktop" ]]; then
    rm -f -- "$HOME/Desktop/frame-hev.desktop"
fi
for size in 48x48 64x64 128x128 256x256 scalable; do
    rm -f -- "$ICONS/$size/apps/frame-hev.png" "$ICONS/$size/apps/frame-hev.svg"
done
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$ICONS" >/dev/null 2>&1 || true
fi
# Plasma caches menu entries; without this the app lingers until logout.
refresh_menu
if $purge; then
    rm -rf -- "$HOME/.config/frame-hev" "$CACHE/frame-hev"
fi

echo 'Frame HEV removed. Native power-button behavior is restored.'
if ! $purge; then echo 'Settings and cache retained; use --purge to remove them too.'; fi
