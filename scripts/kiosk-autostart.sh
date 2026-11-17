#!/usr/bin/env bash
# Start the kiosk on boot (NOT enabled by default; the PM decides).
#   scripts/kiosk-autostart.sh status
#   scripts/kiosk-autostart.sh install     # server as a systemd --user service + browser on login
#   scripts/kiosk-autostart.sh uninstall
#
# install writes two files and changes nothing else:
#   ~/.config/systemd/user/ada-kiosk.service   (make kiosk, restarted if it crashes)
#   ~/.config/autostart/ada-kiosk-browser.desktop  (scripts/kiosk-browser.sh on desktop login)
# Both run as the logged-in user, so the screen needs a desktop session (auto-login, or
# someone logging in). For the server to run before anyone logs in:
#   sudo loginctl enable-linger "$USER"     (not done by this script)
# Secrets: put ADA_ADMIN_TOKEN=... in ~/.config/ada/kiosk.env (chmod 600); optional.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/ada-kiosk.service"
DESKTOP="$HOME/.config/autostart/ada-kiosk-browser.desktop"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"

case "${1:-status}" in
  install)
    mkdir -p "$UNIT_DIR" "$(dirname "$DESKTOP")" "$HOME/.config/ada"
    sed -e "s|@REPO@|$REPO|g" -e "s|@UV@|$UV|g" "$REPO/deploy/ada-kiosk.service" > "$UNIT"
    sed -e "s|@REPO@|$REPO|g" "$REPO/deploy/ada-kiosk-browser.desktop" > "$DESKTOP"
    systemctl --user daemon-reload
    systemctl --user enable --now ada-kiosk.service
    echo "installed. Server: systemctl --user status ada-kiosk; browser opens at next login."
    ;;
  uninstall)
    systemctl --user disable --now ada-kiosk.service 2>/dev/null || true
    rm -f "$UNIT" "$DESKTOP"
    systemctl --user daemon-reload
    echo "removed. (~/.config/ada/kiosk.env left in place)"
    ;;
  status)
    if [[ -f "$UNIT" ]]; then systemctl --user --no-pager status ada-kiosk.service || true
    else echo "server autostart: not installed"; fi
    [[ -f "$DESKTOP" ]] && echo "browser autostart: $DESKTOP" || echo "browser autostart: not installed"
    ;;
  *) echo "usage: $0 status|install|uninstall" >&2; exit 2 ;;
esac
