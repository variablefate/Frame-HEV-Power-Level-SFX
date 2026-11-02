#!/usr/bin/env bash
# Right-click "Uninstall Frame HEV…" in the application menu.
#
# Shows exactly what will be removed in a KDE dialog, then runs the installed
# uninstaller with --purge. Falls back to zenity, a terminal, then plain output.
set -uo pipefail

# Menus launch with a thinner PATH than a login shell; /usr/sbin may be absent.
PATH="/usr/local/bin:/usr/bin:/usr/sbin:/bin:/sbin:${PATH:-}"
export PATH

APP="${FRAME_HEV_HOME:-$HOME/.local/share/frame-hev}"
SCRIPT="$APP/uninstall.sh"
LOG="${XDG_CACHE_HOME:-$HOME/.cache}/frame-hev-uninstall.log"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

has_display() { [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; }

notify_error() {
  echo "[$(date -Is)] $1" >>"$LOG"
  if has_display; then
    if command -v kdialog >/dev/null 2>&1; then
      kdialog --title "Frame HEV" --error "$1" 2>/dev/null && return
    elif command -v zenity >/dev/null 2>&1; then
      zenity --error --text "$1" 2>/dev/null && return
    fi
  fi
  echo "$1" >&2
}

[ -f "$SCRIPT" ] || { notify_error "No uninstaller at $SCRIPT."; exit 1; }

if ! has_display; then
  notify_error "No display available - open a terminal and run: bash $SCRIPT --purge"
  exit 1
fi

preview="$(bash "$SCRIPT" --dry-run --purge 2>&1)"
echo "[$(date -Is)] --- run ---" >>"$LOG"
printf '%s\n' "$preview" >>"$LOG"

if command -v kdialog >/dev/null 2>&1; then
  if kdialog --title "Uninstall Frame HEV" --icon edit-delete \
       --yesno "${preview}"$'\n\n'"Remove all of this? Settings and cached audio are deleted too."; then
    if bash "$SCRIPT" --purge >>"$LOG" 2>&1; then
      kdialog --title "Frame HEV" --msgbox "Frame HEV has been removed."
    else
      notify_error "Removal failed or was incomplete. Details: $LOG"
    fi
  fi
  exit 0
fi

if command -v zenity >/dev/null 2>&1; then
  if zenity --question --title "Uninstall Frame HEV" --text "$preview"; then
    bash "$SCRIPT" --purge >>"$LOG" 2>&1 \
      && zenity --info --text "Frame HEV has been removed." \
      || notify_error "Removal failed. Details: $LOG"
  fi
  exit 0
fi

for term in konsole x-terminal-emulator xfce4-terminal gnome-terminal xterm; do
  command -v "$term" >/dev/null 2>&1 || continue
  case "$term" in
    konsole)             exec konsole --hold -e bash "$SCRIPT" --purge ;;
    x-terminal-emulator) exec x-terminal-emulator -e bash "$SCRIPT" --purge ;;
    xfce4-terminal)      exec xfce4-terminal --hold -x bash "$SCRIPT" --purge ;;
    gnome-terminal)      exec gnome-terminal -- bash -c "bash '$SCRIPT' --purge; read -r _" ;;
    xterm)               exec xterm -e bash -c "bash '$SCRIPT' --purge; read -r _" ;;
  esac
done

notify_error "No dialog or terminal found - run: bash $SCRIPT --purge"
exit 1
