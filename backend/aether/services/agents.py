"""Agent helpers: endpoint secrets are encrypted at rest and never returned by the API."""

from __future__ import annotations

import json
import re
from typing import Any

from aether.crypto import decrypt, encrypt

SECRET_FIELDS = ("api_key", "headers")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "agent"


def seal_endpoint(endpoint: dict[str, Any] | None, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    endpoint = dict(endpoint or {})
    secrets = open_secrets(previous or {})
    for f in SECRET_FIELDS:
        if f in endpoint:
            val = endpoint.pop(f)
            if val in (None, "", {}):
                secrets.pop(f, None)
            else:
                secrets[f] = val
    if secrets:
        endpoint["secrets_enc"] = encrypt(json.dumps(secrets))
        endpoint["has_api_key"] = "api_key" in secrets
        endpoint["header_names"] = sorted((secrets.get("headers") or {}).keys())
    else:
        endpoint.pop("secrets_enc", None)
        endpoint["has_api_key"] = False
        endpoint["header_names"] = []
    return endpoint


def open_secrets(endpoint: dict[str, Any]) -> dict[str, Any]:
    raw = endpoint.get("secrets_enc")
    if not raw:
        return {}
    return json.loads(decrypt(raw) or "{}")


def runtime_endpoint(endpoint: dict[str, Any]) -> dict[str, Any]:
    """Endpoint dict with secrets restored, for the engine."""
    ep = {k: v for k, v in (endpoint or {}).items() if k not in ("secrets_enc", "has_api_key", "header_names")}
    ep.update(open_secrets(endpoint or {}))
    return ep


def public_endpoint(endpoint: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in (endpoint or {}).items() if k != "secrets_enc"}
