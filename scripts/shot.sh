#!/usr/bin/env bash
# Screenshot a page of the running UI with headless Chrome: scripts/shot.sh <path> <out.png> [width] [height]
set -euo pipefail
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
URL="${BASE_URL:-http://localhost:5173}$1"
"$CHROME" --headless=new --disable-gpu --hide-scrollbars --window-size="${3:-1480},${4:-1400}" --virtual-time-budget=9000 --screenshot="$2" "$URL" 2>/dev/null
echo "$2"
