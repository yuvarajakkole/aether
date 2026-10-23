import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from aether import auth
from aether import db as dbm
from aether.bootstrap import ensure_bootstrap_key, ensure_workspace
from aether.config import get_settings

ISSUER = "https://idp.example.com/oauth2/default"
AUDIENCE = "api://aether"


@pytest.fixture
def oidc(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class FakeJWKS:
        def get_signing_key_from_jwt(self, token):
            return type("K", (), {"key": key.public_key()})()

    s = get_settings()
    monkeypatch.setattr(s, "auth_mode", "oidc")
    monkeypatch.setattr(s, "oidc_issuer", ISSUER)
    monkeypatch.setattr(s, "oidc_audience", AUDIENCE)
    monkeypatch.setattr(auth, "_jwks_client", lambda url: FakeJWKS())

    def make(**claims):
        base = {"iss": ISSUER, "aud": AUDIENCE, "sub": "00u1", "email": "dev@example.com", "exp": int(time.time()) + 300}
        return jwt.encode({**base, **claims}, key, algorithm="RS256")

    return make


async def test_oidc_tokens_are_verified(client, oidc):
    ok = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {oidc()}"})
    assert ok.status_code == 200 and ok.json()["actor"] == "dev@example.com" and ok.json()["via"] == "oidc"
    assert (await client.get("/api/v1/me")).status_code == 401
    assert (await client.get("/api/v1/me", headers={"Authorization": f"Bearer {oidc(aud='someone-else')}"})).status_code == 401
    assert (await client.get("/api/v1/me", headers={"Authorization": f"Bearer {oidc(exp=int(time.time()) - 600)}"})).status_code == 401
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": int(time.time()) + 300}, other, algorithm="RS256")
    assert (await client.get("/api/v1/me", headers={"Authorization": f"Bearer {forged}"})).status_code == 401
    assert (await client.get("/healthz")).status_code == 200, "health checks stay public"


async def test_api_key_mode_and_bootstrap_key(client, monkeypatch):
    token = "ak_bootstrap_test_0123456789abcdef"
    async with dbm.sessionmaker()() as db:
        ws = await ensure_workspace(db)
        await ensure_bootstrap_key(db, ws, token)
        await ensure_bootstrap_key(db, ws, token)  # idempotent
        await db.commit()
        with pytest.raises(ValueError):
            await ensure_bootstrap_key(db, ws, "not-a-valid-key")
    monkeypatch.setattr(get_settings(), "auth_mode", "api_key")
    assert (await client.get("/api/v1/agents")).status_code == 401
    r = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["actor"] == "api-key:bootstrap-admin"
    assert (await client.get("/api/v1/agents", headers={"X-API-Key": token})).status_code == 200
    assert (await client.get("/api/v1/meta")).status_code == 200, "metadata stays public so the UI can show sign-in options"
