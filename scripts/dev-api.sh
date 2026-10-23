#!/usr/bin/env bash
# Run the Aether API with an embedded worker against a local SQLite database.
# Usage: scripts/dev-api.sh [--fresh]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="${AETHER_DEV_DB:-/tmp/aether-local.db}"
if [[ "${1:-}" == "--fresh" ]]; then rm -f "$DB" "$DB-wal" "$DB-shm"; fi
set -a
[[ -f "$ROOT/.env" ]] && source "$ROOT/.env"
set +a
export AETHER_DATABASE_URL="sqlite+aiosqlite:///$DB"
export AETHER_BOOTSTRAP_FILE="$ROOT/config/bootstrap.json"
export AETHER_SAMPLE_TOOLS_URL="${AETHER_SAMPLE_TOOLS_URL:-http://localhost:8100/tools}"
export AETHER_SAMPLE_AGENTS_URL="${AETHER_SAMPLE_AGENTS_URL:-http://localhost:8100}"
export AETHER_EMBEDDED_WORKER=true
export AETHER_WORKER_CONCURRENCY="${AETHER_WORKER_CONCURRENCY:-12}"
export AETHER_LOG_JSON="${AETHER_LOG_JSON:-false}"
cd "$ROOT/backend"
exec uv run aether serve --port "${PORT:-8000}"
