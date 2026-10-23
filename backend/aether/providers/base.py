from __future__ import annotations

import asyncio
import json
import logging
import random
import ssl
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from aether.config import get_settings
from aether.providers.ratelimit import RateLimiter

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass
class ToolSpec:
    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = field(default_factory=lambda: "call_" + uuid.uuid4().hex[:20])
    raw_arguments: str | None = None
    parse_error: str | None = None


@dataclass
class Message:
    role: str  # system | user | assistant | tool
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ChatResponse:
    content: str | None
    tool_calls: list[ToolCall]
    usage: Usage
    latency_ms: int
    finish_reason: str | None = None
    model: str | None = None
    cost_usd: float = 0.0


@dataclass
class ModelConfig:
    """Everything an adapter needs to call one model. Built from DB rows by the registry."""

    provider_kind: str
    provider_name: str
    model: str
    base_url: str | None = None
    api_key: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    provider_options: dict[str, Any] = field(default_factory=dict)
    model_options: dict[str, Any] = field(default_factory=dict)
    input_cost_per_mtok: float = 0.0
    output_cost_per_mtok: float = 0.0
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    limiter_key: str = ""

    def option(self, key: str, default: Any = None) -> Any:
        if key in self.model_options:
            return self.model_options[key]
        return self.provider_options.get(key, default)


def parse_json_arguments(raw: Any) -> tuple[dict[str, Any], str | None]:
    if raw is None or raw == "":
        return {}, None
    if isinstance(raw, dict):
        return raw, None
    try:
        val = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        return {}, f"arguments are not valid JSON: {exc}"
    if not isinstance(val, dict):
        return {}, "arguments JSON is not an object"
    return val, None


_http: dict[bool, httpx.AsyncClient] = {}


def ssl_context() -> ssl.SSLContext:
    s = get_settings()
    if s.tls_trust == "system":
        import truststore

        ctx: ssl.SSLContext = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    else:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
    if s.ca_bundle:
        import os

        if not os.path.isfile(s.ca_bundle):
            raise ProviderError(f"AETHER_CA_BUNDLE is set to {s.ca_bundle!r}, but that file does not exist")
        ctx.load_verify_locations(cafile=s.ca_bundle)
    return ctx


def http_client(verify: bool = True) -> httpx.AsyncClient:
    client = _http.get(verify)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(get_settings().request_timeout_s, connect=15.0),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
            verify=ssl_context() if verify else False,
            trust_env=True,
        )
        _http[verify] = client
    return client


async def close_http_client() -> None:
    for client in list(_http.values()):
        await client.aclose()
    _http.clear()


def _is_tls_error(exc: Exception) -> bool:
    return "CERTIFICATE_VERIFY_FAILED" in str(exc) or isinstance(exc.__cause__, ssl.SSLError)


