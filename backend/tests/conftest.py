import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="aether-test-")
os.environ.setdefault("AETHER_DATABASE_URL", f"sqlite+aiosqlite:///{_tmp}/test.db")
os.environ.setdefault("AETHER_SECRET_KEY", "test-secret")
os.environ.setdefault("AETHER_SEED_SAMPLES", "true")
os.environ.setdefault("AETHER_LOG_JSON", "false")
os.environ.setdefault("AETHER_WORKER_CONCURRENCY", "8")
os.environ.setdefault("AETHER_ENV", "test")
os.environ.pop("AETHER_BOOTSTRAP_FILE", None)

import httpx  # noqa: E402
import pytest  # noqa: E402

from aether.api.app import create_app  # noqa: E402
from aether.providers.ratelimit import RateLimiter  # noqa: E402


@pytest.fixture(scope="session")
async def app():
    application = create_app()
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture(scope="session")
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_limits():
    RateLimiter.reset()
    yield
