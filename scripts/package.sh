#!/usr/bin/env bash
# Build a shareable zip: dist-release/aether-<version>.zip (source + prebuilt web UI, no secrets).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VERSION="$(python3 -c "import re;print(re.search(r'__version__ = \"(.+)\"', open('backend/aether/__init__.py').read()).group(1))")"
OUT="$ROOT/dist-release"
STAGE="$(mktemp -d)/aether"

echo "Building web UI..."
(cd frontend && { [[ -d node_modules ]] || npm ci --no-audit --no-fund; } && npm run build >/dev/null)
test -f frontend/dist/index.html

echo "Collecting files..."
mkdir -p "$STAGE"
git ls-files -co --exclude-standard -z | while IFS= read -r -d '' f; do
  [[ -e "$f" ]] || continue
  case "$f" in .superpowers/*|dist-release/*|frontend/test-results/*|frontend/playwright-report/*) continue ;; esac
  mkdir -p "$STAGE/$(dirname "$f")" && cp -p "$f" "$STAGE/$f"
done
cp -R frontend/dist "$STAGE/frontend/dist"

# Refuse to ship anything that looks like a credential.
if grep -rIlE "(sk-[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9-]{20,}|genai_[0-9]+_[A-Za-z0-9]{10,}|BEGIN (RSA |EC )?PRIVATE KEY)" "$STAGE" >/dev/null; then
  grep -rIlE "(sk-[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9-]{20,}|genai_[0-9]+_[A-Za-z0-9]{10,}|BEGIN (RSA |EC )?PRIVATE KEY)" "$STAGE"
  echo "Refusing to package: the files above look like they contain secrets." >&2
  exit 1
fi
for f in .env config/ca-bundle.pem; do [[ ! -e "$STAGE/$f" ]] || { echo "Refusing to package $f" >&2; exit 1; }; done

mkdir -p "$OUT"
ZIP="$OUT/aether-$VERSION.zip"
rm -f "$ZIP"
(cd "$(dirname "$STAGE")" && zip -qr -X "$ZIP" aether)
echo "Created $ZIP ($(du -h "$ZIP" | cut -f1), $(cd "$(dirname "$STAGE")" && find aether -type f | wc -l | tr -d ' ') files)"
