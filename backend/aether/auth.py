"""Authentication.

AETHER_AUTH_MODE:
  none     local/dev: every request acts as 'local' in the default workspace
  api_key  requests need 'Authorization: Bearer ak_...' or 'X-API-Key: ak_...'
  oidc     requests need an OIDC access/ID token (Okta, Auth0, Entra ID, Keycloak, ...) verified
           against the issuer's JWKS; API keys are also accepted for CI.
Workspace: 'X-Workspace: <slug>' header, else the token's 'aether_workspace' claim, else 'default'.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aether.config import get_settings
from aether.crypto import hash_token
from aether.db import get_session
from aether.models import ApiKey, Workspace

DEFAULT_WORKSPACE = "default"


@dataclass
class Principal:
    actor: str
    workspace: Workspace
    via: str


def new_api_key() -> tuple[str, str]:
    token = "ak_" + secrets.token_urlsafe(32)
    return token, token[:10]


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)


def verify_oidc(token: str) -> dict:
    s = get_settings()
    if not (s.oidc_issuer and s.oidc_audience):
        raise HTTPException(500, "OIDC is enabled but AETHER_OIDC_ISSUER / AETHER_OIDC_AUDIENCE are not set")
    jwks_url = s.oidc_jwks_url or s.oidc_issuer.rstrip("/") + "/v1/keys"
    try:
        key = _jwks_client(jwks_url).get_signing_key_from_jwt(token).key
        return jwt.decode(
            token, key, algorithms=["RS256", "RS384", "RS512", "ES256", "ES384"], audience=s.oidc_audience, issuer=s.oidc_issuer, leeway=30
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"invalid token: {exc}") from exc


async def _workspace(db: AsyncSession, slug: str) -> Workspace:
    ws = (await db.execute(select(Workspace).where(Workspace.slug == slug))).scalar_one_or_none()
    if ws is None:
        raise HTTPException(404, f"workspace '{slug}' not found")
    return ws


async def get_principal(
    request: Request,
    db: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_workspace: str | None = Header(default=None),
) -> Principal:
    s = get_settings()
    token = x_api_key or (authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else None)

    if token and token.startswith("ak_"):
        key = (await db.execute(select(ApiKey).where(ApiKey.token_hash == hash_token(token), ApiKey.revoked.is_(False)))).scalar_one_or_none()
        if key is None:
            raise HTTPException(401, "invalid or revoked API key")
        key.last_used_at = datetime.now(UTC)
        await db.commit()
        ws = await db.get(Workspace, key.workspace_id)
        return Principal(actor=f"api-key:{key.name}", workspace=ws, via="api_key")

    if s.auth_mode == "none":
        return Principal(actor="local", workspace=await _workspace(db, x_workspace or DEFAULT_WORKSPACE), via="none")

    if s.auth_mode == "oidc" and token:
        claims = verify_oidc(token)
        actor = claims.get("email") or claims.get("preferred_username") or claims.get("sub", "unknown")
        slug = x_workspace or claims.get("aether_workspace") or DEFAULT_WORKSPACE
        return Principal(actor=actor, workspace=await _workspace(db, slug), via="oidc")

    raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})
