#!/usr/bin/env bash
# Run the sample tool APIs and external agents on :8100.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
set -a
[[ -f "$ROOT/.env" ]] && source "$ROOT/.env"
set +a
cd "$ROOT/sample_agents"
[[ -d .venv ]] || { uv venv -q --python 3.12 && uv pip install -q -e .; }
exec .venv/bin/uvicorn sample_agents.app:app --host 0.0.0.0 --port "${PORT:-8100}" --log-level warning
