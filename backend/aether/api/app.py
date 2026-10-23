from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from aether import __version__
from aether import db as dbm
from aether.api.routes import agents, ci, platform, providers, runs, suites
from aether.bootstrap import run_bootstrap
from aether.config import get_settings
from aether.logging_setup import configure_logging, request_id
from aether.providers.base import close_http_client
from aether.services.resolve import Invalid, NotFound

log = logging.getLogger("aether.api")

HTTP_REQUESTS = Counter("aether_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram("aether_http_request_seconds", "HTTP latency", ["method", "route"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    dbm.init_engine()
    if s.database_url.startswith("sqlite") or getattr(s, "auto_create_schema", False):
        await dbm.create_all()
    async with dbm.sessionmaker()() as db:
        await run_bootstrap(db)
    stop = asyncio.Event()
    worker_task = None
    if s.embedded_worker:
        from aether.worker import Worker

        worker_task = asyncio.create_task(Worker().run_forever(stop))
        log.info("embedded worker enabled")
    log.info("aether api started", extra={"version": __version__, "env": s.env, "auth_mode": s.auth_mode})
    yield
    stop.set()
    if worker_task:
        await worker_task
    await close_http_client()
    await dbm.dispose()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Aether", version=__version__, description="Agent evaluation platform API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Request-ID"]
    )

    @app.middleware("http")
    async def observe(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={"path": request.url.path})
            response = JSONResponse({"detail": "internal server error", "request_id": rid}, status_code=500)
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        HTTP_REQUESTS.labels(request.method, path, response.status_code).inc()
        HTTP_LATENCY.labels(request.method, path).observe(time.perf_counter() - started)
        response.headers["X-Request-ID"] = rid
        request_id.reset(token)
        return response

    @app.exception_handler(NotFound)
    async def _nf(_, exc: NotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(Invalid)
    async def _inv(_, exc: Invalid):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    for r in (platform.router, providers.router, agents.router, suites.router, runs.router, ci.router):
        app.include_router(r)

    static = Path(s.static_dir) if s.static_dir else None
    if static and static.exists():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            f = static / path
            if path and f.is_file():
                return FileResponse(f)
            return FileResponse(static / "index.html")

    return app


app = create_app()
