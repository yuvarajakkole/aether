#!/usr/bin/env bash
# Start Aether with Docker. Usage: ./start.sh [--selftest] [--rebuild]
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }
trap 'printf "\033[31mError:\033[0m start.sh stopped at line %s (exit %s). Re-run with: bash -x ./start.sh\n" "$LINENO" "$?" >&2' ERR

SELFTEST=0
BUILD_ARGS=(--build)
for arg in "$@"; do
  case "$arg" in
    --selftest) SELFTEST=1 ;;
    --rebuild) BUILD_ARGS=(--build --force-recreate) ;;
    -h|--help) sed -n '2p' "$0"; exit 0 ;;
    *) fail "unknown option $arg" ;;
  esac
done

command -v docker >/dev/null 2>&1 || fail "Docker is not installed. Install Docker Desktop (macOS/Windows) or Docker Engine (Linux) first."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required ('docker compose'). Update Docker Desktop or install the compose plugin."
docker info >/dev/null 2>&1 || fail "Docker is installed but not running. Start Docker Desktop (or the docker service) and try again."

random_hex() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex "$1"
  else od -An -tx1 -N"$1" /dev/urandom | tr -d ' \n'; fi
}
env_get() { { grep -E "^$1=" .env 2>/dev/null || true; } | tail -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"; }
env_set() {
  if grep -qE "^$1=" .env; then
    sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}

if [[ ! -f .env ]]; then
  cp .env.example .env
  say "Created .env from .env.example"
fi
secret="$(env_get AETHER_SECRET_KEY)"
if [[ -z "$secret" || "$secret" == replace-with-* ]]; then
  env_set AETHER_SECRET_KEY "$(random_hex 32)"
  say "Generated a random AETHER_SECRET_KEY in .env"
fi
auth="$(env_get AETHER_AUTH_MODE)"
if [[ -n "$auth" && "$auth" != "none" && -z "$(env_get AETHER_BOOTSTRAP_API_KEY)" ]]; then
  env_set AETHER_BOOTSTRAP_API_KEY "ak_$(random_hex 24)"
  say "Authentication is on: generated an admin API key (AETHER_BOOTSTRAP_API_KEY in .env)"
fi

PORT="$(env_get AETHER_PORT)"; PORT="${PORT:-8080}"
SPORT="$(env_get SAMPLES_PORT)"; SPORT="${SPORT:-8100}"
running="$(docker compose ps --status running --services 2>/dev/null || true)"
for p in "$PORT" "$SPORT"; do
  if [[ -z "$running" ]] && (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then
    fail "port $p is already in use. Set AETHER_PORT / SAMPLES_PORT in .env to free ports."
  fi
done

say "Building and starting Aether (first run downloads images and dependencies; this can take a few minutes)..."
docker compose up -d "${BUILD_ARGS[@]}"

say "Waiting for Aether to become ready on http://localhost:$PORT ..."
for i in $(seq 1 120); do
  if [[ "$(docker compose ps api --format '{{.Health}}' 2>/dev/null)" == "healthy" ]] \
     && docker compose exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3)" >/dev/null 2>&1; then
    ready=1; break
  fi
  if docker compose ps -a --format '{{.Service}} {{.State}} {{.ExitCode}}' 2>/dev/null | grep -E '^migrate exited [1-9]' >/dev/null; then
    docker compose logs migrate | tail -30
    fail "database migration failed (logs above)."
  fi
  sleep 3
done
[[ "${ready:-0}" == 1 ]] || { docker compose logs --tail 40 api; fail "Aether did not become ready in 6 minutes (api logs above)."; }

say ""
say "Aether is running:  http://localhost:$PORT"
if [[ -n "$auth" && "$auth" != "none" ]]; then
  say "Sign in: open Settings and paste the admin key from AETHER_BOOTSTRAP_API_KEY in .env"
fi
say "Stop:    docker compose down        (keeps your data)"
say "Verify:  docker compose exec api aether selftest --url http://localhost:8000"

if [[ "$SELFTEST" == 1 ]]; then
  say ""
  say "Running the self-test..."
  key="$(env_get AETHER_BOOTSTRAP_API_KEY)"
  docker compose exec -T api aether selftest --url http://localhost:8000 ${key:+--api-key "$key"}
fi
