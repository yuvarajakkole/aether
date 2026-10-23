"""Aether command line.

  aether serve              run the API (and optionally an embedded worker)
  aether worker             run a queue worker (scale horizontally by running more)
  aether migrate            apply database migrations
  aether gate ...           CI: start a run against a remote Aether, wait, exit 1 if the release gate fails
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

import typer

app = typer.Typer(add_completion=False, help="Aether agent evaluation platform")


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000, workers: int = 1, reload: bool = False):
    """Run the HTTP API."""
    import uvicorn

    uvicorn.run("aether.api.app:app", host=host, port=port, workers=workers, reload=reload, proxy_headers=True, log_config=None)


@app.command()
def worker(concurrency: int = typer.Option(None, help="Parallel trials (default AETHER_WORKER_CONCURRENCY)"), metrics_port: int = 9101):
    """Run a queue worker."""
    from prometheus_client import start_http_server

    from aether import db as dbm
    from aether.config import get_settings
    from aether.logging_setup import configure_logging
    from aether.providers.base import close_http_client
    from aether.worker import Worker

    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    if metrics_port:
        start_http_server(metrics_port)

    async def main() -> None:
        dbm.init_engine()
        if s.database_url.startswith("sqlite") or s.auto_create_schema:
            await dbm.create_all()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await Worker(concurrency=concurrency).run_forever(stop)
        await close_http_client()
        await dbm.dispose()

    asyncio.run(main())


@app.command()
def migrate():
    """Apply Alembic migrations to AETHER_DATABASE_URL."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent / "migrations"))
    command.upgrade(cfg, "head")


@app.command()
def gate(
    agent: str = typer.Option(..., help="Agent slug"),
    models: list[str] = typer.Option([], "--model", "-m", help="Model id, name or provider/model (repeatable)"),
    suite: str = typer.Option(None, help="Suite id or name (default: newest ready suite)"),
    version: int = typer.Option(None, help="Agent prompt version number (default: latest)"),
    trials: int = 3,
    tool_mode: str = "simulated",
    failure_rate: float = 0.0,
    url: str = typer.Option(os.environ.get("AETHER_URL", "http://localhost:8000"), help="Aether base URL"),
    api_key: str = typer.Option(os.environ.get("AETHER_API_KEY", ""), help="Aether API key"),
    timeout: int = 3600,
):
    """Start a run, wait for it, print the gate result and exit non-zero if it fails."""
    import httpx

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    with httpx.Client(base_url=url, headers=headers, timeout=60) as c:
        body = {"agent": agent, "models": models, "suite": suite, "version": version, "trials": trials, "tool_mode": tool_mode, "failure_rate": failure_rate}
        r = c.post("/api/v1/ci/runs", json=body)
        if r.status_code >= 400:
            typer.echo(f"failed to start run: {r.status_code} {r.text}", err=True)
            raise typer.Exit(2)
        run = r.json()
        typer.echo(f"run {run['number']} started ({run['id']})")
        deadline = time.time() + timeout
        while time.time() < deadline:
            g = c.get(f"/api/v1/runs/{run['id']}/gate").json()
            if g.get("done"):
                break
            p = c.get(f"/api/v1/runs/{run['id']}/progress").json()["total"]
            typer.echo(f"  {p.get('done', 0) + p.get('error', 0)} / {sum(p.values())} attempts finished")
            time.sleep(10)
        else:
            typer.echo("timed out waiting for the run", err=True)
            raise typer.Exit(2)
        if g.get("status") != "completed":
            typer.echo(f"run ended with status {g.get('status')}", err=True)
            raise typer.Exit(2)
        typer.echo(json.dumps(g, indent=2))
        for rule in g["gate"]["rules"]:
            mark = "PASS" if rule["passed"] else "FAIL"
            actual = "n/a" if rule["actual"] is None else round(rule["actual"], 4)
            typer.echo(f"  {mark}  {rule.get('label') or rule['metric']} {rule['op']} {rule['value']} (actual {actual})")
        if not g["passed"]:
            typer.echo(f"release gate FAILED for {g['model_name']}", err=True)
            raise typer.Exit(1)
        typer.echo(f"release gate passed for {g['model_name']}")


@app.command()
def selftest(
    url: str = typer.Option(os.environ.get("AETHER_URL", "http://localhost:8080"), help="Aether base URL"),
    api_key: str = typer.Option(os.environ.get("AETHER_API_KEY", ""), help="API key, if authentication is enabled"),
    live: bool = typer.Option(False, help="Also test the connection to every configured real model"),
    ui: bool = typer.Option(True, help="Check that the web UI is served (disable for a dev API without a UI build)"),
):
    """Verify every feature of a running installation end to end (uses the offline mock models)."""
    from aether.selftest import SelfTest

    ok = SelfTest(url, api_key or None, live, typer.echo, ui=ui).run()
    raise typer.Exit(0 if ok else 1)


if __name__ == "__main__":
    sys.exit(app())
