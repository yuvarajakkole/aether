# Installing and running Aether

This guide takes you from the zip file to a working Aether in about ten minutes, then shows how to
verify every feature and connect your own models. Nothing beyond Docker is required on your machine.

## 1. Requirements

| | |
|---|---|
| Docker | Docker Desktop 4.x (macOS, Windows) or Docker Engine 24+ with the Compose v2 plugin (Linux). `docker compose version` must work. |
| Resources | 2 CPU cores, 4 GB RAM free for Docker, 3 GB disk. |
| Ports | 8080 (web UI and API) and 8100 (sample agents) free, or choose others in `.env`. |
| Network | The first build downloads base images from Docker Hub and Python packages from PyPI. After that Aether runs offline unless you connect real models. |

No Python, Node or database installation is needed; everything runs in containers.

## 2. Start

macOS, Linux, or Windows (inside WSL or Git Bash):

```bash
unzip aether-*.zip
cd aether
./start.sh
```

`start.sh` checks Docker, creates `.env` with a random secret key, builds the images, starts the
stack (Postgres, migrations, API + UI, 2 workers, sample agents) and waits until it is ready.
When it prints **Aether is running**, open <http://localhost:8080>.

Without the script (any OS, including Windows PowerShell):

```bash
cp .env.example .env          # Windows: copy .env.example .env
# edit .env and set AETHER_SECRET_KEY to any long random string
docker compose up -d --build
# wait ~1 minute, then open http://localhost:8080
```

## 3. Verify the installation

```bash
docker compose exec api aether selftest --url http://localhost:8000
```

The self-test drives every feature through the API using built-in offline mock models: providers and
secret masking, agents and prompt versions, suite import/export/generation, a multi-model run with
failure injection, the task field and replay, review, comparison, re-scoring, CSV export, the CI gate
with API keys, both kinds of external agents, real-API tool calls, the audit log and metrics. It
cleans up after itself and ends with `13 of 13 checks passed`. Add `--live` to also test every real
model you configured. `./start.sh --selftest` starts and verifies in one go.

## 4. First tour (5 minutes, no API keys needed)

1. **Agents** shows four sample agents (all brands are fictional): two built in Aether, one external
   agent speaking the Aether protocol, one OpenAI-compatible endpoint.
2. Open **Hotel Concierge**, then **Run an evaluation**. Pick *Mock (perfect)* and *Mock (flaky)*,
   3 attempts, keep *Simulate the response*, start.
3. The run page fills in live: the verdict, model strips, reliability curve and the task field.
   Click any tile to replay that attempt step by step; mark it pass or fail.
4. **New test suite** on an agent shows how Aether writes tests from the agent's prompt and tools.
5. **Models** lists providers; **Settings** has API keys for CI and the audit log.

The mock models are deterministic stand-ins so you can see every screen; connect real models next.

## 5. Connect real models

Edit `.env`, then run `docker compose up -d` (recreates containers with the new values):

