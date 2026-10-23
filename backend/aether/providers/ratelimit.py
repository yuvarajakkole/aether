"""Per-model request and token rate limiting (sliding 60 s window).

Limits are enforced per process. With N workers, configure each model's limits as the
share each worker may use, or set AETHER_WORKER_CONCURRENCY low enough that the
provider's own 429 handling (retried with backoff) absorbs bursts.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque


class RateLimiter:
    _registry: dict[str, RateLimiter] = {}

    def __init__(self, rpm: int | None, tpm: int | None):
        self.rpm = rpm
        self.tpm = tpm
        self._requests: deque[float] = deque()
        self._tokens: deque[tuple[float, int]] = deque()
        self._lock = asyncio.Lock()

    @classmethod
    def for_key(cls, key: str, rpm: int | None, tpm: int | None) -> RateLimiter:
        lim = cls._registry.get(key)
        if lim is None or lim.rpm != rpm or lim.tpm != tpm:
            lim = cls(rpm, tpm)
            cls._registry[key] = lim
        return lim

    @classmethod
    def reset(cls) -> None:
        cls._registry.clear()

    def _trim(self, now: float) -> None:
        while self._requests and now - self._requests[0] >= 60:
            self._requests.popleft()
        while self._tokens and now - self._tokens[0][0] >= 60:
            self._tokens.popleft()

    async def acquire(self, est_tokens: int) -> None:
        if not self.rpm and not self.tpm:
            return
        async with self._lock:
            while True:
                now = time.monotonic()
                self._trim(now)
                wait = 0.0
                if self.rpm and len(self._requests) >= self.rpm:
                    wait = max(wait, 60 - (now - self._requests[0]))
                if self.tpm:
                    used = sum(t for _, t in self._tokens)
                    # A single request larger than the whole budget is let through when the window is idle.
                    if used + est_tokens > self.tpm and used > 0:
                        wait = max(wait, 60 - (now - self._tokens[0][0]))
                if wait <= 0:
                    self._requests.append(now)
                    self._tokens.append((now, est_tokens))
                    return
                await asyncio.sleep(min(wait, 5.0))

    def record(self, actual_tokens: int, est_tokens: int) -> None:
        if not self.tpm or not actual_tokens:
            return
        delta = actual_tokens - est_tokens
        if delta:
            self._tokens.append((time.monotonic(), delta))