class LLMClient(ABC):
    max_attempts = 5
    max_throttle_retries = 12

    def __init__(self, cfg: ModelConfig):
        self.cfg = cfg
        self.limiter = RateLimiter.for_key(cfg.limiter_key or f"{cfg.provider_name}/{cfg.model}", cfg.rpm_limit, cfg.tpm_limit)

    @abstractmethod
    async def _chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        *,
        temperature: float | None,
        max_tokens: int | None,
        json_mode: bool,
        purpose: str | None,
    ) -> tuple[str | None, list[ToolCall], Usage, str | None]:
        """Perform one request. Return (content, tool_calls, usage, finish_reason)."""

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
        purpose: str | None = None,
    ) -> ChatResponse:
        # Quota systems such as Azure OpenAI reserve prompt + max_tokens per request, so estimate the same way.
        prompt_est = sum(len(m.content or "") + sum(len(c.raw_arguments or "") for c in m.tool_calls) for m in messages) // 4
        prompt_est += sum(len(t.description) + len(str(t.parameters)) for t in tools or []) // 4
        est = prompt_est + (max_tokens or 1024)
        attempt = 0
        throttled = 0
        while True:
            attempt += 1
            await self.limiter.acquire(est)
            started = time.perf_counter()
            try:
                content, calls, usage, finish = await self._chat(
                    messages, tools, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode, purpose=purpose
                )
            except ProviderError as exc:
                if exc.status == 429:
                    throttled += 1
                    attempt -= 1  # rate limiting is back-pressure, not failure; it has its own budget
                    if throttled > self.max_throttle_retries:
                        raise
                    await self._backoff(throttled, exc, floor=5.0, cap=60.0)
                    continue
                if not exc.retryable or attempt >= self.max_attempts:
                    raise
                await self._backoff(attempt, exc)
                continue
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if isinstance(exc, (httpx.LocalProtocolError, httpx.UnsupportedProtocol)):
                    raise ProviderError(f"invalid request to {self.cfg.base_url}: {exc}") from exc
                if _is_tls_error(exc):
                    raise ProviderError(
                        f"TLS verification failed for {self.cfg.base_url}: {exc}. Set AETHER_CA_BUNDLE to your CA "
                        "certificate file, or AETHER_TLS_TRUST=system to use the OS trust store."
                    ) from exc
                if attempt >= self.max_attempts:
                    raise ProviderError(f"{type(exc).__name__}: {exc}", retryable=True) from exc
                await self._backoff(attempt, exc)
                continue
            latency = int((time.perf_counter() - started) * 1000)
            self.limiter.record(usage.input_tokens + usage.output_tokens, est)
            return ChatResponse(
                content=content,
                tool_calls=calls,
                usage=usage,
                latency_ms=latency,
                finish_reason=finish,
                model=self.cfg.model,
                cost_usd=self.cost(usage),
            )

    def cost(self, usage: Usage) -> float:
        return (
            usage.input_tokens * self.cfg.input_cost_per_mtok + usage.output_tokens * self.cfg.output_cost_per_mtok
        ) / 1_000_000

    async def _backoff(self, attempt: int, exc: Exception, floor: float = 0.0, cap: float = 30.0) -> None:
        retry_after = getattr(exc, "retry_after", None)
        delay = retry_after if retry_after else min(cap, max(floor, 2 ** (attempt - 1)) + random.random() * max(1.0, floor))
        log.warning(
            "provider call failed, retrying",
            extra={"provider": self.cfg.provider_name, "model": self.cfg.model, "attempt": attempt, "delay_s": delay, "error": str(exc)[:300]},
        )
        await asyncio.sleep(delay)

    # ---- helpers for HTTP adapters ----
    def _auth_headers(self, default_style: str = "bearer") -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        style = self.cfg.provider_options.get("auth_style", default_style)
        key = self.cfg.api_key
        if key:
            if style == "bearer":
                headers["Authorization"] = f"Bearer {key}"
            elif style == "api-key":
                headers["api-key"] = key
            elif style == "x-api-key":
                headers["x-api-key"] = key
            elif style == "basic":
                headers["Authorization"] = f"Basic {key}"
        headers.update(self.cfg.headers or {})
        return headers

    async def _post(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        params = self.cfg.provider_options.get("query") or None
        verify = bool(self.cfg.provider_options.get("verify_ssl", True))
        resp = await http_client(verify).post(url, json=body, headers=headers, params=params)
        if resp.status_code >= 400:
            text = resp.text[:800]
            err = ProviderError(
                f"{self.cfg.provider_name} returned HTTP {resp.status_code}: {text}",
                status=resp.status_code,
                retryable=resp.status_code in RETRYABLE_STATUS,
            )
            ra = resp.headers.get("retry-after")
            if ra:
                try:
                    err.retry_after = min(float(ra), 60.0)  # type: ignore[attr-defined]
                except ValueError:
                    pass
            raise err
        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(f"{self.cfg.provider_name} returned non-JSON body: {resp.text[:300]}") from exc