| You have | Set in `.env` |
|---|---|
| OpenAI | `OPENAI_API_KEY=sk-...` (gpt-4o-mini, gpt-5, gpt-5.2 are added automatically) |
| Anthropic | `ANTHROPIC_API_KEY=sk-ant-...` (Claude Sonnet 4.6 and Opus 4.6) |
| A company LLM gateway that speaks the OpenAI API | `GATEWAY_API_KEY`, `GATEWAY_APP_NAME`, `GATEWAY_OPENAI_BASE_URL` (and `GATEWAY_BEDROCK_BASE_URL` if it proxies Bedrock's Converse API) |

Anything else (Azure OpenAI, Amazon Bedrock with an API key, Ollama, vLLM, LiteLLM, other model names)
can be added in the UI: **Models > Connect a provider**, then **Add a model**, then **Test**. Keys are
encrypted at rest and never shown again. The model list, prices and quotas created at startup live in
`config/bootstrap.json`; edit it to change them.

Aether also uses models for its own work: writing tasks, simulating tools and users, and judging.
By default it picks a configured real model (GPT-5.2 if available); change these on the **Models**
page with the *write tasks*, *simulate* and *judge* tags.

## 6. Running it for a team

**Authentication.** In `.env` set `AETHER_AUTH_MODE=api_key` (or `oidc` with `AETHER_OIDC_ISSUER`,
`AETHER_OIDC_AUDIENCE` and `AETHER_OIDC_JWKS_URL` for Okta, Auth0, Entra ID, Keycloak). `start.sh`
generates `AETHER_BOOTSTRAP_API_KEY`; paste it under **Settings > This browser** to use the UI, and
create further keys there. In `oidc` mode the API accepts tokens from your identity provider; the web UI
does not yet run the browser sign-in flow itself, so UI users paste a key or token in Settings.

**Corporate networks.** Set `HTTP_PROXY`, `HTTPS_PROXY` and `NO_PROXY` in `.env` if outbound traffic
goes through a proxy. If your network inspects TLS or your gateway uses an internal certificate
authority, save the CA certificates as `config/ca-bundle.pem` and set
`AETHER_CA_BUNDLE=/config/ca-bundle.pem`. On macOS you can export the system's trusted certificates with:

```bash
security find-certificate -a -p /Library/Keychains/System.keychain /System/Library/Keychains/SystemRootCertificates.keychain > config/ca-bundle.pem
```

For Docker builds behind a proxy, configure the proxy in Docker Desktop (Settings > Resources > Proxies).

**Scale.** `AETHER_WORKERS=4` runs four workers; `AETHER_WORKER_CONCURRENCY` sets parallel attempts per
worker. Per-model request and token quotas are set on each model so Aether stays under your limits.

**Database.** Data lives in the `pgdata` Docker volume. To use a managed Postgres instead, point
`AETHER_DATABASE_URL` in `docker-compose.yml` at it (`postgresql+asyncpg://user:pass@host:5432/db`).

**Monitoring.** `GET /healthz` (liveness), `GET /readyz` (database reachable), Prometheus metrics at
`/metrics` on the API and on port 9101 of each worker; logs are JSON on stdout.

## 7. Everyday commands

| | |
|---|---|
| Stop (keep data) | `docker compose down` |
| Start again | `docker compose up -d` |
| Logs | `docker compose logs -f api worker` |
| Upgrade to a new zip | unzip over the folder (keep your `.env`), then `docker compose up -d --build` |
| Reset everything (deletes data) | `docker compose down -v` |
| Use the CLI in CI | see [docs/ci.md](docs/ci.md) |

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `port 8080 is already in use` | Set `AETHER_PORT=8090` (and/or `SAMPLES_PORT`) in `.env`. |
| `set AETHER_SECRET_KEY in .env` | Run `./start.sh`, or put any long random string in `AETHER_SECRET_KEY`. |
| Build fails downloading packages | Check internet/proxy access to Docker Hub and PyPI; behind a proxy, set it in Docker Desktop. The UI is prebuilt in the zip, so npm is not needed. |
| Model test says `TLS verification failed` | Your network needs a corporate CA: see *Corporate networks* above. |
| Model test says `HTTP 401/403` | The key is wrong or lacks access to that model; check the provider's console. |
| Runs stay queued | Workers are not running: `docker compose ps` should list two healthy `worker` containers; see `docker compose logs worker`. |
| HTTP 429 in worker logs | Normal back-pressure: Aether retries automatically. Lower the model's quota on the Models page to stay under your limit. |
| Self-test fails on *External agents* or *Real-API tool mode* | The `samples` container must be running (`docker compose ps samples`). |

## 9. Developing Aether

See the README's *Local development* section: `scripts/dev-samples.sh`, `scripts/dev-api.sh` and
`npm run dev` in `frontend/` run everything without Docker, with hot reload.
