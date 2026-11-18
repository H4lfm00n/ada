#!/usr/bin/env bash
# Open the kiosk full screen in Chromium kiosk mode on this machine (the GB10's screen).
#   scripts/kiosk-browser.sh            # waits for the kiosk server, then opens it
#   KIOSK_URL=http://127.0.0.1:8080/ui/ scripts/kiosk-browser.sh
# Leave kiosk mode with Alt+F4. Nothing is stored: a throwaway profile, incognito.
# The browser is only opened once /healthz answers, so the screen never shows a
# "site can't be reached" page while the server is still starting.
set -euo pipefail

URL="${KIOSK_URL:-http://127.0.0.1:8080/ui/}"
HEALTH="${URL%/ui/*}/healthz"

BROWSER="${KIOSK_BROWSER:-}"
if [[ -z "$BROWSER" ]]; then
  for b in chromium chromium-browser google-chrome google-chrome-stable; do
    if command -v "$b" >/dev/null 2>&1; then BROWSER="$b"; break; fi
  done
fi
if [[ -z "$BROWSER" ]]; then
  echo "no Chromium found. Install it (sudo snap install chromium) or set KIOSK_BROWSER." >&2
  exit 1
fi

echo "waiting for the kiosk server at $HEALTH ..."
until curl -fsS --max-time 2 "$HEALTH" >/dev/null 2>&1; do sleep 2; done

# A fresh profile each launch: no history, no "restore pages?" bubble, no stored data.
PROFILE="$(mktemp -d "${TMPDIR:-/tmp}/ada-kiosk-XXXXXX")"
trap 'rm -rf "$PROFILE"' EXIT

echo "opening $URL with $BROWSER (Alt+F4 to leave)"
"$BROWSER" \
  --kiosk "$URL" \
  --user-data-dir="$PROFILE" \
  --incognito \
  --no-first-run --no-default-browser-check \
  --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
  --disable-translate --disable-features=Translate,TranslateUI \
  --disable-pinch --overscroll-history-navigation=0 \
  --password-store=basic \
  --check-for-update-interval=31536000 \
  "$@"
